# Architecture review — scoperoute (fable_routing) · 2026-07-07

Read-only assessment (no code changes). Method: churn/coupling map via git+grep, targeted slices only.
Reviewer: Claude Fable 5 via `/arch-review`. Companion to `_fable/fable-architecture-review.md` (probe-run review); TP1/TP3 here independently corroborate its recs #2/#5 (task #33).

> **STATUS: APPLIED (v0.3.1, same day).** All 4 refactor steps landed: `backends.py` (Backend ABC +
> capabilities + workdir property), archprobe direct imports (S.* surface 12→3), `models.STAGES`
> single stage-graph (estimate ⇄ run-meta agree by test), workdir seam formalized. New canaries:
> `tests/test_backend_parity.py`, `tests/test_stages.py`. Suite: 8/8 green.

## 1. Repo map (≤1 screen)

~3.6k LOC stdlib Python engine + deterministic tests. One package dir, flat modules:

| Module | LOC | Churn (commits) | Role |
|---|---|---|---|
| `scoperoute.py` | **1243** | **16** (top; 3× next code file) | CLI entrypoint + config + context collection + **both backends** + triage + FR4 output + batch + run loop + wizard |
| `estimate.py` | 315 | 6 | pre-run pricing, own stage list |
| `transcript.py` | 294 | 1 | on-disk session-layout reader (the shared seam) |
| `archprobe.py` | 293 | 5 | arch/code probe modes — imports `scoperoute as S` |
| `fabledocs.py` | 277 | 2 | default post-stage, reads run workdir transcripts |
| `fable_watch.py` | 251 | – | live monitor (standalone on transcript.py) |
| `subscription.py` | 233 | 2 | CodexBar tier/spend |
| `verdicts.py` / `models.py` | 122 / ~60 | 2 / 1 | **already-extracted pure cores** (prior refactor, healthy) |

