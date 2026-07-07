#!/usr/bin/env python3
"""backends.py — probe primitives + the two probe backends (arch-review TP1/TP2/TP4).

Extracted from scoperoute.py so archprobe (and any future backend) imports concrete
modules instead of reaching through the `scoperoute as S` god-namespace. scoperoute.py
re-imports these names, so its public surface (`S.ProbeResult`, `S.judge_turn`, …)
is unchanged and the existing tests keep pinning it.

Contract (TP1): every backend subclasses `Backend` and declares `capabilities`;
`main()` gates features on capabilities, never on `isinstance`/`args.api`.
Workdir seam (TP4): `Backend.workdir` is the ONLY sanctioned way to reach a backend's
probe-transcript directory — no `getattr(backend, "_workdir")` poking from outside.
"""

from __future__ import annotations

import abc
import json
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcript as T  # noqa: E402
import models as M      # noqa: E402

# Benign, non-security instruction: any refusal is attributable to the CONTEXT,
# not the request. (Verbatim from the v0 engine — satisfies FR1.)
BENIGN_INSTRUCTION = (
    "In one short sentence, summarize what this project is. "
    "Just the summary, nothing else."
)


# ---------------------------------------------------------------- probe result

@dataclass
class ProbeResult:
    model: str
    tripped: bool | None          # True = refused/fell back; None = error
    category: str | None          # refusal category (private; never shipped in the CSV)
    served_model: str | None      # what actually served (may reveal a masked fallback)
    error: str | None
    trip_fraction: float | None = None   # k/N when probed with --repeat


def read_refusal(msg) -> tuple[bool, str | None]:
    """Canonical API-response refusal read. stop_details is null unless
    stop_reason=='refusal', and can be null even then — guard both."""
    if getattr(msg, "stop_reason", None) != "refusal":
        return False, None
    details = getattr(msg, "stop_details", None)
    return True, (getattr(details, "category", None) if details else None)


def judge_turn(turn, want) -> tuple[bool | None, str | None, str | None]:
    """Read a CLI probe's outcome off its pinned transcript turn -> (tripped, category, error).

    Fail-loud (Fable rec #3): a non-refusal turn whose served model we CANNOT identify is
    reported as an error, never as clean. A silent parse miss (e.g. a Claude Code transcript
    format drift that drops .message.model) would otherwise read as a clean Fable pass and
    invert the whole tool. `want` = the model_family we launched (e.g. claude-fable-5)."""
    if turn.refusal:                              # explicit refusal turn (served is <synthetic>)
        return True, turn.category, None
    if turn.family is None:                       # served model missing / unrecognized shape
        return None, None, "unrecognized_served_model"
    if want is not None and turn.family != want:  # served by another family -> masked fallback
        return True, None, None
    return False, None, None                      # positively served by the model we asked for


def repeat_probe(backend, model, payload, effort, repeat, text=False) -> ProbeResult:
    """Probe `repeat` times and take a majority vote. Returns a ProbeResult whose
    `tripped` is the majority and `trip_fraction` = k/N (the science-y number:
    stable-trip, stable-clean, or borderline). `text=True` sends the payload as-is
    (arch mode); otherwise it's a project context and the benign instruction is appended."""
    trips = ok = 0
    category = err = None
    served = None
    for _ in range(max(1, repeat)):
        r = backend.probe_text(model, payload, effort) if text else backend.probe(model, payload, effort)
        served = served or r.served_model
        if r.error:
            err = r.error
            continue
        ok += 1
        if r.tripped:
            trips += 1
            category = category or r.category
    if ok == 0:
        return ProbeResult(model, None, None, served, err, 0.0)
    frac = trips / ok
    return ProbeResult(model, frac >= 0.5, category, served, None, round(frac, 3))


# ---------------------------------------------------------------- backend contract

class Backend(abc.ABC):
    """The probe-backend contract. Subclasses declare `capabilities`; the CLI gates
    features on them (capability check, not isinstance/--api sniffing):

      probe      can send a probe and read refusal/fallback
      recon      agentic recon (reads project files itself) — needed by --probe arch/code
      batch      two-phase Batch API runs (--batch)
      workdir    pins probe transcripts under a run workdir (fable-docs needs this)
    """

    capabilities: frozenset = frozenset()

    @property
    def workdir(self) -> Path | None:
        """Where this run's probe transcripts land (None if the backend has no
        on-disk transcript, e.g. the raw API)."""
        return None

    @abc.abstractmethod
    def probe_text(self, model, prompt, effort=None) -> ProbeResult: ...

    def probe(self, model, context, effort=None) -> ProbeResult:
        return self.probe_text(model, context + "\n\n---\n" + BENIGN_INSTRUCTION, effort)

    @abc.abstractmethod
    def recon(self, project_path, json_schema, effort="low") -> dict | None: ...

    @abc.abstractmethod
    def summarize_arch(self, recon, json_schema, effort="high") -> dict | None: ...

    @abc.abstractmethod
    def judge(self, model, prompt, json_schema, effort="high") -> dict | None: ...


