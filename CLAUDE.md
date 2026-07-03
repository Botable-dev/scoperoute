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

## Layout

```
.claude-plugin/{plugin.json, marketplace.json}   # plugin + self-hosted marketplace metadata
skills/scoperoute/
  SKILL.md                                        # user-invocable: true → /scoperoute
  scripts/scoperoute.py                           # Phase 1 engine + backends + summary-mode orchestration
  scripts/archprobe.py                            # --probe arch: no-trim recon->summary->per-component probe
  scripts/estimate.py                             # --estimate: pre-run cost/size calculator
  scripts/fable_watch.py                          # Phase 2 live fallback monitor
  scripts/transcript.py                           # shared metadata-only transcript reader
  references/{interpreting-results, how-it-works, claude-session-sync-README}.md
PRD_scoperoute.md · README.md · LICENSE · .gitignore
```

## Run / setup

```bash
# default CLI backend — free Fable via the Claude Code subscription, no API key
python skills/scoperoute/scripts/scoperoute.py --root ~/dev --jobs 4
python skills/scoperoute/scripts/scoperoute.py --projects ~/dev/a ~/dev/b   # fast smoke test
# API backend — cleanest raw-refusal signal; --batch = 50% off (needs `pip install anthropic` + API Fable)
python skills/scoperoute/scripts/scoperoute.py --root ~/dev --api --batch --adjudicate
```

No lint/test tooling. The engine is stdlib-only in CLI mode; `--api`/`--batch` import `anthropic`
lazily. There are deterministic tests worth re-creating when you change logic (see Verification below).

## Architecture

**Flow (`triage_project`):** `collect_context` builds `bare` (code) and `full` (+`CLAUDE.md`/`.claude`)
→ `probe(FABLE)` on each → `classify_fable` (4 buckets: `fable_friendly`/`config_triggered`/
`code_triggered`/`error`) → controls (Opus 4.8 @ high, Sonnet 5 @ low) **only on the variant that
tripped** → `calibrate` (`fable_specific`/`genuinely_sensitive`/`ambiguous`) → `combine` into 6 final
verdicts (`*_overtrigger` vs `*_sensitive`). Optional `--adjudicate` = Opus 4.8 structured tie-break on
`*_ambiguous`. `--repeat N` = majority vote via `repeat_probe` (adds a `trip_fraction`).

**Two probe modes (`--probe`):** `summary` (default) is the above — cheap, but samples code within
`--max-context-chars` (a compromise). `arch` (`archprobe.py`, CLI only) is the **no-trim** path:
`CLIBackend.recon` (Sonnet 5 agentic, reads files itself, retried) → `summarize_arch` (Opus) →
per-component `probe_text` on an "improve architecture" task → per-component verdicts rolled up to the
project (`archprobe._rollup`). `estimate.py` (`--estimate`) prices any run before it starts and is the
guard that keeps "no trim" from meaning "read 49M tokens" (it skips data/generated/vendored/oversize
files via `is_source_file`). Char-truncation is treated as an anti-pattern — see `references/how-it-works.md`.

**Two backends (`--api` toggles):**
- `CLIBackend` (default): `claude -p --model … --output-format json --session-id <uuid>` from a **neutral
  empty cwd** (so the project's `CLAUDE.md` is never auto-loaded — bare/full is controlled only by
  `collect_context`; do **not** add `--bare`, it forces API-key auth). Refusal/fallback is read from the
  pinned transcript via `transcript.last_served_turn`: a `refusal` turn, or a served family ≠ Fable.
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

Fable 5 `claude-fable-5` · Opus 4.8 `claude-opus-4-8` · Sonnet 5 `claude-sonnet-5`. When touching the
Anthropic SDK calls, load the `claude-api` skill first (effort = `output_config.effort`; refusal
semantics; Batch/structured-output shapes).

## Verification (re-create when changing logic)

- `transcript.py`: replay the real refusal record and a dated subagent from `~/.claude/projects` (assert
  `category`, `<synthetic>` exclusion, prefix-match). Metadata only.
- Engine: deterministic `classify_fable`/`calibrate`/`combine` table + an FR4 check that the shareable CSV
  contains no category/reasoning; resume `load_done` + `--only-errors`. No model calls.
- Live smoke: `--projects <one-with-CLAUDE.md> <one-plain>` in each backend; confirm they agree and no
  category leaks.
