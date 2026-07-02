#!/usr/bin/env python3
"""transcript.py — read Claude Code session transcripts, metadata only.

Claude Code writes every session append-only to
    ~/.claude/projects/<encoded-project-path>/<session-uuid>.jsonl
one JSON object per line. Subagents (incl. Workflow agents) are written to
sidecar files under <session-uuid>/subagents/**/agent-*.jsonl, each with its
own served model.

This module is the shared reader for:
  - scoperoute.py's CLI backend (read the served model / refusal of a probe
    run that went through `claude -p`), and
  - fable_watch.py (Phase 2 live fallback monitor).

Design invariants (PRD_scoperoute.md §4.5):
  - METADATA ONLY. Never read or return prompt/response text.
  - Never return stop_details.explanation — on a refusal it carries a live,
    tokened URL. Only the category label leaves this module.

Stdlib only.

Empirical schema (Claude Code 2.1.x), for reference:
  assistant record top-level keys:
    type, uuid, parentUuid, sessionId, requestId, isSidechain, userType,
    timestamp, cwd, gitBranch, version, entrypoint, message
  served model:  .message.model   (top-level .model is null)
                 bare "claude-opus-4-8" | "claude-fable-5", dated
                 "claude-haiku-4-5-20251001", or the sentinel "<synthetic>"
  refusal:       .message.stop_reason == "refusal"
                 .message.stop_details = {type, category, explanation}
                 (the refusal turn's .message.model is "<synthetic>")
  usage:         .message.usage.{input_tokens, output_tokens,
                 cache_read_input_tokens, cache_creation_input_tokens,
                 service_tier, speed, inference_geo, iterations}
  subagent turns: separate sidecar files, isSidechain==true, own agentId
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

SYNTHETIC = "<synthetic>"
DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# Known model families, longest/most-specific first so startswith() prefix
# matching resolves dated snapshots (claude-haiku-4-5-20251001 -> claude-haiku-4-5).
MODEL_FAMILIES = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
)

# Aliases Claude Code accepts in /model and settings.json (e.g. "opus", "opus[1m]").
MODEL_ALIASES = {
    "fable": "claude-fable-5",
    "mythos": "claude-mythos-5",
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}


def model_family(model: str | None) -> str | None:
    """Normalize a model string to its family, or None for synthetic/unknown-empty.

    Handles the three forms seen on disk / in settings:
      "claude-opus-4-8"           -> "claude-opus-4-8"
      "claude-haiku-4-5-20251001" -> "claude-haiku-4-5"  (dated snapshot)
      "opus[1m]" / "opus"         -> "claude-opus-4-8"    (alias + context tag)
    Returns None for None, "" and the "<synthetic>" sentinel (non-served turns).
    """
    if not model or model == SYNTHETIC:
        return None
    s = model.strip().lower()
    s = re.sub(r"\[[^\]]*\]$", "", s)          # strip a trailing [1m] / [200k] tag
    s = MODEL_ALIASES.get(s, s)                # opus -> claude-opus-4-8
    for fam in MODEL_FAMILIES:
        if s.startswith(fam):
            return fam
    return s                                   # unknown but non-synthetic — keep as-is


# ---------- project path <-> session directory ----------

def encode_project_path(path: str | os.PathLike) -> str:
    """Map an absolute project path to its ~/.claude/projects folder name.

    Claude Code replaces every non-[A-Za-z0-9] character with '-', so a leading
    '/' becomes a leading '-'. This is LOSSY / non-reversible: '/a/b_c' and
    '/a/b-c' collide. Forward mapping only.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path).resolve()))


def session_dir(project_path: str | os.PathLike,
                projects_root: str | os.PathLike | None = None) -> Path:
    root = Path(projects_root) if projects_root else DEFAULT_PROJECTS_ROOT
    return root / encode_project_path(project_path)


