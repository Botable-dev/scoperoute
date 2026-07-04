#!/usr/bin/env python3
"""Interactive-wizard helpers (scoperoute.py): the safety property is that non-TTY / EOF
NEVER blocks and always takes the default — so headless, piped, and inside-Claude-Code runs
can't hang. Run: python tests/test_interactive.py  (no model calls; runs non-TTY.)"""
import builtins
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "scoperoute" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import scoperoute as S   # noqa: E402


def test_parse_selection():
    assert S._parse_selection("1,3,5-8", 10) == [0, 2, 4, 5, 6, 7]
    assert S._parse_selection("", 4) == [0, 1, 2, 3]
    assert S._parse_selection("all", 4) == [0, 1, 2, 3]
    assert S._parse_selection("2-4", 5) == [1, 2, 3]
    assert S._parse_selection("9,99", 5) == []          # out-of-range ignored
    assert S._parse_selection("3, 1 ,2", 5) == [0, 1, 2]  # spaces + dedupe/sort


def test_defaults_when_non_tty():
    # the test process is not a TTY -> _interactive() is False -> every prompt returns default
    assert S._interactive() is False
    assert S._confirm("go?", default=True) is True
    assert S._confirm("go?", default=False) is False    # never silently 'yes'
    assert S._choose("pick", ["a", "b", "c"], default=2) == 2
    assert S._ask("path", "DEF") == "DEF"


def test_eof_returns_default(monkeypatch):
    # force 'interactive', then make input() raise EOFError (closed stdin) -> defaults, no crash
    monkeypatch.setattr(S, "_interactive", lambda: True)
    def _eof(*a, **k):
        raise EOFError
    monkeypatch.setattr(builtins, "input", _eof)
    assert S._confirm("go?", default=False) is False
    assert S._choose("pick", ["a", "b"], default=1) == 1
    assert S._ask("x", "DEF") == "DEF"


def test_help_does_not_crash():
    # a crashing --help (e.g. an unescaped % in an argparse help string) must never ship
    import subprocess
    for script in ("scoperoute.py", "estimate.py"):
        r = subprocess.run([sys.executable, str(SCRIPTS / script), "--help"],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"{script} --help exited {r.returncode}: {r.stderr[-300:]}"
        assert "usage:" in r.stdout


def test_wizard_declines_when_headless(monkeypatch):
    # a full wizard run under non-TTY must reach the gate default (No) and NOT set args.yes
    from types import SimpleNamespace
    args = SimpleNamespace(root=None, projects=None, probe=None, repeat=1, tier=None,
                           plan_usd=None, no_codexbar=True, yes=False, interactive=False)
    monkeypatch.setattr(S, "find_projects", lambda root: [])   # no repos -> early, cheap exit
    assert S.interactive_wizard(args) is False
    assert args.yes is False                                    # nothing approved


# minimal monkeypatch shim so this runs without pytest
class _MP:
    def __init__(self): self._undo = []
    def setattr(self, obj, name, val):
        if isinstance(obj, str):
            mod, _, attr = obj.rpartition("."); import importlib
            obj = importlib.import_module(mod); name = attr
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, val)
    def undo(self):
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)


if __name__ == "__main__":
    import inspect
    for nm, fn in sorted(globals().items()):
        if nm.startswith("test_") and callable(fn):
            mp = _MP()
            try:
                fn(mp) if "monkeypatch" in inspect.signature(fn).parameters else fn()
                print(f"  ok  {nm}")
            finally:
                mp.undo()
    print("OK — interactive helpers green (non-TTY defaults, EOF-safe, wizard never auto-spends)")