Import graph: `scoperoute → {transcript, estimate, models, verdicts}` eagerly; `→ archprobe, fabledocs, subscription` lazily. **Cycle: `archprobe → scoperoute (S.*)` ⇄ `scoperoute :903 → archprobe`** — broken only by lazy import order (the file admits it in a comment). archprobe pulls **12 distinct symbols** through `S.*`, including `S.E` (estimate reached *through* scoperoute's namespace) and `S.M`.

## 2. Turning points (ranked: blast radius × reversibility cost)

### TP1 — Duck-typed backend duality, capability drift managed by scattered `args.api` gates · risk 7 × effort 4
Two classes (`CLIBackend` :253, `APIBackend` :415) share a 5-method surface (`probe_text/probe/recon/summarize_arch/judge`) **by convention only** — no ABC, no parity check. Capability differences are enforced far away in `main()`: `args.probe = "summary" if args.api` (:1133), `--batch` gate (:1135), arch/code-vs-api gate (:1137), fabledocs gate `not args.api` (:1227). **Evidence:** the capability matrix already has 3 axes (probe mode × backend × post-stage) and every new mode/backend touches `main()` + both classes + archprobe. Most expensive to reverse later: once a third backend (Agent SDK, Bedrock) or fourth probe mode lands on implicit gating, unwinding costs a `main()` rewrite. *(Independently confirms Fable's own rec #2, deferred as task #33.)*
**Design fork:** explicit `Backend` ABC with a declared `capabilities` set consumed by `main()` — vs. keep gating in `main()` and accept quadratic growth.

### TP2 — `scoperoute.py` is simultaneously CLI, orchestrator, backend impl, and shared library (accidental package root) · risk 6 × effort 5
**Evidence:** 1243 LOC / 16-commit churn (cost concentrates here); the S⇄archprobe cycle; archprobe's 12-symbol `S.*` surface (`repeat_probe, ProbeResult, adjudicate, calibrate, RECOMMENDATIONS, read_text, IGNORE_DIRS, FABLE_MODEL, CONTROL_MODELS, M, E, run`). Half of those (`M, E, calibrate, RECOMMENDATIONS`) are re-exports archprobe could import directly — the prior verdicts/models extraction did the hard part; the remaining coupling is backends + `ProbeResult` + config constants.
**Design fork:** finish the extraction (backends + ProbeResult + context into importable core modules; archprobe imports concrete modules) — vs. leave `S` as the god-namespace and keep the lazy-import cycle as a permanent load-bearing hack.

### TP3 — estimate ↔ execution stage-graph duplicated by hand · risk 6 × effort 3
The approval gate's core promise ("prices the run first, nothing spent until yes") rests on `estimate.py` staying in sync with the executor **manually**. **Evidence:** stage names are string literals in ≥3 sites in estimate.py (`:174–200`, print loop `:258`) with no executor-side counterpart to check against; execution stages live implicitly in `triage_project`/`triage_arch` control flow. When "curate" was added, estimate needed parallel edits (3 literal sites observed; historical diff not verified). Drift here silently mis-prices the gate — a trust failure in the product's differentiator. *(= Fable rec #5, task #33.)*
**Design fork:** one declarative STAGES table (name, model-role, `when`, IO heuristic) that estimate iterates and the executor validates against — vs. discipline.

### TP4 — Transcript-as-database with a leaky workdir seam · risk 5 × effort 2
Verdicts (`judge_turn` fail-loud), fabledocs, and fable_watch all depend on the **undocumented external** `~/.claude` session layout. `transcript.py` is the right seam and already exists — but it's leaky: `getattr(backend, "_workdir", None)` pokes a private attr from outside at `:866` and `:1231`, and fabledocs attribution depends on the schema-v2 `_run.workdir` stamp round-tripping. **Evidence:** the layout spec is a *vendored README* (`references/claude-session-sync-README.md`) — the tool's correctness tracks a format Anthropic can change any release.
**Design fork:** formalize `backend.workdir()` (None for API) + a layout-canary fixture test — vs. keep private-attr poking and find out at runtime.

**Explicitly NOT turning points:** append-only JSONL + schema-v2 stamping + `sharable_row` allowlist (FR4) — inspected, structurally sound, leave alone. `verdicts.py`/`models.py` are the pattern to copy, not fix.

## 3. Refactor plan (ordered; seam-first, no big-bang)

1. **Backend ABC + parity canary** *(TP1; prerequisite for step 2)*
   Smallest seam: `class Backend(ABC)` declaring the 5 methods + `capabilities: frozenset` (e.g. `{"recon","arch","batch","workdir"}`); both classes inherit, zero behavior change. Then convert the four `args.api` gates in `main()` to capability checks, one at a time. Safety net: new `tests/test_backend_parity.py` asserting identical method signatures + every `main()` gate maps to a declared capability (deterministic, no model calls — matches existing test style). Blast radius: `main()` + 2 class headers.
2. **Break the S⇄archprobe cycle** *(TP2; cheap once step 1 lands)*
   Step A (trivial, safe now): archprobe imports `models as M`, `estimate as E`, `from verdicts import calibrate, RECOMMENDATIONS` directly — removes ~half the `S.*` surface, no semantic change. Step B: move `ProbeResult`, `repeat_probe`, `judge_turn`, both backends into `backends.py`; `scoperoute.py` keeps CLI/orchestration and re-imports (existing `test_verdicts.py` already pins that `S.*` names resolve — those pins ARE the characterization test; update deliberately). Blast radius: imports only; behavior frozen by the existing deterministic suite.
3. **Single stage-graph** *(TP3; independent)*
   Declarative `STAGES` table in `models.py` (already the "one table" precedent): `(name, model_role, when, applies_to_mode)`. `estimate.py` iterates it; the executor stamps each executed stage into run-meta; a test asserts executed ⊆ declared. Do after step 2 so it lands in the right module.
4. **Formalize the workdir seam** *(TP4; independent, smallest)*
   `Backend.workdir` property (None on API); replace both `getattr(..., "_workdir")` sites; one vendored-fixture layout canary in `test_faillloud.py` so a session-layout change fails a test, not a run. Blast radius: 3 call sites.

Each step ships green on the existing suite; order 1→2 shares the backend move; 3–4 slot anywhere after.

## 4. Not inspected + confidence

- **Not inspected:** bodies of `fable_watch.py` and `subscription.py`; `CLIBackend._run` internals beyond signatures; README/SKILL docs; historical diffs (churn counts only).
- **Confidence:** HIGH on TP1/TP2 (direct grep evidence: gates, cycle, symbol surface). MEDIUM on TP3 — the stage-literal duplication is observed, but "curate required 3 parallel edits" is inferred from the 3 literal sites, not verified against the commit. MEDIUM-HIGH on TP4 (call sites verified; likelihood of an upstream layout change is a judgment call).
- Notable: TP1 and TP3 independently re-derive Fable's own review recs #2 and #5 (task #33) from the coupling data — this review corroborates that deferral list as the correct top of the backlog.
