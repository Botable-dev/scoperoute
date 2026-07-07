#!/usr/bin/env python3
"""Backend contract parity + workdir seam canary (arch-review TP1/TP4, Fable rec #2).
Deterministic — no model calls, no network, no ~/.claude writes."""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills/scoperoute/scripts"))
import backends as B      # noqa: E402
import scoperoute as S    # noqa: E402
import transcript as T    # noqa: E402

PROBE_METHODS = ("probe_text", "probe", "recon", "summarize_arch", "judge")
KNOWN_CAPS = {"probe", "recon", "batch", "workdir"}


def test_contract():
    # Both backends implement the one Backend ABC…
    assert issubclass(B.CLIBackend, B.Backend)
    assert issubclass(B.APIBackend, B.Backend)
    # …with identical probe-surface signatures (the parity canary: a method added or
    # reshaped on one backend but not the other fails here, not at runtime).
    for name in PROBE_METHODS:
        sig_cli = inspect.signature(getattr(B.CLIBackend, name))
        sig_api = inspect.signature(getattr(B.APIBackend, name))
        assert sig_cli == sig_api, f"{name}: {sig_cli} != {sig_api}"
    # Capabilities are declared, non-empty, and use only known names.
    for cls in (B.CLIBackend, B.APIBackend):
        assert cls.capabilities and cls.capabilities <= KNOWN_CAPS, cls
    # The gates main() relies on:
    assert "recon" in B.CLIBackend.capabilities      # arch/code probe modes
    assert "workdir" in B.CLIBackend.capabilities    # fable-docs stage
    assert "batch" in B.APIBackend.capabilities
    assert "recon" not in B.APIBackend.capabilities
    # scoperoute re-exports keep the public S.* surface stable.
    for name in ("Backend", "CLIBackend", "APIBackend", "ProbeResult", "judge_turn",
                 "repeat_probe", "read_refusal", "BENIGN_INSTRUCTION"):
        assert getattr(S, name) is getattr(B, name), name
    print("ok contract")


def test_workdir_seam():
    # Base contract: no on-disk transcripts unless a backend declares one.
    assert B.Backend.workdir.fget(object.__new__(B.APIBackend)) is None
    cli = B.CLIBackend()
    assert cli.workdir == cli._workdir and cli.workdir.is_dir()
    # Layout canary (TP4): the workdir->transcript-dir mapping the CLI probe and
    # fable-docs both depend on. If Claude Code's session layout changes shape,
    # this fails in tests, not silently at run time.
    sdir = T.session_dir(Path("/x/y-z"))
    assert sdir.name == T.encode_project_path(Path("/x/y-z"))
    assert "-x-y-z" in str(sdir) and ".claude" in str(sdir)
    print("ok workdir seam")


if __name__ == "__main__":
    test_contract()
    test_workdir_seam()
    print("test_backend_parity: all ok")