def latest_session(sdir: str | os.PathLike) -> Path | None:
    """Newest top-level <uuid>.jsonl in a session dir (ignores the <uuid>/ payload dirs)."""
    sdir = Path(sdir)
    if not sdir.is_dir():
        return None
    files = [p for p in sdir.glob("*.jsonl") if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def is_live(jsonl_path: str | os.PathLike, window: float = 90.0) -> bool:
    """True if the transcript was written within `window` seconds (active session)."""
    try:
        return (time.time() - Path(jsonl_path).stat().st_mtime) < window
    except OSError:
        return False


def session_files(sdir: str | os.PathLike, session_id: str,
                  include_subagents: bool = True) -> list[Path]:
    """Main transcript + subagent sidecar files for one session.

    A Fable->Opus fallback can hit a single subagent independently, so the
    monitor must follow the sidecars too.
    """
    sdir = Path(sdir)
    files: list[Path] = []
    main = sdir / f"{session_id}.jsonl"
    if main.is_file():
        files.append(main)
    if include_subagents:
        payload = sdir / session_id
        if payload.is_dir():
            # subagents/agent-*.jsonl and subagents/workflows/wf_*/agent-*.jsonl
            files.extend(sorted(payload.glob("subagents/**/agent-*.jsonl")))
    return files


# ---------- per-turn parsing (metadata only) ----------

@dataclass
class Turn:
    """A normalized, metadata-only view of one assistant record. No text content."""
    served_model: str | None          # raw .message.model (may be "<synthetic>")
    family: str | None                # normalized family, or None if synthetic/served-less
    synthetic: bool                   # served_model == "<synthetic>"
    stop_reason: str | None
    refusal: bool                     # stop_reason == "refusal"
    category: str | None              # stop_details.category (NEVER the explanation URL)
    input_tokens: int | None
    output_tokens: int | None
    cache_read: int | None
    cache_creation: int | None
    service_tier: str | None
    speed: str | None
    inference_geo: str | None
    fallback_iter: bool               # any usage.iterations[].type == "fallback_message"
    uuid: str | None
    parent_uuid: str | None
    session_id: str | None
    request_id: str | None
    agent_id: str | None
    is_sidechain: bool | None
    timestamp: str | None


def parse_line(line: str) -> Turn | None:
    """Parse one JSONL line into a Turn, or None if it is not an assistant record.

    Returns a Turn for every assistant record — including a `<synthetic>` refusal
    (read `refusal`/`category`, not `family`, in that case).
    """
    line = line.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(rec, dict) or rec.get("type") != "assistant":
        return None
    msg = rec.get("message") or {}
    usage = msg.get("usage") or {}
    stop_details = msg.get("stop_details") or {}
    iters = usage.get("iterations") or []
    served = msg.get("model")
    stop_reason = msg.get("stop_reason")
    return Turn(
        served_model=served,
        family=model_family(served),
        synthetic=(served == SYNTHETIC),
        stop_reason=stop_reason,
        refusal=(stop_reason == "refusal"),
        category=stop_details.get("category"),          # label only, never .explanation
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cache_read=usage.get("cache_read_input_tokens"),
        cache_creation=usage.get("cache_creation_input_tokens"),
        service_tier=usage.get("service_tier"),
        speed=usage.get("speed"),
        inference_geo=usage.get("inference_geo"),
        fallback_iter=any(
            isinstance(it, dict) and it.get("type") == "fallback_message" for it in iters
        ),
        uuid=rec.get("uuid"),
        parent_uuid=rec.get("parentUuid"),
        session_id=rec.get("sessionId"),
        request_id=rec.get("requestId"),
        agent_id=rec.get("agentId"),
        is_sidechain=rec.get("isSidechain"),
        timestamp=rec.get("timestamp"),
    )


def iter_turns(jsonl_path: str | os.PathLike):
    """Yield Turn for each assistant record in a transcript file."""
    with open(jsonl_path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            turn = parse_line(line)
            if turn is not None:
                yield turn


def last_served_turn(jsonl_path: str | os.PathLike) -> Turn | None:
    """The last assistant record that actually reached a model or was refused.

    Skips plain `<synthetic>` housekeeping turns (interrupts / hooks) UNLESS they
    are refusals — a refusal is exactly what we want to surface, and it is written
    as a `<synthetic>` turn. Used by scoperoute.py's CLI backend to read the
    outcome of a `claude -p` probe.
    """
    result: Turn | None = None
    try:
        for turn in iter_turns(jsonl_path):
            if turn.refusal or not turn.synthetic:
                result = turn
    except OSError:
        return None
    return result


def is_fallback(turn: Turn, launch_family: str | None) -> bool | None:
    """Did this turn fall off the launch model?

    Returns None when it can't be judged (synthetic/served-less turn, or unknown
    launch model) — such turns must not count toward a fallback rate. Otherwise
    True iff the served family differs from the launch family.
    """
    if turn.synthetic or turn.family is None or launch_family is None:
        return None
    return turn.family != launch_family


if __name__ == "__main__":
    # Tiny CLI for eyeballing a transcript's served-model / refusal timeline.
    # Prints metadata only.
    import argparse

    ap = argparse.ArgumentParser(description="Dump served-model/refusal timeline of a transcript (metadata only).")
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("--launch-model", default=None, help="e.g. claude-fable-5 (to flag fallbacks)")
    args = ap.parse_args()

    launch = model_family(args.launch_model)
    n = fb = refusals = 0
    for t in iter_turns(args.jsonl):
        if t.refusal:
            refusals += 1
            print(f"REFUSAL   category={t.category} ts={t.timestamp}")
            continue
        if t.synthetic:
            continue
        n += 1
        flag = is_fallback(t, launch)
        if flag:
            fb += 1
        mark = "FALLBACK" if flag else "served  "
        print(f"{mark}  {t.family:<18} tokens_out={t.output_tokens} ts={t.timestamp}")
    if launch:
        rate = (fb / n) if n else 0.0
        print(f"\n{n} served turns · {fb} off-{launch} · fallback_rate={rate:.2f} · {refusals} refusals")
