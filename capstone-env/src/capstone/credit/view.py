"""The agent view: exactly what the credit agent may see of a family turn.

A family turn (``family.json``) mixes what the agent reads with grader-only data: checkpoints,
expected answers, programs, record changes, synthetic flags, edited cells, claimed values, the
version type. ``agent_view`` keeps only the speaker, the text and the documents' own fields, and
``render_message`` turns that into the one message the agent receives. Nothing else from a family
reaches the model.
"""

from __future__ import annotations

from typing import Sequence

AGENT_TURN_FIELDS = ("speaker", "text", "documents")
AGENT_DOC_FIELDS = ("doc_id", "doc_type", "title", "issued_by", "supersedes", "signed_by", "dated",
                    "table", "body")
# Fields of a family turn or document that must never reach the agent (checked by the tests).
GRADER_ONLY_FIELDS = ("kind", "turn", "checkpoint", "expected_answer", "program", "record_change",
                      "reactions", "claimed_value", "evidence_attached", "synthetic", "edited_cells")
TABLE_MARKER = "[schedule table]"


def agent_view(turn: dict) -> dict:
    """Speaker, text and documents (own fields only) of one family turn."""
    return {"speaker": turn["speaker"], "text": turn["text"],
            "documents": [{k: d[k] for k in AGENT_DOC_FIELDS if k in d} for d in turn.get("documents", [])]}


def render_table(table: Sequence[Sequence[str]]) -> str:
    if not table:
        return ""
    head = [c or " " for c in table[0]]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(c or "(blank)" for c in row) + " |" for row in table[1:]]
    return "\n".join(lines)


def speaker_label(speaker: str) -> str:
    return speaker.replace("_", " ").capitalize()


def render_document(doc: dict) -> str:
    meta = [doc.get("doc_type", "").replace("_", " ")]
    meta += [f"{label} {doc[k]}" for k, label in (("issued_by", "issued by"), ("signed_by", "signed by"),
                                                   ("dated", "dated"), ("supersedes", "supersedes")) if doc.get(k)]
    head = f"[{doc['doc_id']}] {doc.get('title', '')} ({', '.join(m for m in meta if m)})"
    table, body = render_table(doc.get("table") or []), doc.get("body") or ""
    if table and TABLE_MARKER in body:
        content = body.replace(TABLE_MARKER, "\n" + table + "\n")
    else:
        content = "\n\n".join(part for part in (table, body) if part)
    return f"{head}\n{content}".rstrip()


def render_message(view: dict) -> str:
    """The message the agent receives for one turn."""
    text = f"{speaker_label(view['speaker'])}: {view['text']}"
    docs = [render_document(d) for d in view["documents"]]
    if docs:
        text += "\n\nAttached documents:\n\n" + "\n\n".join(docs)
    return text
