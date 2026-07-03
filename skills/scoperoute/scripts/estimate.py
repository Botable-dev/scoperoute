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
from dataclasses import dataclass
from pathlib import Path

# ---- knobs (transparent so you can tune them) ----

# Notional list price, USD per 1M tokens (input, output). As of 2026-06-24.
PRICING = {
    "claude-fable-5":  (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),   # intro pricing through 2026-08-31 (else 3.0/15.0)
    "claude-haiku-4-5": (1.0, 5.0),
}
CHARS_PER_TOKEN = 3.2       # rough for source code (denser than the ~4.0 of prose)
RECON_OVERHEAD = 1.5        # agentic recon re-reads files / adds tool+thinking tokens
# "Don't trim" != "read 49M tokens of video-metadata JSON". Recon reads
# human-authored source, skips data/generated/vendored files, and is bounded:
MAX_FILE_BYTES = 200_000    # a hand-written source file is ~never bigger; above = data/generated
RECON_INPUT_CAP = 300_000   # tokens: recon reads a representative slice, then summarizes
RECON_OUT_BASE = 800        # Sonnet recon output: inventory + per-component notes
RECON_OUT_PER_COMPONENT = 200
SUMMARY_OUT_PER_COMPONENT = 1000   # Opus arch.md per component
ARCH_TOKENS = 1200          # distilled arch.md handed to Fable/controls per component
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
class Estimate:
    project: str
    components: int
    code_files: int
    code_tokens: int
    calls_min: int          # no components trip -> no controls
    calls_max: int          # every component trips -> full controls
    usd_min: float
    usd_max: float


def estimate_project(project: Path, repeat: int = 1) -> Estimate:
    comps = detect_components(project)
    n = len(comps)
    proj_bytes, proj_files = code_bytes(project)
    proj_tok = toks(proj_bytes)

    # Stage 1 — recon (Sonnet 5, low): reads source (no char-truncation), but is
    # bounded — it samples a representative slice then summarizes, so a giant repo
    # doesn't mean a giant bill.
    recon_in = min(int(proj_tok * RECON_OVERHEAD), RECON_INPUT_CAP)
    recon_out = RECON_OUT_BASE + RECON_OUT_PER_COMPONENT * n
    usd = _cost("claude-sonnet-5", recon_in, recon_out)
    calls = 1

    # Stage 2 — summary (Opus): recon notes -> per-component arch.md (one call).
    summary_out = SUMMARY_OUT_PER_COMPONENT * n
    usd += _cost("claude-opus-4-8", recon_out + 500, summary_out)
    calls += 1

    # Stage 3 — probe (Fable): each component, repeated N times.
    probe_calls = n * repeat
    usd += _cost("claude-fable-5", ARCH_TOKENS, PROBE_OUT, probe_calls)
    calls += probe_calls

    usd_min = usd
    calls_min = calls

    # Stage 4 — controls (Opus + Sonnet), only on components that trip.
    # min = none trip; max = all trip.
    ctrl_each = _cost("claude-opus-4-8", ARCH_TOKENS, PROBE_OUT) + \
                _cost("claude-sonnet-5", ARCH_TOKENS, PROBE_OUT)
    usd_max = usd + ctrl_each * n
    calls_max = calls + 2 * n

    return Estimate(str(project), n, proj_files, proj_tok,
                    calls_min, calls_max, round(usd_min, 3), round(usd_max, 3))


def summarize(projects: list[Path], repeat: int = 1) -> list[Estimate]:
    return [estimate_project(p, repeat) for p in projects if p.is_dir()]


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
    args = ap.parse_args()
    projects = _find_projects(args.root) if args.root else list(args.projects)
    print_report(summarize(projects, args.repeat), args.repeat)


if __name__ == "__main__":
    main()
