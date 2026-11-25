"""Declarative gateway config (``config/gateway.yaml``).

Strategy trees are Portkey-style (nested ``fallback`` / ``loadbalance`` / ``latency`` nodes);
retry policies and cooldowns are LiteLLM-style (per error class, ``allowed_fails`` in a window).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from atlas.common.logging import get_logger

log = get_logger(__name__)
_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class StrategyNode(BaseModel):
    target: str | None = None  # "provider/model" leaf
    mode: Literal["fallback", "loadbalance", "latency"] | None = None
    targets: list[StrategyNode] = Field(default_factory=list)
    weight: float = 1.0
    params: dict[str, Any] = Field(default_factory=dict)  # e.g. effort, max_tokens cap

    @model_validator(mode="after")
    def _shape(self) -> StrategyNode:
        if bool(self.target) == bool(self.mode):
            raise ValueError("a strategy node needs exactly one of 'target' or 'mode'+'targets'")
        if self.mode and not self.targets:
            raise ValueError(f"'{self.mode}' node has no targets")
        return self

    def leaves(self) -> list[StrategyNode]:
        return [self] if self.target else [leaf for t in self.targets for leaf in t.leaves()]


class RetryPolicy(BaseModel):
    rate_limit: int = 2
    overloaded: int = 2
    timeout: int = 1
    connection: int = 2
    unknown: int = 1
    base_delay_s: float = 0.25
    max_delay_s: float = 8.0
    max_retry_after_s: float = 10.0  # don't sleep longer than this on a retry-after; fail over instead

    def attempts_for(self, kind: str) -> int:
        return int(getattr(self, kind, 0))


class CooldownPolicy(BaseModel):
    allowed_fails: int = 3
    window_s: int = 60
    cooldown_s: int = 30


class Timeouts(BaseModel):
    total_s: float = 60.0
    ttft_s: float = 10.0


class CachePolicy(BaseModel):
    exact: bool = False
    exact_ttl_s: int = 3600
    semantic: bool = False
    semantic_threshold: float = 0.95
    semantic_ttl_s: int = 3600
    semantic_max_messages: int = 4


class RouteConfig(BaseModel):
    strategy: StrategyNode
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    cooldown: CooldownPolicy = Field(default_factory=CooldownPolicy)
    timeouts: Timeouts = Field(default_factory=Timeouts)
    cache: CachePolicy = Field(default_factory=CachePolicy)
    context_window_fallbacks: list[str] = Field(default_factory=list)
    content_policy_fallbacks: list[str] = Field(default_factory=list)
    default_max_tokens: int = 1024
    prompt_cache: bool = True
    downgrade_route: str | None = None  # used when a budget's soft limit is reached


class ProviderConfig(BaseModel):
    type: Literal["openai_compat", "anthropic", "mock"]
    base_url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    max_tokens_param: str = "max_tokens"
    supports_temperature: bool = True
    server_fallbacks: bool = False

    def resolved_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env) or None
        return None

    @property
    def enabled(self) -> bool:
        if self.type == "mock" or self.api_key == "mock":
            return True
        # Safety switch: real (paid) providers are only used when ATLAS_LLM_LIVE=true, even if keys
        # are present in the environment. Default = deterministic mock LLM, zero spend.
        if os.environ.get("ATLAS_LLM_LIVE", "false").lower() != "true" and self.api_key_env != "ATLAS_OLLAMA_ENABLED":
            return False
        return self.resolved_key() is not None


class GatewayConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    routes: dict[str, RouteConfig]
    # Used when every target of a route is unavailable (e.g. no API keys in a demo).
    fallback_when_unavailable: str | None = None
    defaults: RouteConfig | None = None

    def enabled_providers(self) -> set[str]:
        return {n for n, p in self.providers.items() if p.enabled}

    def prune(self) -> GatewayConfig:
        """Drop targets whose provider has no credentials so routing never wastes attempts."""
        enabled = self.enabled_providers()

        def keep(node: StrategyNode) -> StrategyNode | None:
            if node.target:
                return node if node.target.split("/", 1)[0] in enabled else None
            kids = [k for k in (keep(t) for t in node.targets) if k]
            return node.model_copy(update={"targets": kids}) if kids else None

        routes: dict[str, RouteConfig] = {}
        for name, r in self.routes.items():
            pruned = keep(r.strategy)
            if pruned is None:
                if not self.fallback_when_unavailable:
                    log.warning("route_disabled", route=name)
                    continue
                log.warning("route_using_fallback", route=name, fallback=self.fallback_when_unavailable)
                pruned = StrategyNode(target=self.fallback_when_unavailable)
            routes[name] = r.model_copy(update={
                "strategy": pruned,
                "context_window_fallbacks": [t for t in r.context_window_fallbacks if t.split("/")[0] in enabled],
                "content_policy_fallbacks": [t for t in r.content_policy_fallbacks if t.split("/")[0] in enabled],
            })
        return self.model_copy(update={"routes": routes})

    def route_for(self, model: str) -> tuple[str, RouteConfig]:
        """`model` may be a route name or an explicit 'provider/model' (ad-hoc single-target route)."""
        if model in self.routes:
            return model, self.routes[model]
        if "/" in model and model.split("/", 1)[0] in self.enabled_providers():
            base = self.defaults or RouteConfig(strategy=StrategyNode(target=model))
            return f"direct:{model}", base.model_copy(update={"strategy": StrategyNode(target=model)})
        raise KeyError(model)


def _expand_env(obj: Any) -> Any:
    if isinstance(obj, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), obj)
    if isinstance(obj, list):
        return [_expand_env(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    return obj


def load_config(path: Path) -> GatewayConfig:
    raw = _expand_env(yaml.safe_load(path.read_text(encoding="utf-8")))
    return GatewayConfig.model_validate(raw).prune()
