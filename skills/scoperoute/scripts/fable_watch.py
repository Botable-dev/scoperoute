#!/usr/bin/env python3
"""fable_watch.py — Phase 2: live Fable->Opus fallback monitor for Claude Code.

Phase 1 (scoperoute.py) is a cold, one-shot triage. This is the runtime half:
during a real Claude Code session it tails the session transcript and, on each
turn, tells you whether the turn was served by the model you launched on (Fable)
or fell back to Opus — the trigger firing *by the work you're doing*, which the
cold probe can't see.

    fable_watch.py                       # auto: newest session of the cwd's project
    fable_watch.py --project ~/dev/app --launch-model claude-fable-5 --alert-streak 3
    fable_watch.py --session <uuid> --from-start --once   # replay a finished session

It follows the main transcript AND the subagent sidecars
(<session>/subagents/**/agent-*.jsonl) — a fallback can hit one subagent alone.

Privacy (PRD §4.5): METADATA ONLY. The event log carries model, tokens, stop
reason, refusal category, and ids — never prompt/response text, and never a
refusal's explanation (it holds a live tokened URL).

Stdlib only; shares transcript.py with the Phase 1 engine.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcript as T  # noqa: E402


@dataclass
class Metrics:
    served: int = 0            # non-synthetic served turns (fallback denominator)
    fallback: int = 0          # served off the launch model
    streak: int = 0            # consecutive off-launch turns
    refusals: int = 0
    categories: Counter = field(default_factory=Counter)


class Monitor:
    def __init__(self, args):
        self.args = args
        self.launch = T.model_family(args.launch_model)   # may be None -> inferred lazily
        self.metrics: dict[str, Metrics] = defaultdict(Metrics)
        self.event_log = open(args.event_log, "a", encoding="utf-8") if args.event_log else None
        self._offsets: dict[Path, int] = {}

    # ---- output ----
    def _alert(self, msg: str) -> None:
        print(f"  {msg}", flush=True)

    def _emit(self, turn: T.Turn, kind: str, scope: str) -> None:
        if not self.event_log:
            return
        rec = {
            "ts": turn.timestamp, "kind": kind, "scope": scope,
            "session_id": turn.session_id, "request_id": turn.request_id,
            "uuid": turn.uuid, "parent_uuid": turn.parent_uuid,
            "is_sidechain": turn.is_sidechain, "agent_id": turn.agent_id,
            "served_model": turn.served_model, "launch_model": self.launch,
            "is_fallback": (kind == "fallback"), "fallback_iter": turn.fallback_iter,
            "refusal": turn.refusal, "category": turn.category,   # label only, never .explanation
            "tokens": {"input": turn.input_tokens, "output": turn.output_tokens,
                       "cache_read": turn.cache_read, "cache_creation": turn.cache_creation,
                       "service_tier": turn.service_tier, "speed": turn.speed,
                       "inference_geo": turn.inference_geo},
        }
        self.event_log.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.event_log.flush()

    # ---- per-turn ----
    def on_turn(self, turn: T.Turn, path: Path) -> None:
        scope = f"sub:{turn.agent_id[:8]}" if turn.agent_id else "main"
        m = self.metrics[scope]

        if turn.refusal:                       # <synthetic> refusal — read stop/category, not model
            m.refusals += 1
            if turn.category:
                m.categories[turn.category] += 1
            self._emit(turn, "refusal", scope)
            self._alert(f"REFUSAL[{turn.category}] {scope} @ {turn.timestamp}")
            return
        if turn.synthetic:                     # interrupt / hook / stop_sequence housekeeping
            return

        if self.launch is None:                # infer launch model from the first served turn
            self.launch = turn.family
            print(f"[launch model inferred: {self.launch}]", flush=True)

        m.served += 1
        fb = T.is_fallback(turn, self.launch)
        if fb:
            m.fallback += 1
            m.streak += 1
            self._emit(turn, "fallback", scope)
            tag = " (Fable->Opus)" if (self.launch == "claude-fable-5"
                                       and (turn.family or "").startswith("claude-opus")) else ""
            corr = " [usage.iterations=fallback_message]" if turn.fallback_iter else ""
            self._alert(f"FALLBACK {scope}: served={turn.family} launch={self.launch}{tag}"
                        f"  rate={m.fallback}/{m.served}{corr}")
            if m.streak >= self.args.alert_streak:
                self._alert(f"STREAK {m.streak} consecutive off-{self.launch} turns in {scope}")
        else:
            if self.args.log_served:
                self._emit(turn, "served", scope)
            m.streak = 0

    # ---- tailing ----
    def _session_files(self):
        return T.session_files(self.sdir, self.session_id,
                               include_subagents=not self.args.no_subagents)

    def follow(self) -> None:
        first = True
        while True:
            for f in self._session_files():
                if f not in self._offsets:
                    # live mode starts at end (watch only new turns); replay starts at 0
                    start_at_end = first and not self.args.from_start and not self.args.once
                    try:
                        self._offsets[f] = f.stat().st_size if start_at_end else 0
                    except OSError:
                        self._offsets[f] = 0
            for f, off in list(self._offsets.items()):
                try:
                    size = f.stat().st_size
                except OSError:
                    continue
                if size < off:                 # truncated / rotated
                    off = 0
                if size <= off:
                    continue
                with f.open("rb") as fh:
                    fh.seek(off)
                    data = fh.read()
                parts = data.split(b"\n")
                if data.endswith(b"\n"):
                    complete, consumed = parts[:-1], len(data)
                else:                           # keep the trailing partial line for next poll
                    complete = parts[:-1]
                    consumed = len(data) - len(parts[-1])
                for raw in complete:
                    turn = T.parse_line(raw.decode("utf-8", "ignore"))
                    if turn is not None:
                        self.on_turn(turn, f)
                self._offsets[f] = off + consumed
            first = False
            if self.args.once:
                return
            time.sleep(self.args.poll)

    # ---- summary + feedback loop (PRD §4.6) ----
    def summary(self) -> None:
        print("\n=== fable_watch summary ===", flush=True)
        tot_served = tot_fb = tot_ref = 0
        agg_cat: Counter = Counter()
        for scope, m in sorted(self.metrics.items()):
            tot_served += m.served
            tot_fb += m.fallback
            tot_ref += m.refusals
            agg_cat.update(m.categories)
            rate = (m.fallback / m.served) if m.served else 0.0
            extra = f" · refusals={m.refusals}" if m.refusals else ""
            print(f"  {scope:<14} served={m.served:<4} off-launch={m.fallback:<4} "
                  f"rate={rate:.2f}{extra}", flush=True)
        rate = (tot_fb / tot_served) if tot_served else 0.0
        print(f"  {'TOTAL':<14} served={tot_served:<4} off-launch={tot_fb:<4} rate={rate:.2f} "
              f"· refusals={tot_ref}", flush=True)
        if agg_cat:
            print("  refusal categories: " + ", ".join(f"{c}={n}" for c, n in agg_cat.most_common()))

        # feedback into Phase 1: sustained fallback above threshold => reclassify hint
        if self.launch == "claude-fable-5" and tot_served and rate >= self.args.fallback_rate_threshold:
            print(f"\n  ⚑ {self.project} ran at {rate:.0%} Fable->Opus fallback "
                  f"(≥ {self.args.fallback_rate_threshold:.0%}). If Phase 1 marked it "
                  f"fable_friendly, reclassify toward code_triggered.", flush=True)
            with open(self.args.reclass_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"project": str(self.project), "fallback_rate": round(rate, 3),
                                     "served": tot_served, "off_launch": tot_fb,
                                     "launch_model": self.launch}) + "\n")


def resolve_target(args) -> tuple[Path, Path, str]:
    project = Path(args.project).resolve() if args.project else Path.cwd()
    sdir = T.session_dir(project)
    if not sdir.is_dir():
        sys.exit(f"No Claude Code sessions found for {project}\n  (looked in {sdir})")
    if args.session and args.session != "latest":
        jsonl = sdir / f"{args.session}.jsonl"
        if not jsonl.is_file():
            sys.exit(f"Session {args.session} not found in {sdir}")
    else:
        jsonl = T.latest_session(sdir)
        if jsonl is None:
            sys.exit(f"No .jsonl sessions in {sdir}")
    return project, sdir, jsonl.stem


def main():
    ap = argparse.ArgumentParser(description="Live Fable->Opus fallback monitor for Claude Code.")
    ap.add_argument("--project", type=Path, default=None, help="Project path (default: cwd).")
    ap.add_argument("--session", default="latest", help="Session uuid, or 'latest' (default).")
    ap.add_argument("--launch-model", default=None,
                    help="Model the session launched on (e.g. claude-fable-5). "
                         "Default: inferred from the first served turn.")
    ap.add_argument("--alert-streak", type=int, default=3, help="Alert on N consecutive off-launch turns.")
    ap.add_argument("--fallback-rate-threshold", type=float, default=0.30,
                    help="Reclassification hint threshold (PRD §4.6).")
    ap.add_argument("--event-log", type=Path, default=None, help="Append metadata-only events here (JSONL).")
    ap.add_argument("--reclass-log", type=Path, default=Path("reclass.jsonl"))
    ap.add_argument("--from-start", action="store_true", help="Read the whole session, not just new turns.")
    ap.add_argument("--once", action="store_true", help="Scan once and exit (replay); don't follow.")
    ap.add_argument("--no-subagents", action="store_true", help="Ignore subagent sidecar files.")
    ap.add_argument("--log-served", action="store_true", help="Also log non-fallback served turns.")
    ap.add_argument("--poll", type=float, default=0.7, help="Poll interval seconds (live mode).")
    args = ap.parse_args()

    project, sdir, session_id = resolve_target(args)
    mon = Monitor(args)
    mon.project, mon.sdir, mon.session_id = project, sdir, session_id

    mode = "replay" if (args.once or args.from_start) else "live tail"
    print(f"fable_watch · {mode} · project={project.name} · session={session_id[:8]} "
          f"· launch={mon.launch or '(infer)'} · alert-streak={args.alert_streak}", flush=True)
    if mode == "live tail":
        print("  watching for new turns… (Ctrl-C to stop)", flush=True)

    def _stop(signum, frame):
        mon.summary()
        if mon.event_log:
            mon.event_log.close()
        sys.exit(0)
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    mon.follow()      # returns only in --once mode
    mon.summary()
    if mon.event_log:
        mon.event_log.close()


if __name__ == "__main__":
    main()
