#!/usr/bin/env python3
"""Single stage-graph: estimator and executor agree on the pipeline (arch-review TP3,
Fable rec #5). Deterministic — no model calls."""

import sys
import tempfile
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills/scoperoute/scripts"))
import backends as B      # noqa: E402
import estimate as E      # noqa: E402
import models as M        # noqa: E402
import scoperoute as S    # noqa: E402


def _fixture_project(tmp: Path) -> Path:
    proj = tmp / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "app.py").write_text("def main():\n    return 42\n" * 50)
    return proj


def test_estimate_matches_declared_stages():
    with tempfile.TemporaryDirectory() as td:
        proj = _fixture_project(Path(td))
        for mode in M.MODES:
            est = E.estimate_project(proj, repeat=2, mode=mode)
            priced = {s.stage for s in est.stages}
            declared = {s.name for s in M.stages_for(mode)}
            assert priced == declared, f"{mode}: priced {priced} != declared {declared}"
            # `when` semantics also come from the table (controls are conditional)
            for s in est.stages:
                table_when = next(t.when for t in M.STAGES if t.name == s.stage)
                assert s.when == table_when, (mode, s.stage)
    print("ok estimate==declared per mode")


def test_run_meta_stamps_declared_stages():
    cli = B.CLIBackend()
    for mode in M.MODES:
        args = Namespace(evaluate=False, probe=mode, fable_effort="low",
                         repeat=1, adjudicate=False)
        meta = S.build_run_meta(cli, args)
        assert meta["stages"] == [s.name for s in M.stages_for(mode)], mode
        assert meta["workdir"] == str(cli.workdir)      # TP4: via the property, stamped
    # evaluate mode probes nothing from the stage graph
    meta = S.build_run_meta(cli, Namespace(evaluate=True, probe="code", fable_effort=None,
                                           repeat=1, adjudicate=False))
    assert meta["stages"] == []
    print("ok run-meta stamps stages")


def test_stage_table_shape():
    names = [s.name for s in M.STAGES]
    assert names == ["recon", "summary", "curate", "probe", "controls"]
    assert all(set(s.modes) <= set(M.MODES) for s in M.STAGES)
    # curate is the code-mode delta; summary mode is probe+controls only
    assert [s.name for s in M.stages_for("summary")] == ["probe", "controls"]
    assert "curate" in {s.name for s in M.stages_for("code")}
    assert "curate" not in {s.name for s in M.stages_for("arch")}
    print("ok table shape")


if __name__ == "__main__":
    test_stage_table_shape()
    test_estimate_matches_declared_stages()
    test_run_meta_stamps_declared_stages()
    print("test_stages: all ok")
