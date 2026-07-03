---
name: scoperoute
description: >
  Triage local git projects to decide which to build with Claude Fable 5 versus keep on Opus 4.8.
  Sends one benign "summarize this project" probe through Fable plus Opus 4.8 and Sonnet 5 controls to
  detect and explain Fable safety-classifier refusals: Fable-specific over-trigger (benign — use Opus)
  vs genuinely sensitive (Opus, with care) vs config wording that trips the classifier (fixable).
  Use when the user asks which projects to point Fable 5 at, says Fable keeps refusing or falling back
  to Opus, wants to scope free-window Fable usage across many repos, or asks "what should I build with
  Fable". Read-only and benign; it detects and routes, and never bypasses the classifier.
version: 0.1.0
license: MIT
user-invocable: true
allowed-tools: Bash, Read
---

# scoperoute — which projects to build with Claude Fable 5

Claude Fable 5's safety classifier reads the **context** of a request (`CLAUDE.md`, the file tree, git
status, source) and can refuse benign work on some projects, silently falling back to Opus. scoperoute
sends **one benign, non-security probe per project** — "in one sentence, summarize what this project is"
— and records whether Fable refuses, then cross-checks with **Opus 4.8** and **Sonnet 5** controls to
explain *why*. It only detects and routes; for a genuinely sensitive project the right answer is Opus,
never coaxing Fable.

## When to run it

- "Which of my projects should I point (free) Fable 5 at?"
- "Fable keeps refusing / falling back to Opus on some repos — which ones, and why?"
- "I have N repos and want to know where Fable will actually cooperate."

## How it works (one line each)

- Two probes per project: **bare** (code tree + source sample) and **full** (bare + `CLAUDE.md` +
  `.claude/**/*.md`). Comparing them separates a *config* trigger from a *code* trigger.
- **Controls run only on the variant that tripped.** If Opus + Sonnet answer the same benign ask, Fable
  over-triggered (benign); if every model refuses, it's genuinely sensitive.
- It never sends a `fallbacks` param — the point is to see the raw refusal, not a rescued Opus answer.

Details: `references/how-it-works.md`. Verdict table: `references/interpreting-results.md`.

## Prerequisites

- **Default (CLI mode):** just Claude Code — probes go through `claude -p`, which uses your
  subscription (free Fable during the window). No API key.
- **`--api` mode:** `pip install anthropic` and Anthropic API Fable access. Cleanest raw-refusal signal;
  enables `--batch` (50 % off). If `ANTHROPIC_API_KEY` is unset, `anthropic.Anthropic()` also picks up an
  `ant auth login` profile — don't hardcode a key.
- **Fable needs ≥30-day data retention.** Under zero / under-30-day retention every Fable request 400s —
  that, not the API key, is the usual cause of an all-error run.

## Run it

```bash
SR="${CLAUDE_PLUGIN_ROOT}/skills/scoperoute/scripts/scoperoute.py"

# ALWAYS estimate first — shows cost/tokens per project and for the whole root, no probes:
python "$SR" --root ~/dev --probe arch --repeat 3 --estimate

# fast first pass (cheap benign-summarize probe, parallel):
python "$SR" --root ~/dev --jobs 4

# accurate, no-trim, per-component (recon->summary->improve-architecture), majority vote:
python "$SR" --root ~/dev --probe arch --repeat 3

# clean raw-refusal signal via the API (summary mode), 50%-off bulk:
python "$SR" --root ~/dev --api --batch --adjudicate
```

- `--probe arch` distills instead of truncating (no `--max-context-chars` limit) and gives per-component
  verdicts; `--repeat N` turns a coin-flip into a trip fraction. Both are worth it for real decisions.
- `--estimate` is important before any `--root` sweep — the arch pipeline reads whole codebases.

Resumable: it appends one line per finished project to `scoperoute_report.jsonl` and skips completed
projects on re-run (`--refresh` to redo, `--only-errors` to retry failures).

## Read the results

Show the user the **verdict** and **recommendation** (never the raw category). Point them at
`references/interpreting-results.md`. The short version:

| verdict | what it means | do |
|---|---|---|
| `fable_friendly` | clean on Fable | build on Fable |
| `config_overtrigger` | config wording trips Fable, project is benign | reword `CLAUDE.md`/`.claude`, re-run |
| `config_sensitive` | config reads sensitive to every model | rework wording / keep on Opus |
| `code_overtrigger` | Fable over-triggers on benign code | use Opus here |
| `code_sensitive` | genuinely sensitive to every model | Opus, with care |
| `*_ambiguous` | controls split | `--adjudicate`, or review |

## Invariants (do not break)

- **Metadata only.** Reports carry verdicts, recommendations, and per-model tripped flags — never prompt
  content. Refusal **categories are private**: they go only to the local `scoperoute_report.jsonl` (and,
  opt-in, `scoperoute_report.private.csv` via `--show-categories`), never the shareable `.csv`. Never
  surface a refusal's explanation URL.
- **Fail toward Opus, never coax.** `code_sensitive` / `config_sensitive` go to Opus; the tool does not
  try to get Fable to comply.

## Cost

Benign ~16-token probes; controls run only on tripped variants; effort is `low` for controls. `--api
--batch` is 50 % off. In CLI mode everything is covered by your Claude Code subscription.
