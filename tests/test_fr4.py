#!/usr/bin/env python3
"""Structural FR4 (Fable rec #4b) + run-metadata (rec #4) tests. No model calls.
Run: python tests/test_fr4.py

Asserts the shareable CSV projection can never carry a category / reasoning / served
model / run-meta / underscore field, even on a fully-populated record with real
private data — the guarantee is by construction (allowlist), not by convention."""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "scoperoute" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import scoperoute as S   # noqa: E402


def _full_row():
    bare = S.ProbeResult("claude-fable-5", True, "cyber_offensive", "claude-opus-4-8", None)
    full = S.ProbeResult("claude-fable-5", True, "cyber_offensive", "claude-opus-4-8", None)
    controls = {"opus": S.ProbeResult("claude-opus-4-8", False, None, "claude-opus-4-8", None),
                "sonnet": S.ProbeResult("claude-sonnet-5", False, None, "claude-sonnet-5", None)}
    row = S.build_row(Path("/x/proj"), bare, full, "code_triggered", controls,
                      "fable_specific", "code_overtrigger", None, object())
    return row


def test_private_present_in_full_row():
    row = _full_row()
    # the private category IS retained in the full record (it goes to the gitignored JSONL)
    assert row["_bare_category"] == "cyber_offensive"
    assert row["_served"]["fable_bare"] == "claude-opus-4-8"


def test_sharable_projection_drops_everything_private():
    row = _full_row()
    sr = S.sharable_row(row)
    # no underscore field survives
    assert not any(k.startswith("_") for k in sr), sr
    # no private VALUE survives (category, served model)
    blob = repr(sr)
    assert "cyber_offensive" not in blob
    assert "claude-opus-4-8" not in blob   # served-model divergence is private
    # but the useful, shareable fields are there
    assert sr["verdict"] == "code_overtrigger"
    assert sr["recommendation"] and "Opus" in sr["recommendation"]


def test_run_metadata_stamp_is_private():
    row = _full_row()
    meta = {"probe_mode": "code", "repeat": 3, "fable_effort": "low",
            "workdir": "/tmp/scoperoute-work-abc"}
    S.stamp_row(row, meta)
    assert row["schema_version"] == S.SCHEMA_VERSION
    assert row["_run"]["workdir"] == "/tmp/scoperoute-work-abc"
    assert len(row["_idempotency"]) == 16
    # none of it leaks into the shareable projection
    sr = S.sharable_row(row)
    assert "schema_version" not in sr and "_run" not in sr and "_idempotency" not in sr
    assert "/tmp/scoperoute-work-abc" not in repr(sr)


def test_allowlist_has_no_private_field():
    assert not any(f.startswith("_") for f in S.CSV_FIELDS)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("OK — FR4 structural + run-metadata green")
