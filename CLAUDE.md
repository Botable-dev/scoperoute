# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`scoperoute` — a Claude Code plugin/skill that triages which local git projects are worth building with
**Claude Fable 5** vs which get routed to **Opus 4.8**. It sends one benign probe per project, detects
whether Fable's safety classifier refuses (falling back to Opus), and cross-checks with Opus/Sonnet
controls to explain *why*. `PRD_scoperoute.md` is the source of truth for requirements (FR1–FR4), the
context buckets, and the invariants; read it before extending.

## Status

- **Phase 1 (shipped): multi-model triage engine + `/scoperoute` skill.** `skills/scoperoute/scripts/
  scoperoute.py` + `transcript.py`, packaged as a plugin (`.claude-plugin/`, `skills/scoperoute/`).
- **Phase 2 (built): `fable_watch.py`** — live per-turn Fable→Opus fallback monitor on `transcript.py`
  (tails main + subagent sidecars; metadata-only event log; fallback-rate/streak; §4.6 reclass hint).
  Verified via unit fixture + real-session replay. `references/claude-session-sync-README.md` is the
  vendored domain-model reference for the on-disk session layout.
- **Phase 3 (built): real-code probe + fable-docs + engine refactor (v0.3.0).** `--probe code` (the new
  DEFAULT) augments each component's Opus summary with **Opus-curated real code** before the Fable probe
  (guardrails fire on implementations, not just prose). After every arch/code run, the **fable-docs**
  stage (`fabledocs.py`) writes each probed repo a `_fable/fable-architecture-review.md` from that run's
  own transcripts (opt out `--no-fable-docs`). The engine was refactored per scoperoute's own Fable
  review (`_fable/`): pure `verdicts.py`, one `models.py` table, structural-FR4 `sharable_row`, schema-v2
  run metadata (records the probe workdir), and a fail-loud transcript verdict (`judge_turn`). Tests in
  `tests/` are deterministic (no model calls).

## Layout

```
.claude-plugin/{plugin.json, marketplace.json}   # plugin + self-hosted marketplace metadata
skills/scoperoute/
  SKILL.md                                        # user-invocable: true → /scoperoute
  scripts/scoperoute.py                           # engine + backends + orchestration; --probe code is DEFAULT
  scripts/archprobe.py                            # --probe arch|code: recon->summary(->curate real code)->per-component probe
  scripts/fabledocs.py                            # DEFAULT post-stage: write <repo>/_fable/ review from the run's transcripts
  scripts/verdicts.py                             # pure, model-free verdict core (classify/calibrate/combine + strings)
  scripts/models.py                               # ONE declarative model/role/pricing table (Fable/Opus/Sonnet)
  scripts/estimate.py                             # --estimate: pre-run calculator, per-stage (Sonnet/Opus/Fable) + per-model
  scripts/subscription.py                         # CodexBar tier/usage/spend + run $ and % of plan + "run first"
  scripts/fable_watch.py                          # Phase 2 live fallback monitor
  scripts/transcript.py                           # shared metadata-only transcript reader
  references/{interpreting-results, how-it-works, claude-session-sync-README}.md
tests/                                            # deterministic, no model calls (verdicts/fr4/codeprobe/fabledocs/faillloud)
PRD_scoperoute.md · README.md · LICENSE · .gitignore
```

## Run / setup

```bash
SR=skills/scoperoute/scripts/scoperoute.py
python $SR --root ~/dev --repeat 3 --estimate            # ALWAYS first: tokens/$ per part + % of plan
python $SR --root ~/dev --repeat 3 --jobs 4 --yes        # code (default): recon→summary→curate real code→Fable
python $SR --root ~/dev --probe arch --yes               # cheaper prose-only screen (no code injected)
python $SR --projects ~/dev/a ~/dev/b --yes              # quick subset
python $SR --root ~/dev --api --batch --adjudicate        # API summary mode; --batch = 50% off
python tests/test_*.py                                    # deterministic tests (no model calls)
```
`--estimate` prints the per-stage (Sonnet recon → Opus summary → Fable probe) + per-model breakdown and a
Subscription block: real tier/usage/spend from CodexBar (`subscription.py`), run $, **% of plan**, and a
"run first" ranking. `--tier {pro,max5,max20,team}` / `--plan-usd N` when CodexBar can't detect it.

**Approval gate:** a bare run (no `--yes`) lists the projects it would probe + the Fable-quota % + cost
and **stops without spending Fable**; `--yes`/`-y` actually runs it. **No artificial caps:** context is
read in full (no `--max-context-chars` cap by default) and `claude -p` calls have no timeout by default
(char-trimming is an anti-pattern; opt back in with `--max-context-chars N` / `--probe-timeout N`).
`estimate.py`'s `MAX_FILE_BYTES`/`RECON_INPUT_CAP` are estimation-only heuristics — they never truncate
triage data.

No lint/test tooling. The engine is stdlib-only in CLI mode; `--api`/`--batch` import `anthropic`
lazily. There are deterministic tests worth re-creating when you change logic (see Verification below).

## Architecture