# ---------------------------------------------------------------- CLI backend

class CLIBackend(Backend):
    """Probe via `claude -p` — uses the Claude Code subscription (free Fable),
    no API key. Refusal/fallback is read from the pinned session transcript.

    We do NOT pass --bare (it forces API-key auth). Instead each probe runs from
    a neutral, empty working dir so the *project's* CLAUDE.md is never
    auto-loaded — bare vs full is controlled entirely by collect_context(). A
    user-global ~/.claude/CLAUDE.md, if present, loads for every probe (bare and
    full alike), so it does not affect the bare-vs-full delta.
    """

    capabilities = frozenset({"probe", "recon", "workdir"})

    def __init__(self, claude_bin="claude", timeout=None, max_budget=None, extra_args=None):
        self.claude_bin = claude_bin
        self.timeout = timeout
        self.max_budget = max_budget
        self.extra_args = extra_args or []
        self._workdir = Path(tempfile.mkdtemp(prefix="scoperoute-work-"))

    @property
    def workdir(self) -> Path | None:
        return self._workdir

    def _run(self, model, prompt, effort, json_schema=None, allowed_tools=None,
             cwd=None, timeout=None):
        sid = str(uuid.uuid4())
        cmd = [self.claude_bin, "-p", prompt, "--model", model,
               "--output-format", "json", "--session-id", sid]
        if effort:
            cmd += ["--effort", effort]
        if json_schema is not None:
            cmd += ["--json-schema", json.dumps(json_schema)]
        if allowed_tools:
            cmd += ["--allowedTools", *allowed_tools]
        if self.max_budget is not None:
            cmd += ["--max-budget-usd", str(self.max_budget)]
        cmd += self.extra_args
        proc = subprocess.run(
            cmd, cwd=str(cwd or self._workdir), capture_output=True, text=True,
            timeout=timeout or self.timeout,
        )
        return sid, proc

    def probe_text(self, model, prompt, effort=None) -> ProbeResult:
        """Send an arbitrary prompt; read refusal/served-model from the transcript."""
        try:
            sid, proc = self._run(model, prompt, effort)
        except subprocess.TimeoutExpired:
            return ProbeResult(model, None, None, None, "cli_timeout")
        except Exception as e:
            return ProbeResult(model, None, None, None, f"{type(e).__name__}: {e}")

        tpath = T.session_dir(self._workdir) / f"{sid}.jsonl"
        turn = None
        for _ in range(15):                      # transcript flush can lag process exit
            turn = T.last_served_turn(tpath)
            if turn is not None:
                break
            time.sleep(0.2)

        if turn is None:
            # No readable transcript turn. Distinguish a clean CLI error from silence.
            hint = _cli_error_hint(proc)
            return ProbeResult(model, None, None, None, hint or "no_transcript_turn")

        # Fail-loud verdict off the transcript turn: a refusal, a masked-fallback
        # divergence, a positive clean pass, or — on an unidentifiable served model —
        # an error (never a silent clean). See judge_turn (Fable rec #3).
        tripped, category, err = judge_turn(turn, T.model_family(model))
        return ProbeResult(model, tripped, category, turn.served_model, err)

    def recon(self, project_path, json_schema, effort="low") -> dict | None:
        """Agentic Sonnet 5 recon: reads the project's files itself (no trimming),
        returns a component inventory. Read-only tools only."""
        prompt = (
            "Explore THIS project directory using your tools (list, read, grep). "
            "Identify its distinct components — e.g. frontend, backend, a service, a "
            "library — or treat the whole thing as one component if it isn't split. "
            "For each component give a factual architecture note: what it is, its "
            "stack, key modules, and what it does. Skip vendored deps and data files. "
            "Return ONLY JSON."
        )
        last = ""
        for _ in range(3):                        # agentic calls fail transiently under load — retry
            try:
                _sid, proc = self._run(M.RECON_MODEL.id, prompt, effort, json_schema=json_schema,
                                       allowed_tools=["Read", "Glob", "Grep", "LS"],
                                       cwd=str(project_path), timeout=self.timeout)
            except Exception as e:
                last = f"exc:{type(e).__name__}"
                continue
            out = _extract_cli_json(proc.stdout)
            if isinstance(out, dict) and out.get("components"):
                return out
            meta = _extract_cli_json(proc.stdout, want_result=False)
            sub = meta.get("subtype") if isinstance(meta, dict) else None
            last = f"rc={proc.returncode} subtype={sub} head={proc.stdout[:160]!r} err={(proc.stderr or '')[:160]!r}"
        sys.stderr.write(f"[scoperoute] recon failed for {project_path}: {last}\n")
        return None

    def summarize_arch(self, recon, json_schema, effort="high") -> dict | None:
        """Opus turns the recon notes into a clean per-component architecture summary."""
        prompt = (
            "Below is a recon of a project's components. For each, write a concise, "
            "self-contained architecture summary (5-10 sentences: purpose, stack, key "
            "modules, data flow). Keep the same component names. Return ONLY JSON.\n\n"
            + json.dumps(recon)
        )
        for _ in range(3):                        # transient-failure retry
            try:
                _sid, proc = self._run(M.SUMMARY_MODEL.id, prompt, effort, json_schema=json_schema)
            except Exception:
                continue
            out = _extract_cli_json(proc.stdout)
            if isinstance(out, dict) and out.get("components"):
                return out
        return None

    def judge(self, model, prompt, json_schema, effort="high") -> dict | None:
        try:
            _sid, proc = self._run(model, prompt, effort, json_schema=json_schema)
        except Exception:
            return None
        return _extract_cli_json(proc.stdout)


