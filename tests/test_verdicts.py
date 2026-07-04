#!/usr/bin/env python3
"""Deterministic table test for the pure verdict core (verdicts.py) + the
models.py/verdicts.py extraction wiring. No model calls. Run: python tests/test_verdicts.py

Re-create/extend this whenever you touch classify_fable / calibrate / combine or the
model table (per CLAUDE.md → Verification, and Fable's own rec #1: a pure core exists
so every verdict path is table-testable)."""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "scoperoute" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import models as M       # noqa: E402
import verdicts as V     # noqa: E402
import scoperoute as S   # noqa: E402
import estimate as E     # noqa: E402
import archprobe         # noqa: E402,F401  — must still resolve S.* names


class P:
    """Minimal fake with the three fields the pure core reads."""
    def __init__(self, tripped=None, error=None, category=None):
        self.tripped, self.error, self.category = tripped, error, category


def test_wiring():
    assert S.FABLE_MODEL == "claude-fable-5"
    assert S.CONTROL_MODELS == [("claude-opus-4-8", "high"), ("claude-sonnet-5", "low")]
    assert S.ADJUDICATOR_MODEL == "claude-opus-4-8"
    assert S.calibrate is V.calibrate and S.RECOMMENDATIONS is V.RECOMMENDATIONS
    assert S.classify_fable is V.classify_fable and S.combine is V.combine
    assert E.PRICING is M.PRICING and M.PRICING["claude-fable-5"] == (10.0, 50.0)
    for name in ("ProbeResult", "repeat_probe", "FABLE_MODEL", "CONTROL_MODELS",
                 "calibrate", "RECOMMENDATIONS", "adjudicate"):
        assert hasattr(S, name), f"archprobe needs S.{name}"


def test_classify_fable():
    clean, trip, err = P(False), P(True), P(None, "boom")
    assert V.classify_fable(clean, clean) == "fable_friendly"
    assert V.classify_fable(clean, trip) == "config_triggered"
    assert V.classify_fable(trip, trip) == "code_triggered"
    assert V.classify_fable(trip, clean) == "code_triggered"
    assert V.classify_fable(err, clean) == "error"


def test_calibrate():
    clean, trip, err = P(False), P(True), P(None, "boom")
    assert V.calibrate({}) == "n/a"
    assert V.calibrate({"opus": clean, "sonnet": clean}) == "fable_specific"
    assert V.calibrate({"opus": trip, "sonnet": trip}) == "genuinely_sensitive"
    assert V.calibrate({"opus": trip, "sonnet": clean}) == "ambiguous"
    assert V.calibrate({"opus": err, "sonnet": clean}) == "control_error"


def test_combine_and_coverage():
    assert V.combine("fable_friendly", "n/a") == "fable_friendly"
    assert V.combine("code_triggered", "fable_specific") == "code_overtrigger"
    assert V.combine("code_triggered", "genuinely_sensitive") == "code_sensitive"
    assert V.combine("config_triggered", "fable_specific") == "config_overtrigger"
    assert V.combine("config_triggered", "genuinely_sensitive") == "config_sensitive"
    assert V.combine("code_triggered", "ambiguous") == "code_ambiguous"
    assert V.combine("config_triggered", "ambiguous") == "config_ambiguous"
    assert V.combine("code_triggered", "control_error") == "code_triggered"
    assert V.combine("code_triggered", "n/a") == "code_triggered"
    assert V.combine("error", "n/a") == "error"
    # every verdict combine can emit has a recommendation + a MARK
    for fv in ("fable_friendly", "code_triggered", "config_triggered", "error"):
        for cal in ("n/a", "fable_specific", "genuinely_sensitive", "ambiguous", "control_error"):
            v = V.combine(fv, cal)
            assert v in V.RECOMMENDATIONS, f"missing recommendation for {v}"
            assert v in V.MARK, f"missing MARK for {v}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("OK — verdicts/models table green")
