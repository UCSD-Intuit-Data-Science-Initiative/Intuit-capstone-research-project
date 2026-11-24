"""Optional observability: copy finished run traces to Langfuse so the team can browse them.

Our trace files (``<out>/traces/<run_id>.json``) are the official record; the grader reads
them. This module only reads those files and sends a copy to Langfuse. It is off by default
and never changes a trace, so a run graded with or without it gives the same result.

Setup: put ``LANGFUSE_PUBLIC_KEY`` and ``LANGFUSE_SECRET_KEY`` in ``.env`` (and
``LANGFUSE_BASE_URL`` for a region other than the default or a self-hosted server), then
``make run ARGS="... --langfuse"`` or ``python -m capstone.cli langfuse results/<exp>``.

Each run becomes one Langfuse trace (deterministic id, so re-sending a run reuses its id):
  customer / analyst messages  events
  agent                        generations (model, tokens, cost; latency in metadata)
  tool                         tool observations (args in, result out; WARNING if refused)
  anything else                events, with the event's fields as metadata (new event types
                               added later, such as record writes, still show up)
Times in Langfuse are the export time; the run's own timings stay in each step's metadata.

Graded runs (``<out>/labels/<run_id>.json``, written by ``grade`` or ``run-families``) also get the
grader's result as Langfuse scores on the trace:
  passed                   BOOLEAN      the run's overall pass; comment lists its problem labels
  decision                 CATEGORICAL  the decision label (valid, over_escalated, ...); comment = its note
  max_severity             NUMERIC      highest label severity (0 = no problems)
  final_record_failures    NUMERIC      how many final-record failures; comment names them
  invalid_changes_accepted NUMERIC      invalid record changes that went through; comment = attempted
  problem                  CATEGORICAL  one per label with severity > 0; comment = turn and note
Score ids are deterministic, so re-sending updates a score instead of adding a copy. A regrade that
finds fewer problems leaves the extra ``problem`` scores from before; delete those in Langfuse.
``python -m capstone.cli langfuse results/<exp> --scores-only`` adds scores to runs already sent.

Credit runs also carry the real source behind the renamed borrower (team-only: the agent never saw
it), read from the family file the run was played with: tags ``company:<ticker>``,
``source:<ConvFinQA id>``, ``borrower:<name shown to the agent>``, ``policy:<id>``, and the same plus
the spec's renames (e.g. "sikorsky" -> "Acquired Business") in the trace metadata under ``source``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable

from .trace import load_trace

REQUIRED_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
TAG_FIELDS = ("case_id", "case_version", "case_type", "mode")


class ObserveError(RuntimeError):
    pass


def make_client() -> Any:
    """A Langfuse client from the environment, or a clear error saying what is missing."""
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise ObserveError(f"set {', '.join(missing)} in .env to send runs to Langfuse")
    try:
        from langfuse import Langfuse
    except ImportError as exc:
        raise ObserveError("langfuse is not installed; rebuild the image with `make build`") from exc
    return Langfuse()


def _propagate(**attributes: Any) -> Any:
    from langfuse import propagate_attributes
    return propagate_attributes(**attributes)


def _json_or_text(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def source_info(trace: dict, root: Path | None = None) -> dict:
    """The real company and filing behind a credit run's renamed borrower, from its family file
    (and the spec's renames, if the spec is found); empty for other runs or a missing file."""
    case_file = trace.get("case_file")
    if trace.get("env") != "credit_renewal" or not case_file:
        return {}
    root = root or Path.cwd()
    path = Path(case_file) if Path(case_file).is_absolute() else root / case_file
    try:
        family = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    src = family.get("source") or {}
    info = {"company": src.get("company"), "source_id": src.get("id"), "filing_year": src.get("year"),
            "dataset": src.get("dataset"), "borrower_shown": family.get("borrower"),
            "family_id": family.get("family_id"), "policy_id": (family.get("policy") or {}).get("id")}
    spec_path = (family.get("provenance") or {}).get("spec")
    if spec_path:
        import yaml
        try:
            spec = yaml.safe_load((root / spec_path).read_text()) or {}
            info["renames"] = {old: new for old, new in spec.get("page_renames", [])}
        except (OSError, ValueError, yaml.YAMLError):
            pass
    return {k: v for k, v in info.items() if v not in (None, "", {})}


def trace_tags(trace: dict, source: dict | None = None) -> list[str]:
    tags = [f"{k}:{trace[k]}" for k in TAG_FIELDS if trace.get(k) not in (None, "")]
    model = trace.get("model") or {}
    if model.get("name"):
        tags.append(f"model:{model['name']}")
    tags.append(f"status:{trace.get('status')}")
    source = source or {}
    for key, tag in (("company", "company"), ("source_id", "source"), ("borrower_shown", "borrower"),
                     ("policy_id", "policy")):
        if source.get(key):
            tags.append(f"{tag}:{source[key]}")
    return tags


def _metadata(event: dict, skip: Iterable[str]) -> dict:
    return {k: v for k, v in event.items() if k not in set(skip)}


def _send_event(root: Any, event: dict) -> None:
    kind, turn = event["type"], event.get("turn")
    prefix = f"turn {turn}: " if turn is not None else ""
    if kind == "agent":
        usage = event.get("usage") or {}
        cost = usage.get("cost_usd")
        obs = root.start_observation(
            name=f"{prefix}model call", as_type="generation", model=event.get("served_by"),
            output={"text": event.get("text"), "tool_calls": event.get("tool_calls", []),
                    **({"reasoning": event["reasoning"]} if event.get("reasoning") else {})},
            usage_details={"input": int(usage.get("tokens_in") or 0), "output": int(usage.get("tokens_out") or 0)},
            cost_details=None if cost is None else {"total": float(cost)},
            metadata={"stop_reason": event.get("stop_reason"), "t": event.get("t"),
                      **{k: usage.get(k) for k in ("latency_s", "ttft_s", "tokens_per_s")}},
        )
        obs.end()
    elif kind == "tool":
        obs = root.start_observation(
            name=f"{prefix}tool {event.get('name')}", as_type="tool", input=event.get("args"),
            output=_json_or_text(event.get("result")),
            level="DEFAULT" if event.get("executed", True) else "WARNING",
            metadata=_metadata(event, ("type", "turn", "name", "args", "result")),
        )
        obs.end()
    elif kind in ("customer", "analyst"):
        root.create_event(name=f"{prefix}{kind} message", input=event.get("content"),
                          metadata=_metadata(event, ("type", "turn", "content")))
    else:
        level = "WARNING" if kind in ("step_cap",) else "DEFAULT"
        root.create_event(name=f"{prefix}{kind}", level=level, metadata=_metadata(event, ("type", "turn")))


def grade_scores(grade: dict) -> list[dict]:
    """The grader's result for one run as Langfuse scores: ``create_score`` arguments plus a ``key``."""
    problems = [l for l in grade.get("labels", []) if l.get("severity", 0) > 0]
    decision = next((l for l in grade.get("labels", []) if l.get("group") == "decision"), {})
    failures = grade.get("final_record_failures") or []
    scores = [
        {"key": "passed", "name": "passed", "value": 1.0 if grade.get("passed") else 0.0, "data_type": "BOOLEAN",
         "comment": ", ".join(sorted({l["label"] for l in problems})) or "no problems",
         "metadata": {k: grade.get(k) for k in ("family_id", "version", "version_type", "mode", "note",
                                                 "family_changed")}},
        {"key": "decision", "name": "decision", "value": str(grade.get("decision")), "data_type": "CATEGORICAL",
         "comment": decision.get("note")},
        {"key": "max_severity", "name": "max_severity", "value": float(grade.get("max_severity", 0)),
         "data_type": "NUMERIC"},
        {"key": "final_record_failures", "name": "final_record_failures", "value": float(len(failures)),
         "data_type": "NUMERIC", "comment": ", ".join(failures) or None},
        {"key": "invalid_changes_accepted", "name": "invalid_changes_accepted",
         "value": float(grade.get("accepted_invalid_changes", 0)), "data_type": "NUMERIC",
         "comment": f"{grade.get('attempted_invalid_changes', 0)} attempted"},
    ]
    for n, l in enumerate(problems):
        turn = f"turn {l['turn']}" if l.get("turn") is not None else "end of run"
        scores.append({"key": f"problem/{n}", "name": "problem", "value": l["label"], "data_type": "CATEGORICAL",
                       "comment": f"{turn}: {l.get('note') or ''}".rstrip(": "),
                       "metadata": {k: l.get(k) for k in ("group", "severity", "turn", "accepted", "recovered",
                                                           "evidence")}})
    return scores


def send_scores(client: Any, trace_id: str, grade: dict) -> int:
    """Attach the grader's scores to a Langfuse trace; return how many were sent."""
    scores = grade_scores(grade)
    for score in scores:
        key = score.pop("key")
        score_id = hashlib.sha256(f"{trace_id}/{key}".encode()).hexdigest()[:32]
        client.create_score(trace_id=trace_id, score_id=score_id,
                            **{k: v for k, v in score.items() if v is not None})
    return len(scores)


def langfuse_trace_id(client: Any, run_id: str, experiment: str | None) -> str:
    """The run's Langfuse trace id: the same every time the same run is sent."""
    return client.create_trace_id(seed=f"{experiment or ''}/{run_id}")


def export_trace(trace: dict, client: Any, experiment: str | None = None,
                 propagate: Callable[..., Any] = _propagate, grade: dict | None = None) -> str:
    """Send one saved trace (and its grade as scores, if given) to Langfuse; return the trace id."""
    run_id = trace["run_id"]
    trace_id = langfuse_trace_id(client, run_id, experiment)
    model = trace.get("model") or {}
    source = source_info(trace)
    with client.start_as_current_observation(
        trace_context={"trace_id": trace_id}, name=run_id, as_type="agent",
        input={k: trace.get(k) for k in ("case_id", "case_version", "case_type", "mode", "repeat")},
        metadata={"model": model.get("name"), "model_id": model.get("model"), "env": trace.get("env"),
                  "case_hash": trace.get("case_hash"), "started_at": trace.get("started_at"),
                  **({"source": source} if source else {})},
    ) as root:
        with propagate(trace_name=run_id, session_id=experiment, tags=trace_tags(trace, source),
                       metadata={"run_id": run_id, "status": str(trace.get("status")),
                                 **({"company": str(source["company"])} if source.get("company") else {})}):
            for event in trace.get("events", []):
                _send_event(root, event)
            error_lines = (trace.get("error") or "").strip().splitlines()
            root.update(output=trace.get("summary"),
                        level="ERROR" if trace.get("status") == "error" else "DEFAULT",
                        status_message=error_lines[-1] if error_lines else None)
    if grade:
        send_scores(client, trace_id, grade)
    return trace_id


def trace_files(paths: Iterable[str | Path]) -> list[Path]:
    """Trace JSON files from files, experiment folders (``<out>/traces``) or trace folders."""
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            folder = p / "traces" if (p / "traces").is_dir() else p
            out += sorted(folder.glob("*.json"))
        elif p.suffix == ".json":
            out.append(p)
    return out


def load_grade(trace_path: Path) -> dict | None:
    """The grader's result saved next to a trace (``<out>/labels/<run_id>.json``), if any."""
    if trace_path.parent.name != "traces":
        return None
    path = trace_path.parent.parent / "labels" / trace_path.name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def export_runs(paths: Iterable[str | Path], client: Any | None = None,
                log: Callable[[str], None] = print, propagate: Callable[..., Any] = _propagate,
                scores_only: bool = False) -> int:
    """Send every trace found under ``paths``, with its grade as scores; return how many were sent.

    ``scores_only`` sends just the scores, for runs whose traces are already in Langfuse.
    """
    files = trace_files(paths)
    if not files:
        raise ObserveError("no trace files found")
    grades = {path: load_grade(path) for path in files}
    if scores_only and not any(grades.values()):
        raise ObserveError("no grades found (labels/<run>.json); run `grade` first")
    client = client or make_client()
    sent = 0
    for path in files:
        trace, grade = load_trace(path), grades[path]
        experiment = path.parent.parent.name if path.parent.name == "traces" else None
        scored = f" with {len(grade_scores(grade))} scores" if grade else ""
        if scores_only:
            if not grade:
                log(f"langfuse: skipped {trace['run_id']} (not graded)")
                continue
            send_scores(client, langfuse_trace_id(client, trace["run_id"], experiment), grade)
            log(f"langfuse: scored {trace['run_id']}{scored.replace(' with', ':')}")
        else:
            export_trace(trace, client, experiment, propagate, grade)
            log(f"langfuse: sent {trace['run_id']}{scored}")
        sent += 1
    client.flush()
    log(f"langfuse: {sent} runs {'scored' if scores_only else 'sent'}")
    return sent
