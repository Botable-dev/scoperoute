#!/usr/bin/env python3
"""Deterministic test of the default fable-docs stage (fabledocs.generate) with
synthetic transcripts — NO model calls, NO writes to ~/.claude. Proves the stage reads
a run's own probe transcripts, attributes reviews to the right project/component via the
run's records, and writes a clean, FR4-safe _fable/ doc. Run: python tests/test_fabledocs.py"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "scoperoute" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fabledocs as FD   # noqa: E402
import transcript as T   # noqa: E402


def _line(**rec):
    return json.dumps(rec) + "\n"


def _summary_transcript(names_paths, arch_by_name):
    recon = {"components": [{"name": n, "path": p} for n, p in names_paths]}
    prompt = FD.SUMMARY_HEAD + " ...\n\n" + json.dumps(recon) + "\n\nReturn ONLY JSON."
    so = {"components": [{"name": n, "arch_md": a} for n, a in arch_by_name.items()]}
    return (_line(type="user", message={"role": "user", "content": prompt})
            + _line(type="assistant", message={"role": "assistant", "model": "claude-opus-4-8",
                    "content": [{"type": "tool_use", "name": "StructuredOutput", "input": so}]}))


def _improve_code_transcript(arch, code, review, model="claude-fable-5", refused=False):
    prompt = ("You are reviewing a software component and suggesting concrete improvements to its "
              "implementation. Here is the component's architecture:\n\n" + arch +
              "\n\nAnd here are real excerpts from its actual source code:\n\n" + code +
              "\n\nSuggest concrete improvements to this code and its design.")
    msg = {"role": "assistant", "model": model, "content": [{"type": "text", "text": review}]}
    if refused:
        msg["stop_reason"] = "refusal"
        msg["stop_details"] = {"category": "cyber_offensive",
                               "explanation": "https://secret.example/tok?leak=1"}
    return (_line(type="user", message={"role": "user", "content": prompt})
            + _line(type="assistant", message=msg))


def test_generate_writes_attributed_review():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        proot = tmp / "projects"
        workdir = tmp / "scoperoute-work-xyz"
        sdir = T.session_dir(workdir, proot)
        sdir.mkdir(parents=True)
        # a real project dir to receive the _fable/ doc
        proj = tmp / "myrepo"
        (proj / "src").mkdir(parents=True)

        review = ("Here are concrete improvements, most impactful first.\n\n"
                  "## 1. Extract the parser\nThe parser is entangled with I/O; split it.\n\n"
                  "## 2. Add retries\nNetwork calls need idempotent retries.") * 2
        (sdir / "s.jsonl").write_text(_summary_transcript(
            [("comp", "src")], {"comp": "ARCH_ONE about the component"}))
        (sdir / "p.jsonl").write_text(_improve_code_transcript(
            "ARCH_ONE about the component", "def handler(): ...", review))

        records = [{"project": str(proj), "_mode": "code",
                    "_components": [{"name": "comp"}]}]
        written = FD.generate(records, str(workdir), projects_root=str(proot))

        assert written == [str(proj / "_fable" / "fable-architecture-review.md")], written
        md = Path(written[0]).read_text()
        assert "## comp — `src`" in md            # attributed + path from the summary
        assert "Extract the parser" in md          # the real Fable review text
        assert "claude-fable-5" in md              # reviewer noted
        # header demotion: the review's own "## 1." became deeper, not a top-level H2
        assert "#### 1. Extract the parser" in md
        # FR4: no refusal URL / category anywhere (this one wasn't a refusal, but check)
        assert "secret.example" not in md and "cyber_offensive" not in md


def test_refusal_is_labeled_not_dumped():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        proot = tmp / "projects"
        workdir = tmp / "scoperoute-work-ref"
        sdir = T.session_dir(workdir, proot)
        sdir.mkdir(parents=True)
        proj = tmp / "repo2"
        proj.mkdir()

        # one friendly component (so the file is written) + one refused component
        (sdir / "s.jsonl").write_text(_summary_transcript(
            [("ok", "a"), ("bad", "b")],
            {"ok": "ARCH_OK", "bad": "ARCH_BAD"}))
        (sdir / "p1.jsonl").write_text(_improve_code_transcript(
            "ARCH_OK", "code", "A solid, detailed review of the ok component. " * 6))
        (sdir / "p2.jsonl").write_text(_improve_code_transcript(
            "ARCH_BAD", "code", "", refused=True))

        records = [{"project": str(proj), "_mode": "code",
                    "_components": [{"name": "ok"}, {"name": "bad"}]}]
        written = FD.generate(records, str(workdir), projects_root=str(proot))
        md = Path(written[0]).read_text()
        assert "declined" in md.lower()
        assert "secret.example" not in md          # NEVER emit the refusal URL
        assert "https://" not in md.split("scoperoute")[-1] or "secret.example" not in md


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("OK — fabledocs default stage green (no model calls, no ~/.claude writes)")
