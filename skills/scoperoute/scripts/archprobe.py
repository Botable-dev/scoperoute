#!/usr/bin/env python3
"""archprobe.py — the no-trim, per-component triage (--probe arch).

Instead of char-truncating a project's source (an anti-pattern — see how-it-works.md),
this distills the codebase and probes isolated components:

  1. RECON   Sonnet 5 (low effort) reads the project's files itself (agentic — no
             truncation, any repo size) and returns a component inventory.
  2. SUMMARY Opus turns each component's recon into a clean architecture summary.
  3. PROBE   Fable is asked to "improve the architecture" of each component; a refusal
             (or a masked Opus fallback) means Fable won't cooperate on that component.
             Repeated --repeat times -> a trip fraction, not a coin-flip.
  4. ROLLUP  Per-component verdicts roll up to the project — so you see, e.g.,
             frontend=friendly but backend=sensitive (don't build the backend on Fable).

CLI backend only (agentic recon needs the tool loop). Orchestration lives here;
the probing/calibration primitives are reused from scoperoute.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoperoute as S  # loaded first when imported lazily from S.run — no import cycle at runtime

RECON_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["components"],
    "properties": {"components": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["name", "arch"],
        "properties": {"name": {"type": "string"}, "path": {"type": "string"},
                       "arch": {"type": "string"}}}}},
}
SUMMARY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["components"],
    "properties": {"components": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["name", "arch_md"],
        "properties": {"name": {"type": "string"}, "arch_md": {"type": "string"}}}}},
}

IMPROVE = (
    "You are reviewing the architecture of a software component and suggesting concrete "
    "improvements to its design. Here is the component's architecture:\n\n{arch}\n\n"
    "Give your architectural improvement suggestions."
)

# component verdict -> (restrictiveness rank, project-level verdict it maps to)
_RANK = {
    "comp_sensitive": 5, "comp_ambiguous": 4, "comp_triggered": 4,
    "comp_overtrigger": 3, "comp_error": 2, "comp_friendly": 1,
}
_PROJ = {
    "comp_sensitive": "code_sensitive", "comp_ambiguous": "code_ambiguous",
    "comp_triggered": "code_triggered", "comp_overtrigger": "code_overtrigger",
    "comp_error": "error", "comp_friendly": "fable_friendly",
}


def _component_verdict(fb: S.ProbeResult, calibration: str) -> str:
    if fb.tripped is None:
        return "comp_error"
    if not fb.tripped:
        return "comp_friendly"
    return {
        "fable_specific": "comp_overtrigger", "genuinely_sensitive": "comp_sensitive",
        "ambiguous": "comp_ambiguous", "control_error": "comp_triggered", "n/a": "comp_triggered",
    }.get(calibration, "comp_triggered")


def _error_row(project: Path, err: str) -> dict:
    return {
        "project": str(project), "verdict": "error", "recommendation": S.RECOMMENDATIONS["error"],
        "mode": "arch", "components": "", "fable_bare_tripped": None, "fable_full_tripped": None,
        "opus_tripped": None, "sonnet_tripped": None, "calibration": "n/a",
        "adjudicator_verdict": None, "adjudicator_score": None, "error": err,
        "_backend": "arch", "_mode": "arch", "_components": [],
    }


def triage_arch(backend, project: Path, args) -> dict:
    if not hasattr(backend, "recon"):
        return _error_row(project, "arch_requires_cli_backend")
    rep = getattr(args, "repeat", 1)

    recon = backend.recon(project, RECON_SCHEMA)
    if not recon or not isinstance(recon.get("components"), list) or not recon["components"]:
        return _error_row(project, "recon_failed")

    summ = backend.summarize_arch(recon, SUMMARY_SCHEMA)
    arch_by_name: dict[str, str] = {}
    if summ and isinstance(summ.get("components"), list):
        for c in summ["components"]:
            if c.get("name"):
                arch_by_name[c["name"]] = c.get("arch_md") or ""

    comps: list[dict] = []          # per-component detail (private)
    for rc in recon["components"]:
        name = rc.get("name") or "component"
        arch_md = arch_by_name.get(name) or rc.get("arch") or ""
        if not arch_md.strip():
            comps.append({"name": name, "verdict": "comp_error", "trip_fraction": None,
                          "calibration": "n/a", "category": None, "error": "empty_arch"})
            continue
        payload = IMPROVE.format(arch=arch_md)
        fb = S.repeat_probe(backend, S.FABLE_MODEL, payload, getattr(args, "fable_effort", "low"),
                            rep, text=True)

        controls: dict[str, S.ProbeResult] = {}
        if not args.no_controls and fb.tripped:
            for model, effort in S.CONTROL_MODELS:
                short = "opus" if "opus" in model else "sonnet"
                controls[short] = S.repeat_probe(backend, model, payload, effort, rep, text=True)
        calibration = S.calibrate(controls) if controls else ("n/a" if not fb.tripped else "control_error")
        cv = _component_verdict(fb, calibration)
        comps.append({"name": name, "verdict": cv, "trip_fraction": fb.trip_fraction,
                      "calibration": calibration, "category": fb.category, "error": fb.error,
                      "_arch": arch_md})

    return _rollup(project, comps, backend, args)


def _rollup(project: Path, comps: list[dict], backend, args) -> dict:
    errored = [c for c in comps if c["verdict"] == "comp_error"]
    real = [c for c in comps if c["verdict"] != "comp_error"]
    capped = any((c.get("error") or "").startswith("fable_usage_capped") for c in comps)

    # Don't call a project friendly if we never actually probed most of it (usage cap,
    # transient errors). That's "incomplete", not a clean bill of health.
    if capped or (errored and len(errored) * 2 >= len(comps)):
        project_verdict = "incomplete"
    elif real and all(c["verdict"] == "comp_friendly" for c in real):
        project_verdict = "fable_friendly"
    else:
        worst = max(comps, key=lambda c: _RANK.get(c["verdict"], 0))
        project_verdict = _PROJ.get(worst["verdict"], "error")

    def frac(c):
        return f"({c['trip_fraction']:.2f})" if c.get("trip_fraction") not in (None, 0.0) else ""
    breakdown = "; ".join(f"{c['name']}={c['verdict'].replace('comp_', '')}{frac(c)}" for c in comps)

    tripping = [c for c in comps if c["verdict"] not in ("comp_friendly", "comp_error")]
    if project_verdict == "incomplete":
        why = "Fable usage cap" if capped else "errors"
        recommendation = (f"Incomplete — {len(errored)}/{len(comps)} components didn't finish ({why}). "
                          f"Re-run with --only-errors once your Fable quota resets; don't trust the "
                          f"per-component verdicts yet.")
    elif project_verdict == "fable_friendly":
        note = (f" ({len(errored)} component(s) errored — re-run --only-errors to confirm)"
                if errored else "")
        recommendation = "Build on Fable — every probed component cooperates." + note
    elif tripping:
        parts = ", ".join(f"{c['name']} ({c['verdict'].replace('comp_', '')})" for c in tripping)
        recommendation = (f"Fable balks on: {parts}. Use Opus for those components; the rest is "
                          f"fine on Fable.")
    else:
        recommendation = S.RECOMMENDATIONS.get(project_verdict, "")

    # optional Opus adjudication on the first ambiguous component
    adj = None
    if args.adjudicate and project_verdict.endswith("_ambiguous"):
        amb = next((c for c in comps if c["verdict"] == "comp_ambiguous" and c.get("_arch")), None)
        if amb:
            adj = S.adjudicate(backend, amb["_arch"])

    worst_cal = next((c["calibration"] for c in sorted(
        comps, key=lambda c: _RANK.get(c["verdict"], 0), reverse=True)), "n/a")
    err = next((c["error"] for c in comps if c.get("error")), "") or ""
    public_comps = [{k: v for k, v in c.items() if k != "_arch"} for c in comps]

    return {
        "project": str(project), "verdict": project_verdict, "recommendation": recommendation,
        "mode": "arch", "components": breakdown,
        "fable_bare_tripped": None, "fable_full_tripped": None,
        "opus_tripped": None, "sonnet_tripped": None, "calibration": worst_cal,
        "adjudicator_verdict": (adj or {}).get("verdict"),
        "adjudicator_score": (adj or {}).get("score"),
        "error": err,
        # private (JSONL only) — categories live here, never in the shareable CSV
        "_backend": type(backend).__name__, "_mode": "arch",
        "_components": public_comps, "_recon_components": len(comps),
        "_adjudicator_reasoning": (adj or {}).get("reasoning"),
    }
