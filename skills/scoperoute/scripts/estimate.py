#!/usr/bin/env python3
"""estimate.py — cost/size calculator for a scoperoute run, BEFORE you start it.

Triaging with the distillation pipeline (Sonnet recon -> Opus summary -> Fable
probe) reads whole codebases — no trimming — so a big --root sweep can be
expensive. Run this first to see what you're committing to, per project and for
the whole workplace, broken down by pipeline stage and model.

    python estimate.py --root ~/workplace
    python estimate.py --projects ~/dev/a ~/dev/b --repeat 3

Pure filesystem + arithmetic — NO model calls, so it's free and instant. Token
counts are estimates (code tokenizes denser than prose); dollar figures are
notional (in CLI/subscription mode you spend quota, not dollars) and let you
compare runs and rank projects by cost.

Stdlib only.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import models as M  # noqa: E402  — the one declarative model/pricing table (rec #6)

# ---- knobs (transparent so you can tune them) ----

# Notional list price, USD per 1M tokens (input, output). Single source of truth in
# models.py so the estimator can never drift from the ids/prices the engine runs.
PRICING = M.PRICING
CHARS_PER_TOKEN = 3.2       # rough for source code (denser than the ~4.0 of prose)
RECON_OVERHEAD = 1.5        # agentic recon re-reads files / adds tool+thinking tokens
# These are ESTIMATION-ONLY heuristics — they model how much the agentic recon is
# assumed to READ for the cost projection. They do NOT truncate any triage data: the
# real arch recon reads files agentically with no cap. They exist so the estimate is
# realistic ("read everything" must not project reading 49M tokens of video-metadata
# JSON). Tune them if your projection looks off; they never affect a verdict.
MAX_FILE_BYTES = 200_000    # exclude data/generated blobs from the cost projection
RECON_INPUT_CAP = 300_000   # tokens the recon is assumed to sample before summarizing
RECON_OUT_BASE = 800        # Sonnet recon output: inventory + per-component notes
RECON_OUT_PER_COMPONENT = 200
SUMMARY_OUT_PER_COMPONENT = 1000   # Opus arch.md per component
ARCH_TOKENS = 1200          # distilled arch.md handed to Fable/controls per component
CODE_EXCERPT_TOKENS = 1500  # Opus-curated real code added to the Fable payload (--probe code)
CURATE_INPUT_CAP = 190_000  # tokens of source Opus reads while curating (bounded curation input)
PROBE_OUT = 500             # improve-architecture reply (or refusal)

IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".build", ".next", "target", ".idea", ".vscode",
    "vendor", ".terraform", "coverage", "site-packages", "Pods", ".gradle", ".tox",
    ".cache", "DerivedData", ".swiftpm",
}
# Generated / lock / data files to skip regardless of size.
SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "composer.lock", "Gemfile.lock", "go.sum", "uv.lock",
}
SKIP_SUFFIXES = (".min.js", ".min.css", ".map", ".bundle.js", ".lock")


def is_source_file(name: str, size: int) -> bool:
    """A human-authored source file worth reading — not data, generated, or vendored."""
    if Path(name).suffix.lower() not in CODE_EXT:
        return False
    if name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES):
        return False
    return size <= MAX_FILE_BYTES
CODE_EXT = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".c",
    ".h", ".cpp", ".cc", ".hpp", ".cs", ".php", ".swift", ".kt", ".sh", ".sql",
    ".yaml", ".yml", ".toml", ".json",
}
# Top-level dir names that read as isolated components.
COMPONENT_HINTS = {
    "frontend", "backend", "client", "server", "api", "web", "app", "mobile",
    "ios", "android", "cli", "core", "lib", "worker", "admin", "ui", "gateway",
}
# Monorepo containers whose immediate children are each a component.
MONOREPO_CONTAINERS = ("packages", "services", "apps", "modules", "cmd")


def toks(nchars: float) -> int:
    return math.ceil(nchars / CHARS_PER_TOKEN)


def code_bytes(root: Path) -> tuple[int, int]:
    """(source bytes, file count) under root — skips junk dirs and data/generated/
    vendored/oversize files (see is_source_file)."""
    total = files = 0
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in fs:
            try:
                size = (Path(dp) / f).stat().st_size
            except OSError:
                continue
            if is_source_file(f, size):
                total += size
                files += 1
    return total, files


def detect_components(project: Path) -> list[tuple[str, Path]]:
    """Isolated pieces to probe separately. Monorepo children + component-named
    top-level dirs; else the whole project is one component."""
    comps: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for container in MONOREPO_CONTAINERS:
        d = project / container
        if d.is_dir():
            for sub in sorted(d.iterdir()):
                if sub.is_dir() and sub.name not in IGNORE_DIRS and sub not in seen:
                    comps.append((f"{container}/{sub.name}", sub))
                    seen.add(sub)
    for sub in sorted(project.iterdir()):
        if (sub.is_dir() and sub.name not in IGNORE_DIRS
                and sub.name.lower() in COMPONENT_HINTS and sub not in seen):
            comps.append((sub.name, sub))
            seen.add(sub)
    if not comps:
        comps = [(project.name, project)]
    return comps


def _cost(model: str, in_tok: int, out_tok: int, calls: int = 1) -> float:
    pin, pout = PRICING[model]
    return calls * (in_tok / 1e6 * pin + out_tok / 1e6 * pout)


@dataclass
class StageCost:
    stage: str          # recon | summary | probe | controls
    model: str
    tokens_in: int
    tokens_out: int
    calls: int
    usd: float
    when: str           # "always" | "if_tripped"  (controls run only where Fable trips)


@dataclass
class Estimate:
    project: str
    components: int
    code_files: int
    code_tokens: int
    calls_min: int          # no components trip -> no controls
    calls_max: int          # every component trips -> full controls
    usd_min: float
    usd_max: float
    stages: list            # list[StageCost] — the tokens/$/part breakdown


def estimate_project(project: Path, repeat: int = 1, mode: str = "code") -> Estimate:
    comps = detect_components(project)
    n = len(comps)
    proj_bytes, proj_files = code_bytes(project)
    proj_tok = toks(proj_bytes)

    stages: list[StageCost] = []

    # Stage 1 — recon (Sonnet 5, low): reads source (no char-truncation), but is
    # bounded — it samples a representative slice then summarizes, so a giant repo
    # doesn't mean a giant bill.
    recon_in = min(int(proj_tok * RECON_OVERHEAD), RECON_INPUT_CAP)
    recon_out = RECON_OUT_BASE + RECON_OUT_PER_COMPONENT * n
    stages.append(StageCost("recon", M.RECON_MODEL.id, recon_in, recon_out, 1,
                            _cost(M.RECON_MODEL.id, recon_in, recon_out), "always"))

    # Stage 2 — summary (Opus): recon notes -> per-component arch.md (one call).
    sum_in, sum_out = recon_out + 500, SUMMARY_OUT_PER_COMPONENT * n
    stages.append(StageCost("summary", M.SUMMARY_MODEL.id, sum_in, sum_out, 1,
                            _cost(M.SUMMARY_MODEL.id, sum_in, sum_out), "always"))

    # Stage 2b — curate (Opus, --probe code only): reads real source (bounded) and
    # returns the high-signal code excerpt per component that gets added to the probe.
    payload_in = ARCH_TOKENS
    if mode == "code":
        curate_in = min(proj_tok, CURATE_INPUT_CAP)
        curate_out = CODE_EXCERPT_TOKENS * n
        stages.append(StageCost("curate", M.SUMMARY_MODEL.id, curate_in, curate_out, 1,
                                _cost(M.SUMMARY_MODEL.id, curate_in, curate_out), "always"))
        payload_in = ARCH_TOKENS + CODE_EXCERPT_TOKENS      # Fable sees arch + real code

    # Stage 3 — probe (Fable): each component, repeated N times.
    probe_calls = n * repeat
    stages.append(StageCost("probe", M.PROBE_MODEL.id,
                            payload_in * probe_calls, PROBE_OUT * probe_calls, probe_calls,
                            _cost(M.PROBE_MODEL.id, payload_in, PROBE_OUT, probe_calls), "always"))

    # Stage 4 — controls (Opus + Sonnet), only on components that trip (max = all n).
    for mdl, _eff in M.CONTROLS:
        stages.append(StageCost("controls", mdl.id, payload_in * n, PROBE_OUT * n, n,
                                _cost(mdl.id, payload_in, PROBE_OUT, n), "if_tripped"))

    usd_min = round(sum(s.usd for s in stages if s.when == "always"), 3)
    usd_max = round(sum(s.usd for s in stages), 3)
    calls_min = sum(s.calls for s in stages if s.when == "always")
    calls_max = sum(s.calls for s in stages)

    return Estimate(str(project), n, proj_files, proj_tok,
                    calls_min, calls_max, usd_min, usd_max, stages)


def summarize(projects: list[Path], repeat: int = 1, mode: str = "code") -> list[Estimate]:
    return [estimate_project(p, repeat, mode) for p in projects if p.is_dir()]


def print_report(ests: list[Estimate], repeat: int) -> None:
    if not ests:
        print("No projects.")
        return
    ests = sorted(ests, key=lambda e: e.usd_max, reverse=True)
    name_w = min(max((len(Path(e.project).name) for e in ests), default=12), 30)
    print(f"{'PROJECT':<{name_w}}  {'CMP':>3}  {'FILES':>6}  {'CODE~tok':>9}  "
          f"{'CALLS':>9}  {'~USD (min–max)':>16}")
    print("-" * (name_w + 54))
    for e in ests:
        nm = Path(e.project).name[:name_w]
        calls = f"{e.calls_min}-{e.calls_max}" if e.calls_min != e.calls_max else str(e.calls_min)
        usd = f"${e.usd_min:.2f}-${e.usd_max:.2f}"
        print(f"{nm:<{name_w}}  {e.components:>3}  {e.code_files:>6}  {e.code_tokens:>9,}  "
              f"{calls:>9}  {usd:>16}")
    tmin = sum(e.usd_min for e in ests)
    tmax = sum(e.usd_max for e in ests)
    cmin = sum(e.calls_min for e in ests)
    cmax = sum(e.calls_max for e in ests)
    tok = sum(e.code_tokens for e in ests)
    print("-" * (name_w + 54))
    print(f"{'TOTAL':<{name_w}}  {sum(e.components for e in ests):>3}  "
          f"{sum(e.code_files for e in ests):>6}  {tok:>9,}  "
          f"{f'{cmin}-{cmax}':>9}  {f'${tmin:.2f}-${tmax:.2f}':>16}")

    # tokens / $ per pipeline part (the "tokens/$/part" the launch prompt asks for)
    from collections import defaultdict
    stage_agg = defaultdict(lambda: {"models": set(), "calls": 0, "tin": 0, "tout": 0,
                                     "usd": 0.0, "when": "always"})
    model_agg = defaultdict(lambda: {"tin": 0, "tout": 0, "usd_min": 0.0, "usd_max": 0.0})
    for e in ests:
        for s in e.stages:
            a = stage_agg[s.stage]
            a["models"].add(s.model.replace("claude-", ""))
            a["calls"] += s.calls; a["tin"] += s.tokens_in; a["tout"] += s.tokens_out; a["usd"] += s.usd
            if s.when == "if_tripped":
                a["when"] = "if_tripped"
            m = model_agg[s.model]
            m["tin"] += s.tokens_in; m["tout"] += s.tokens_out; m["usd_max"] += s.usd
            if s.when == "always":
                m["usd_min"] += s.usd
    print("\nBy stage (tokens / $ per part):")
    for stage in ("recon", "summary", "curate", "probe", "controls"):
        if stage in stage_agg:
            a = stage_agg[stage]
            tag = "" if a["when"] == "always" else "   ← only components that trip"
            print(f"  {stage:<9} {'+'.join(sorted(a['models'])):<20} calls={a['calls']:<5} "
                  f"in={a['tin']:>10,} out={a['tout']:>8,}  ${a['usd']:>7.2f}{tag}")
    print("By model:")
    for model in ("claude-sonnet-5", "claude-opus-4-8", "claude-fable-5"):
        if model in model_agg:
            m = model_agg[model]
            print(f"  {model:<18} in={m['tin']:>10,} out={m['tout']:>8,}  "
                  f"${m['usd_min']:.2f}–${m['usd_max']:.2f}")
    print(f"\n{len(ests)} project(s) · repeat={repeat} · "
          f"~{tmin:.2f}–{tmax:.2f} USD notional  (min = nothing trips → no controls; "
          f"max = every component trips → full controls)")
    print("Estimates only: tokens ≈ chars/%.1f (code is denser than prose); "
          "pricing is notional list price (CLI/subscription spends quota, not $)."
          % CHARS_PER_TOKEN)


def _find_projects(root: Path) -> list[Path]:
    out = []
    for dp, dirs, _ in os.walk(root):
        if ".git" in dirs:
            out.append(Path(dp))
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description="Estimate a scoperoute run's cost/size before starting.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--root", type=Path, help="Estimate every git repo under here.")
    g.add_argument("--projects", type=Path, nargs="+", help="Estimate these paths.")
    ap.add_argument("--repeat", type=int, default=1, help="Probe repeats per component (majority vote).")
    ap.add_argument("--probe", choices=["summary", "arch", "code"], default="code",
                    help="Which pipeline to price: code (default, +Opus curate +real code in the "
                         "Fable payload), arch (prose only), or summary (legacy).")
    ap.add_argument("--tier", choices=["pro", "max5", "max20", "team"], default=None,
                    help="Claude plan for the $/% math (else CodexBar detects it).")
    ap.add_argument("--plan-usd", type=float, default=None, help="Override the plan's monthly USD.")
    ap.add_argument("--no-codexbar", action="store_true", help="Don't call CodexBar for real tier/usage.")
    args = ap.parse_args()
    projects = _find_projects(args.root) if args.root else list(args.projects)
    ests = summarize(projects, args.repeat, args.probe)
    print_report(ests, args.repeat)
    try:
        import subscription as SUB
        snap = None if args.no_codexbar else SUB.snapshot()
        print(SUB.format_block(ests, args.tier, args.plan_usd, snap))
    except Exception:
        pass


if __name__ == "__main__":
    main()
