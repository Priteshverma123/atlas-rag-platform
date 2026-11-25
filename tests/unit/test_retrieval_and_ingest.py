from __future__ import annotations

from atlas.embeddings.sparse import BM25Encoder
from atlas.evals import metrics as M
from atlas.generation.citations import cited_numbers, verify
from atlas.ingestion.chunking import ChunkParams, chunk_document
from atlas.ingestion.parsers.base import Block, ParsedDocument
from atlas.ingestion.parsers.registry import DOCX, MARKDOWN, PDF, TEXT, sniff_mime
from atlas.ingestion.parsers.text import parse_markdown
from atlas.mockllm.logic import extractive_answer, instantiate
from atlas.retrieval.context import Source, build_context
from atlas.retrieval.fusion import Fused, rrf
from atlas.retrieval.planner import Intent, heuristic_plan
from atlas.retrieval.rerank import confident


# ---------------------------------------------------------------- BM25
def test_bm25_keeps_identifiers_verbatim_and_stems_words() -> None:
    enc = BM25Encoder()
    toks = enc.tokens("Error E-1042 occurred while running inspections")
    assert "e-1042" in toks
    assert "run" in toks and "inspect" in toks
    assert "occurred" not in toks  # stemmed to "occur"


def test_bm25_query_matches_document_terms() -> None:
    enc = BM25Encoder()
    d = enc.encode_document("Compass interference detected: error E-1042")
    q = enc.encode_query("what is E-1042")
    assert set(q.indices) & set(d.indices)
    assert all(v > 0 for v in d.values)


def test_bm25_tf_saturates() -> None:
    enc = BM25Encoder()
    one = dict(zip(*[enc.encode_document("drone").indices, enc.encode_document("drone").values], strict=True))
    many = enc.encode_document("drone " * 50)
    assert max(many.values) < (enc.k1 + 1) and max(many.values) > max(one.values())


# ---------------------------------------------------------------- fusion / rerank skip
def test_rrf_rewards_agreement() -> None:
    fused = rrf({"dense": [("a", 0.9, {}), ("b", 0.8, {})], "sparse": [("b", 12.0, {}), ("c", 3.0, {})]})
    assert fused[0].id == "b"
    assert fused[0].ranks == {"dense": 2, "sparse": 1}


def test_rerank_skipped_only_when_both_retrievers_agree() -> None:
    agree = rrf({"dense": [("a", 1, {})], "sparse": [("a", 1, {})]}) + [Fused("z", 0.0, {})]
    assert confident(agree)
    disagree = rrf({"dense": [("a", 1, {}), ("b", 1, {})], "sparse": [("b", 1, {}), ("a", 1, {})]})
    assert not confident(disagree)


# ---------------------------------------------------------------- planner heuristics
def test_heuristic_plan_skips_llm_for_simple_first_turn() -> None:
    p = heuristic_plan("What is the meal allowance in London?", has_history=False)
    assert p is not None and p.intent is Intent.LOOKUP
    assert heuristic_plan("compare Q2 and Q3 revenue", has_history=False) is None
    assert heuristic_plan("and what about Tokyo?", has_history=True) is None
    chat = heuristic_plan("thanks!", has_history=True)
    assert chat is not None and chat.intent is Intent.CHITCHAT


# ---------------------------------------------------------------- parsing / chunking
def test_sniff_mime_trusts_magic_bytes() -> None:
    assert sniff_mime(b"%PDF-1.7 ...", "x.txt") == PDF
    assert sniff_mime(b"# Title\nhello", "notes.md") == MARKDOWN
    assert sniff_mime(b"plain text", "a.txt") == TEXT
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w/>")
    assert sniff_mime(buf.getvalue(), "x.pdf") == DOCX


def test_markdown_parser_blocks() -> None:
    doc = parse_markdown("# T\n\nPara one.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## Sub\n- x\n- y\n")
    kinds = [b.kind for b in doc.blocks]
    assert kinds == ["heading", "paragraph", "table", "heading", "list"]


def _doc() -> ParsedDocument:
    para = "The drone flies for 42 minutes. " * 30
    return ParsedDocument("Manual", [
        Block("heading", "Manual", level=1, page=1),
        Block("paragraph", para, page=1, bbox=(0, 0, 10, 10)),
        Block("heading", "Errors", level=2, page=2),
        Block("table", "| Code | Meaning |\n|---|---|\n| E-1 | x |", page=2),
        Block("paragraph", "Short note.", page=2),
        Block("paragraph", "Another paragraph about errors and fixes that is long enough. " * 3, page=2),
    ])


