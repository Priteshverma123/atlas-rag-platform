"""Builds provider adapter instances from config."""

from __future__ import annotations

from atlas.gateway.config import GatewayConfig
from atlas.gateway.providers.anthropic import AnthropicProvider
from atlas.gateway.providers.base import Provider
from atlas.gateway.providers.mock import MockProvider
from atlas.gateway.providers.openai_compat import OpenAICompatProvider


def build_providers(cfg: GatewayConfig) -> dict[str, Provider]:
    out: dict[str, Provider] = {}
    for name, p in cfg.providers.items():
        if not p.enabled:
            continue
        key = p.resolved_key() or "not-needed"
        if p.type == "anthropic":
            out[name] = AnthropicProvider(name, key, server_fallbacks=p.server_fallbacks)
        elif p.type == "openai_compat":
            if not p.base_url:
                raise ValueError(f"provider {name}: base_url required")
            out[name] = OpenAICompatProvider(name, p.base_url, key, max_tokens_param=p.max_tokens_param,
                                             supports_temperature=p.supports_temperature)
        else:
            out[name] = MockProvider(name)
    return out
