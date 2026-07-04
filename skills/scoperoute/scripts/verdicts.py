#!/usr/bin/env python3
"""verdicts.py — the pure, model-free verdict core.

Fable's own review of scoperoute (see _fable/): "The heart of the system —
classify_fable -> calibrate -> combine -> six verdicts — is pure decision logic, but
it lives in the same script as sampling, backend dispatch, CLI parsing, and report
writing. Pull it into a module that takes plain result records in and emits verdict
records out, with zero imports from the backend layer."

That is exactly this file. It imports nothing from scoperoute (no cycle) and touches
no model. The functions are duck-typed on three fields — `.tripped` (True/False/None),
`.error` (str|None), `.category` (str|None) — so they can be table-tested with trivial
fakes and no model calls. scoperoute.py imports these names back, so archprobe's
`S.calibrate` / `S.RECOMMENDATIONS` keep resolving unchanged.

Stdlib only.
"""
from __future__ import annotations

from typing import Protocol


class Probe(Protocol):
    """The minimal surface the verdict logic reads off a probe result."""
    tripped: bool | None      # True = refused / fell back; False = clean; None = error
    error: str | None
    category: str | None


# ---------------------------------------------------------------- classify / calibrate / combine

def classify_fable(bare: Probe, full: Probe) -> str:
    """Bare (code only) + full (code + CLAUDE.md/.claude) Fable probes -> which surface
    trips. config_triggered = only the config-carrying variant trips; code_triggered =
    the bare code trips (config can't be the cause)."""
    if bare.error or full.error:
        return "error"
    if not bare.tripped and not full.tripped:
        return "fable_friendly"
    if full.tripped and not bare.tripped:
        return "config_triggered"
    if bare.tripped:
        return "code_triggered"
    return "error"


def calibrate(controls: dict) -> str:
    """Given the controls (Opus/Sonnet) run on the tripped variant, why did Fable refuse?
    controls maps short-name -> probe result."""
    if not controls:
        return "n/a"
    if any(c.error for c in controls.values()):
        return "control_error"
    refused = [c.tripped for c in controls.values()]
    if not any(refused):
        return "fable_specific"        # controls answered -> Fable over-triggered (benign)
    if all(refused):
        return "genuinely_sensitive"   # every model refuses -> legitimately sensitive
    return "ambiguous"                  # split control signal


def combine(fable_verdict: str, calibration: str) -> str:
    """Fold the Fable bucket + control calibration into one of the six final verdicts."""
    if fable_verdict == "fable_friendly":
        return "fable_friendly"
    if fable_verdict == "error":
        return "error"
    prefix = "config" if fable_verdict == "config_triggered" else "code"
    mapping = {
        "fable_specific": f"{prefix}_overtrigger",
        "genuinely_sensitive": f"{prefix}_sensitive",
        "ambiguous": f"{prefix}_ambiguous",
        "control_error": fable_verdict,      # keep the raw bucket; controls failed
        "n/a": fable_verdict,                # controls disabled
    }
    return mapping.get(calibration, fable_verdict)


# ---------------------------------------------------------------- user-facing strings (FR4-safe)

# final_verdict -> recommendation. FR4-safe: no category, no reasoning, no code.
RECOMMENDATIONS = {
    "fable_friendly":
        "Build on Fable — it cooperates here.",
    "config_overtrigger":
        "Your CLAUDE.md/.claude wording trips Fable but the project is benign "
        "(Opus + Sonnet answered fine). Reword the config and re-run, or accept the "
        "Opus fallback; confirm with `claude --safe-mode`.",
    "config_sensitive":
        "The config text reads as sensitive to every model, not just Fable. "
        "Rework the wording or keep this project on Opus.",
    "config_ambiguous":
        "Config trips Fable and the controls split. Run with --adjudicate, or review "
        "the config wording by hand.",
    "code_overtrigger":
        "Fable over-triggers on this benign code (Opus + Sonnet handle it). Use Opus "
        "here — don't fight the classifier.",
    "code_sensitive":
        "Genuinely sensitive: every model declines the benign probe. Opus, with care.",
    "code_ambiguous":
        "The code trips Fable and the controls split. Run with --adjudicate, or eyeball it.",
    "config_triggered":
        "Config trips Fable; controls errored, so the cause is unconfirmed. Re-run with "
        "--only-errors, or treat as Opus for now.",
    "code_triggered":
        "Code trips Fable; controls errored, so the cause is unconfirmed. Re-run with "
        "--only-errors, or treat as Opus for now.",
    "error":
        "Probe did not complete. In --api mode, confirm Fable access AND that your org "
        "has >=30-day data retention (ZDR -> 400 on every request). In --cli mode, "
        "confirm `claude` is logged in and the model id is available.",
}

# verdict -> console tag: an explicit word (no cryptic ?/INC/FIX), padded to a fixed 6-char
# width for column alignment. The word names the action: Fable (build on Fable) / Opus
# (route to Opus) / Reword (rework CLAUDE.md wording) / Review (controls split — look closer)
# / Re-run (unconfirmed or capped — re-probe) / Error. Colored by scoperoute.mark_tag.
MARK = {"fable_friendly": "Fable ", "config_overtrigger": "Reword", "config_sensitive": "Opus  ",
        "config_ambiguous": "Review", "code_overtrigger": "Opus  ", "code_sensitive": "Opus  ",
        "code_ambiguous": "Review", "config_triggered": "Re-run", "code_triggered": "Re-run",
        "incomplete": "Re-run", "predicted_safe": "Fable ", "predicted_risky": "Opus  ",
        "predicted_review": "Review", "error": "Error "}