def test_chunker_respects_sections_tables_and_parents() -> None:
    chunks = chunk_document(_doc(), ChunkParams(target_tokens=120, max_tokens=200, overlap_tokens=20,
                                                min_tokens=10, parent_tokens=1000))
    leaves = [c for c in chunks if c.kind != "parent"]
    parents = [c for c in chunks if c.kind == "parent"]
    assert any(c.kind == "table" for c in leaves)
    table = next(c for c in leaves if c.kind == "table")
    assert table.section_path == "Manual > Errors" and table.page_start == 2
    assert all(c.token_count <= 220 for c in leaves)
    assert parents, "consecutive chunks in one top-level section form a parent"
    assert any(c.parent_ordinal is not None for c in leaves)
    assert len({c.text_hash for c in leaves}) == len(leaves)
    assert leaves[0].bboxes and leaves[0].bboxes[0]["page"] == 1


def test_chunk_embed_text_includes_title_section_and_context() -> None:
    c = chunk_document(_doc(), ChunkParams())[0]
    t = c.embed_text("Manual", "Context line")
    assert t.startswith("Manual") and "Context line" in t


# ---------------------------------------------------------------- context + citations
def _src(n: int, text: str, doc: str = "d1", section: str = "s") -> Source:
    return Source(n=n, chunk_ids=[f"c{n}"], document_id=doc, title="T", text=text, section_path=section,
                  page_start=1, page_end=1, score=1.0)


def test_build_context_dedupes_and_respects_budget() -> None:
    items = [Fused(str(i), 1.0 / (i + 1), {"text": f"unique text {i} " * 50, "text_hash": f"h{i}",
                                           "document_id": f"d{i}", "section_path": "x"}) for i in range(10)]
    items.append(Fused("dup", 0.01, {"text": "unique text 0 " * 50, "text_hash": "h0", "document_id": "d0"}))
    srcs = build_context(items, budget_tokens=500, max_sources=8)
    assert 1 <= len(srcs) < 10
    assert len({s.document_id for s in srcs}) == len(srcs)


def test_verify_flags_weak_and_invalid_citations() -> None:
    sources = [_src(1, "The KD-200 has a 42 minute maximum flight time and an IP55 rating.")]
    answer = ("The KD-200 flies for 42 minutes maximum flight time [1]. "
              "It was designed by aliens from Mars in 1950 [1]. Pricing is secret [7].")
    v = verify(answer, sources)
    assert v.cited == 3 and v.supported == 1
    assert 7 in v.invalid_markers
    assert cited_numbers(answer) == [1, 7]


# ---------------------------------------------------------------- mock llm
def test_mock_extractive_answer_cites_sources() -> None:
    msgs = [{"role": "user", "content": '<sources>\n<source id="1" title="M">The KD-200 has a 42 minute '
                                        'maximum flight time.</source>\n</sources>\n<question>How long can the '
                                        'KD-200 fly?</question>'}]
    assert "[1]" in extractive_answer(msgs)


def test_mock_instantiates_schema_with_question_hint() -> None:
    schema = {"type": "object", "properties": {"intent": {"type": "string", "enum": ["lookup", "global"]},
                                               "standalone_query": {"type": "string"},
                                               "sub_queries": {"type": "array", "items": {"type": "string"}}}}
    assert instantiate(schema, "q?") == {"intent": "lookup", "standalone_query": "q?", "sub_queries": []}


# ---------------------------------------------------------------- eval metrics
def test_retrieval_metrics() -> None:
    chunks = ["nothing here", "the answer is 42 minute maximum flight time", "noise"]
    ev = ["42 minute maximum flight time"]
    assert M.hit_at_k(chunks, ev, 1) == 0.0 and M.hit_at_k(chunks, ev, 2) == 1.0
    assert M.mrr(chunks, ev) == 0.5
    assert 0 < M.ndcg_at_k(chunks, ev, 10) < 1
    assert M.recall_at_k(chunks, ev + ["missing fact"], 3) == 0.5
    assert M.token_f1("the cat sat", "the cat sat") == 1.0