**Verdict core is pure (`verdicts.py`, Fable rec #1):** `classify_fable` (4 buckets) / `calibrate`
(`fable_specific`/`genuinely_sensitive`/`ambiguous`) / `combine` (→ 6 final verdicts `*_overtrigger` vs
`*_sensitive`) + `RECOMMENDATIONS`/`MARK` are model-free and table-tested. `scoperoute.py` imports them
back (so `S.calibrate` etc. still resolve for `archprobe`). Model ids/roles/pricing are one table
(`models.py`, rec #6). `--adjudicate` = Opus structured tie-break on `*_ambiguous`; `--repeat N` =
majority vote via `repeat_probe` (adds `trip_fraction`).

**Flow (`triage_project`, summary mode):** `collect_context` builds `bare` (code) and `full`
(+`CLAUDE.md`/`.claude`) → `probe(FABLE)` on each → `classify_fable` → controls **only on the tripped
variant** → `calibrate` → `combine`.

**Three probe modes (`--probe`):** **`code` is the DEFAULT** (resolves to `summary` under `--api`, which
can't do agentic recon). All in `archprobe.py`, CLI only, **no char-truncation**:
`CLIBackend.recon` (Sonnet 5 agentic, reads files itself) → `summarize_arch` (Opus). Then:
- **`code`** (default): `curate_code` (Opus reads the component's REAL source — bounded input, whole
  functions — and returns high-safety-signal excerpts) → Fable probed on **arch summary + real code**
  via `IMPROVE_CODE`. Guardrails can miss benign prose yet fire on the implementation; this matches what
  Fable sees when you build.
- **`arch`**: the cheaper prose-only screen — Fable probed on the Opus summary alone (`IMPROVE`).
- **`summary`**: legacy bare/full benign-summarize probe.
Per-component verdicts roll up to the project (`archprobe._rollup`). Real code stays in the transcript,
never the JSONL/CSV. `estimate.py` (`--estimate`) prices any run first (per-stage incl. the `code` curate
stage), skipping data/generated/vendored/oversize via `is_source_file`; char-truncation is an anti-pattern.

**Default post-stage — fable-docs (`fabledocs.py`):** after an arch/code run, writes each probed repo a
`_fable/fable-architecture-review.md` from that run's pinned transcripts (found via the schema-v2
`_run.workdir` stamp). Attribution comes from the run's own records → project/component/path; path-
clustered, header-demoted, FR4-safe. Opt out `--no-fable-docs`.

**Two backends (`--api` toggles):**
- `CLIBackend` (default): `claude -p --model … --output-format json --session-id <uuid>` from a **neutral
  empty cwd** (so the project's `CLAUDE.md` is never auto-loaded — bare/full is controlled only by
  `collect_context`; do **not** add `--bare`, it forces API-key auth). The one backend instance keeps one
  `_workdir` (all this run's probe transcripts land there — that's what fable-docs reads). Outcome is read
  from the pinned transcript by `judge_turn` (Fable rec #3, **fail-loud**): a `refusal` turn → tripped; a
  served family ≠ Fable → tripped (masked fallback); a positively-matched family → clean; an
  **unidentifiable** served model → `error`, never a silent clean.
- `APIBackend` (`--api`): `messages.create`, **no `thinking`, no `fallbacks`** (raw refusal via
  `read_refusal` → `stop_reason=="refusal"` + `stop_details.category`). `--batch` = two-phase Batch API;
  a refusal there is a **`succeeded`** result, not an error.

**`transcript.py`** (shared, stdlib): `model_family` (alias-resolve, strip `[1m]`, prefix-match dated
ids, `<synthetic>`→None), `encode_project_path` (non-alnum→`-`, lossy), `session_dir`/`latest_session`/
`session_files` (main + `subagents/**/agent-*.jsonl`), `parse_line`→metadata-only `Turn`, `is_fallback`.

**Resume/output:** append-only `scoperoute_report.jsonl` is the source of truth (skip completed on
re-run; `--refresh`/`--only-errors`). Reports render from a fixed schema constant (no `rows[0].keys()`).

## Invariants (from the PRD — do not break)

- **Metadata only; categories are private (FR4).** Shareable `scoperoute_report.csv` = verdict +
  recommendation + tripped flags, **no categories/reasoning**. Categories live only in the gitignored
  `.jsonl` (and opt-in `.private.csv` via `--show-categories`). Never read/emit a refusal's `explanation`
  (it carries a live tokened URL).
- **The probe never sends `fallbacks`** — a rescued Opus answer would mask the refusal we measure.
- **Fail toward Opus, never coax.** `*_sensitive` → Opus.
- **Fable needs ≥30-day retention** (ZDR → 400 every request); the error path names this first.

## Model IDs

Single table in **`models.py`** (rec #6): Fable 5 `claude-fable-5` · Opus 4.8 `claude-opus-4-8` ·
Sonnet 5 `claude-sonnet-5` (+ roles: probe/recon/summary/adjudicator/controls, and pricing). Change ids
or prices there — `scoperoute.py` and `estimate.py` both import it, so they can't drift. When touching the
Anthropic SDK calls, load the `claude-api` skill first (effort = `output_config.effort`; refusal
semantics; Batch/structured-output shapes).

## Verification (`tests/`, re-create when changing logic — deterministic, no model calls)

- `test_verdicts.py`: full `classify_fable`/`calibrate`/`combine` truth table + the models/verdicts
  extraction wiring (`S.*` names still resolve for archprobe).
- `test_fr4.py`: `sharable_row` drops every category/reasoning/served-model/run-meta field on a fully
  populated record (structural FR4, rec #4b).
- `test_codeprobe.py`: `--probe code` injects Opus-curated real code via `IMPROVE_CODE`; `arch` stays
  prose-only; real code never reaches the JSONL row.
- `test_fabledocs.py`: the default fable-docs stage attributes reviews to the right project/component and
  never emits a refusal category/URL (synthetic transcripts; no `~/.claude` writes).
- `test_faillloud.py`: `judge_turn` — refusal / clean / masked fallback / **drift→error** / dated snapshot.
- Live smoke: `--projects <one-with-CLAUDE.md> <one-plain>` in each backend; confirm they agree and no
  category leaks.