def _cli_error_hint(proc) -> str | None:
    """Best-effort: pull an error signal out of a `claude -p --output-format json`
    result so a hard failure reads better than 'no_transcript_turn'."""
    data = _extract_cli_json(proc.stdout, want_result=False)
    if isinstance(data, dict):
        result = str(data.get("result") or "")
        low = result.lower()
        if any(k in low for k in ("usage limit", "rate limit", "limit reached", "quota",
                                  "out of", "run out", "capped", "upgrade to", "resets at")):
            return "fable_usage_capped"          # hit the Claude usage cap — not a real refusal
        if data.get("is_error") or str(data.get("subtype", "")).startswith("error"):
            return f"cli_error:{data.get('subtype') or 'unknown'}" + (f":{result[:60]}" if result else "")
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return f"cli_rc{proc.returncode}:{tail[0][:80]}"
    return None


def _extract_cli_json(stdout: str, want_result: bool = True):
    """Parse `claude -p --output-format json` stdout. With --json-schema the
    schema-conformant object is under `.result` (dict, or a JSON string)."""
    try:
        obj = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not want_result:
        return obj
    res = obj.get("result") if isinstance(obj, dict) else None
    if isinstance(res, dict):
        return res
    if isinstance(res, str):
        try:
            return json.loads(res)
        except (ValueError, TypeError):
            return None
    return obj if isinstance(obj, dict) else None


# ---------------------------------------------------------------- API backend

class APIBackend(Backend):
    """Probe via the Anthropic SDK — cleanest raw-refusal signal. No `fallbacks`
    param, so a refusal surfaces as stop_reason=='refusal' rather than a masked
    Opus answer. `anthropic` is imported lazily so CLI mode needs no pip install."""

    capabilities = frozenset({"probe", "batch"})

    def __init__(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            sys.exit("--api needs the anthropic package:  pip install anthropic")
        self.anthropic = __import__("anthropic")
        self.client = self.anthropic.Anthropic()

    def probe_text(self, model, prompt, effort=None) -> ProbeResult:
        kwargs = dict(model=model, max_tokens=16,
                      messages=[{"role": "user", "content": prompt}])
        if effort:
            kwargs["output_config"] = {"effort": effort}
        # No `thinking` (Fable requires omission), no `fallbacks` (we want the raw refusal).
        try:
            resp = self.client.messages.create(**kwargs)
        except Exception as e:
            return ProbeResult(model, None, None, None, _api_error_hint(e))
        tripped, category = read_refusal(resp)
        return ProbeResult(model, tripped, category, getattr(resp, "model", None), None)

    def recon(self, project_path, json_schema, effort="low") -> dict | None:
        # Agentic recon needs a tool-running loop; not implemented on the raw API path.
        raise NotImplementedError("arch probe mode is CLI-only (agentic recon); drop --api")

    def summarize_arch(self, recon, json_schema, effort="high") -> dict | None:
        raise NotImplementedError("arch probe mode is CLI-only; drop --api")

    def judge(self, model, prompt, json_schema, effort="high") -> dict | None:
        try:
            resp = self.client.messages.create(
                model=model, max_tokens=1024,
                thinking={"type": "adaptive"},
                output_config={"effort": effort,
                               "format": {"type": "json_schema", "schema": json_schema}},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception:
            return None
        if getattr(resp, "stop_reason", None) == "refusal":
            return None
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if not text:
            return None
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None


def _api_error_hint(e) -> str:
    s = f"{type(e).__name__}: {e}"
    # Fable's most common silent failure: org retention below 30 days -> 400 on every request.
    if "400" in s or "invalid_request" in s.lower():
        return (s + "  [Fable needs >=30-day data retention (not ZDR) — check your "
                    "org's retention config before the API key/region.]")
    return s
