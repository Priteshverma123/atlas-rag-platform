"""Simple, plain-language flow + user-flow diagrams → docs/diagrams/atlas-flows.drawio

These complement the detailed technical diagrams (atlas.drawio): one idea per page, short labels,
readable by non-engineers. Run: python build_flows.py
"""

from pathlib import Path

from _drawio import Document

d = Document()

d.page("1. Question → answer").flow(
    "What a question looks like end to end",
    [("ask", "Ask a question", "chat, API or MCP", 0, 1, "actor"),
     ("mem", "Recall context", "recent turns + user facts", 1, 1),
     ("plan", "Understand question", "rewrite, pick strategy", 2, 1),
     ("find", "Find evidence", "keyword + meaning search", 3, 1),
     ("enough", "Enough evidence?", "", 4, 1, "ask"),
     ("none", "Say \"not found\"", "no LLM call, no cost", 4, 2.6, "end"),
     ("write", "Write answer", "every fact cites a source", 5, 0),
     ("check", "Check citations", "flag weak claims", 5, 1.3),
     ("show", "Show answer", "click a source → PDF page", 6, 1.3, "good")],
    [("ask", "mem"), ("mem", "plan"), ("plan", "find"), ("find", "enough"), ("enough", "none", "no"),
     ("enough", "write", "yes"), ("write", "check"), ("check", "show")],
    note="Simple questions skip the 'understand' LLM call entirely, the most common case is also the fastest.")

d.page("2. User flow: add documents").flow(
    "Adding knowledge: from file to searchable",
    [("user", "Create collection", "e.g. HR handbook", 0, 1, "actor"),
     ("up", "Upload", "file, web page or text", 1, 1),
     ("same", "Seen before?", "", 2, 1, "ask"),
     ("skip", "Nothing to do", "unchanged = free", 2, 2.6, "good"),
     ("proc", "Processing", "read → split → index", 3, 1),
     ("ok", "Worked?", "", 4, 1, "ask"),
     ("fail", "Show error", "retry or re-process", 4, 2.6, "end"),
     ("ready", "Ready to search", "usually seconds", 5, 1, "good"),
     ("enrich", "Improve in background", "add context + summary", 6, 1)],
    [("user", "up"), ("up", "same"), ("same", "skip", "yes"), ("same", "proc", "no"), ("proc", "ok"),
     ("ok", "fail", "no"), ("ok", "ready", "yes"), ("ready", "enrich")],
    note="Updating a document only re-processes the parts that changed.")

d.page("3. User flow: chat").flow(
    "Chatting with your documents",
    [("open", "Open chat", "pick collections", 0, 1, "actor"),
     ("ask", "Ask", "", 1, 1),
     ("stream", "Answer streams in", "with numbered sources", 2, 1),
     ("cite", "Click a source", "PDF opens at the passage", 3, 0),
     ("follow", "Ask a follow-up", "\"and for Tokyo?\"", 3, 1),
     ("rate", "Rate the answer", "👍 / 👎 / correction", 3, 2),
     ("memory", "Remembers context", "earlier turns + preferences", 4, 1),
     ("evalset", "Improves test set", "bad answers become tests", 4, 2, "good")],
    [("open", "ask"), ("ask", "stream"), ("stream", "cite"), ("stream", "follow"), ("stream", "rate"),
     ("follow", "memory"), ("memory", "stream", "uses"), ("rate", "evalset")])

d.page("4. User flow: deep research").flow(
    "Deep research: a report instead of an answer",
    [("q", "Ask a big question", "+ effort: low / med / high", 0, 1, "actor"),
     ("plan", "Plan", "outline + sub-questions", 1, 1),
     ("r", "Research in parallel", "one helper per sub-question", 2, 1),
     ("w", "Write report", "only from found evidence", 3, 1),
     ("rev", "Review", "", 4, 1, "ask"),
     ("done", "Report", "sections + citations", 5, 1, "good"),
     ("part", "Partial report", "budget reached, clearly marked", 3, 2.4, "end")],
    [("q", "plan"), ("plan", "r"), ("r", "w"), ("w", "rev"), ("rev", "done", "approved"),
     ("rev", "plan", "gaps found"), ("r", "part", "over budget")],
    note="Progress streams live; a crashed job resumes where it stopped.")

d.page("5. One LLM call (gateway)").flow(
    "What happens to every LLM call",
    [("app", "App asks for an answer", "route: fast / answer / deep", 0, 1, "actor"),
     ("allowed", "Within limits?", "", 1, 1, "ask"),
     ("deny", "Politely refused", "retry later / budget hit", 1, 2.6, "end"),
     ("cache", "Already answered?", "", 2, 1, "ask"),
     ("hit", "Return saved answer", "instant, free", 2, 2.6, "good"),
     ("best", "Ask best provider", "e.g. Claude", 3, 1),
     ("worked", "Worked?", "", 4, 1, "ask"),
     ("next", "Try next provider", "OpenAI, Gemini, local…", 4, 2.6),
     ("ans", "Answer", "", 5, 1, "good"),
     ("log", "Record cost", "in the background", 6, 1)],
    [("app", "allowed"), ("allowed", "deny", "no"), ("allowed", "cache", "yes"), ("cache", "hit", "yes"),
     ("cache", "best", "no"), ("best", "worked"), ("worked", "ans", "yes"), ("worked", "next", "no"),
     ("next", "ans"), ("ans", "log")])

d.page("6. Memory").flow(
    "How Atlas remembers",
    [("turn", "Each message", "", 0, 1, "actor"),
     ("recent", "Recent turns", "kept word for word", 1, 0),
     ("sum", "Older turns", "folded into a summary", 2, 0),
     ("facts", "Facts about you", "\"works in EU finance\"", 1, 2),
     ("merge", "Nightly tidy-up", "merge duplicates, retire outdated", 2, 2),
     ("use", "Next answer", "uses summary + relevant facts", 3, 1, "good"),
     ("ctl", "You stay in control", "view or delete any memory", 4, 2, "end")],
    [("turn", "recent"), ("recent", "sum", "too long"), ("turn", "facts", "in background"),
     ("facts", "merge"), ("sum", "use"), ("merge", "use"), ("merge", "ctl")])

d.page("7. Admin flow").flow(
    "Setting up a team",
    [("boot", "Create workspace", "one-time setup token", 0, 1, "actor"),
     ("keys", "Create API keys", "read / write / admin", 1, 1),
     ("budget", "Set budgets", "per key per day, per team per month", 2, 1),
     ("access", "Share documents", "per group access", 3, 1),
     ("connect", "Connect apps", "REST, OpenAI SDK, MCP", 4, 1),
     ("watch", "Watch usage", "cost, savings, latency", 5, 1, "good")],
    [("boot", "keys"), ("keys", "budget"), ("budget", "access"), ("access", "connect"), ("connect", "watch")])

print(f"wrote {d.save(Path(__file__).with_name('atlas-flows.drawio'))}")
