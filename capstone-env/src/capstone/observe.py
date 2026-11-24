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

Credit runs also carry the real source behind the renamed borrower (team-only: the agent never saw
it), read from the family file the run was played with: tags ``company:<ticker>``,
``source:<ConvFinQA id>``, ``borrower:<name shown to the agent>``, ``policy:<id>``, and the same plus
the spec's renames (e.g. "sikorsky" -> "Acquired Business") in the trace metadata under ``source``.
"""

from __future__ import annotations

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


def export_trace(trace: dict, client: Any, experiment: str | None = None,
                 propagate: Callable[..., Any] = _propagate) -> str:
    """Send one saved trace to Langfuse; return the Langfuse trace id."""
    run_id = trace["run_id"]
    trace_id = client.create_trace_id(seed=f"{experiment or ''}/{run_id}")
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


def export_runs(paths: Iterable[str | Path], client: Any | None = None,
                log: Callable[[str], None] = print, propagate: Callable[..., Any] = _propagate) -> int:
    """Send every trace found under ``paths``; return how many were sent."""
    files = trace_files(paths)
    if not files:
        raise ObserveError("no trace files found")
    client = client or make_client()
    for path in files:
        trace = load_trace(path)
        experiment = path.parent.parent.name if path.parent.name == "traces" else None
        export_trace(trace, client, experiment, propagate)
        log(f"langfuse: sent {trace['run_id']}")
    client.flush()
    log(f"langfuse: {len(files)} runs sent")
    return len(files)
