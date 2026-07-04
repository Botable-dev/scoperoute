#!/usr/bin/env python3
"""Fail-loud transcript verdict (Fable rec #3): judge_turn must never report a clean
Fable pass when it can't positively identify the served model. Run: python tests/test_faillloud.py"""
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "scoperoute" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import scoperoute as S   # noqa: E402
import transcript as T   # noqa: E402

WANT = T.model_family("claude-fable-5")


def _turn(**message):
    return T.parse_line(json.dumps({"type": "assistant", "message": message}))


def test_refusal():
    t = _turn(role="assistant", model="<synthetic>", stop_reason="refusal",
              stop_details={"category": "cyber_offensive"})
    assert S.judge_turn(t, WANT) == (True, "cyber_offensive", None)


def test_clean_served_by_fable():
    t = _turn(role="assistant", model="claude-fable-5", stop_reason="end_turn")
    assert S.judge_turn(t, WANT) == (False, None, None)


def test_masked_fallback_to_opus():
    t = _turn(role="assistant", model="claude-opus-4-8", stop_reason="end_turn")
    tripped, cat, err = S.judge_turn(t, WANT)
    assert tripped is True and err is None      # served-model divergence = tripped


def test_drift_missing_model_is_error_not_clean():
    # transcript-format drift: .message.model vanished. This USED to fall through to a
    # clean Fable pass — the silent inversion Fable warned about. Now it's an error.
    t = _turn(role="assistant", stop_reason="end_turn")   # no model field
    tripped, cat, err = S.judge_turn(t, WANT)
    assert tripped is None and err == "unrecognized_served_model", (tripped, err)


def test_dated_snapshot_still_clean():
    t = _turn(role="assistant", model="claude-fable-5-20260601", stop_reason="end_turn")
    assert S.judge_turn(t, WANT) == (False, None, None)   # prefix-match still resolves


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("OK — fail-loud transcript verdict green")
