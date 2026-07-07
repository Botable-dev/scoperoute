#!/usr/bin/env python3
"""Deterministic test of the --probe code orchestration (real-code injection) with a
fake backend — NO model calls, so it never touches Fable quota. Proves:
  - _gather_component_source reads real source (skips data/vendored), bounded;
  - code mode injects the Opus-curated code into the Fable payload via IMPROVE_CODE;
  - arch mode stays prose-only via IMPROVE;
  - the real code is kept out of the JSONL record (lives only in the transcript).
Run: python tests/test_codeprobe.py"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "scoperoute" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import scoperoute as S   # noqa: E402
import archprobe as A    # noqa: E402


class FakeBackend:
    """Records every probe payload; returns canned recon/summary/curate. Declares the
    `recon` capability so triage_arch takes the CLI path (capability check, TP1)."""
    capabilities = frozenset({"probe", "recon"})

    def __init__(self):
        self.payloads = []

    def recon(self, project, schema, effort="low"):
        return {"components": [{"name": "comp", "path": "skills/scoperoute/scripts",
                                "arch": "recon note"}]}

    def summarize_arch(self, recon, schema, effort="high"):
        return {"components": [{"name": "comp", "arch_md": "ARCH_TEXT_MARKER"}]}

    def judge(self, model, prompt, schema, effort="high"):
        # curate call -> canned code excerpt
        return {"components": [{"name": "comp", "code_excerpt": "REAL_CODE_MARKER def f(): pass"}]}

    def probe_text(self, model, prompt, effort=None):
        self.payloads.append(prompt)
        return S.ProbeResult(model, False, None, model, None)   # clean


def _args(mode):
    return SimpleNamespace(repeat=1, no_controls=True, adjudicate=False,
                           fable_effort="low", probe=mode)


def test_gather_component_source():
    src = A._gather_component_source(ROOT, "skills/scoperoute/scripts")
    assert "### FILE:" in src and "def " in src, "should read real .py source"
    assert "scoperoute.py" in src
    # bounded
    assert len(src) <= A.CURATE_INPUT_BUDGET + 50_000


def test_code_mode_injects_real_code():
    be = FakeBackend()
    row = A.triage_arch(be, ROOT, _args("code"))
    assert be.payloads, "Fable should have been probed"
    p = be.payloads[0]
    assert "REAL_CODE_MARKER" in p, "curated real code must be in the Fable payload"
    assert "ARCH_TEXT_MARKER" in p, "arch summary must also be present (augment, not replace)"
    assert p.startswith("You are reviewing a software component and suggesting concrete impro")
    assert row["mode"] == "code"
    # the real code is NOT written into the JSONL record (it lives in the transcript)
    blob = repr(row)
    assert "REAL_CODE_MARKER" not in blob, "real code must not leak into the JSONL row"
    assert "_code" not in str(row.get("_components"))


def test_arch_mode_is_prose_only():
    be = FakeBackend()
    row = A.triage_arch(be, ROOT, _args("arch"))
    p = be.payloads[0]
    assert "ARCH_TEXT_MARKER" in p
    assert "REAL_CODE_MARKER" not in p, "arch mode must not inject code"
    assert p.startswith("You are reviewing the architecture of a software component")
    assert row["mode"] == "arch"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("OK — code-injection probe green (no model calls)")
