---
name: scoperoute
description: >
  Decide which local git projects to build with Claude Fable 5 vs keep on Opus 4.8, and what it costs.
  Distills each repo (Sonnet 5 recon → Opus 4.8 summary), probes Fable 5 per component with an "improve
  the architecture" task, and reports per-component verdicts. Also estimates the run's tokens/$ per part
  and — via CodexBar — as a % of the user's Claude plan (Pro/Max/Team), and says what to run first.
  Use when the user asks which projects to point Fable at, says Fable keeps refusing / falling back to
  Opus, asks "what should I build with free Fable", or wants a cost/subscription estimate for a triage.
  Read-only and benign; detects and routes, never bypasses the classifier. By HubLab.ai.
version: 0.2.0
license: MIT
user-invocable: true
allowed-tools: Bash, Read
---

# scoperoute — which projects to build with Claude Fable 5, and what it costs

Fable 5's safety classifier reads the **context** of a request and can refuse benign work on some
projects, silently falling back to Opus. scoperoute distills each project and probes Fable per component
to tell you where it cooperates — and prices the run before you spend anything. It only detects and
routes; for a genuinely sensitive project the answer is Opus, never coaxing Fable.

`SR="${CLAUDE_PLUGIN_ROOT}/skills/scoperoute/scripts/scoperoute.py"`

## The flow (follow this order)

1. **Estimate first — always.** The default `arch` mode reads whole codebases, so show the cost before
   running:
   ```bash
   python "$SR" --root <path> --repeat 3 --estimate
   ```
   This prints tokens/$ **per part** (Sonnet recon → Opus summary → Fable probe → controls) and, when
   CodexBar is available, the user's real tier + current window usage + spend, the run as a **% of their
   plan**, and a **"run these first"** ranking.

2. **Get the tier if it wasn't detected.** If the Subscription block shows no detected plan (CodexBar
   absent or not logged in), **ask the user** with AskUserQuestion: *Pro (~$20) / Max 5× (~$100) /
   Max 20× (~$200) / Team (~$30/seat)* — then re-run the estimate with `--tier {pro,max5,max20,team}`
   (or `--plan-usd N`) so the % is real.

3. **Present the plan.** Show the per-part cost, the % of their subscription, and what to run first
   (cheapest → dearest; flag the 1–2 cost-dominant projects). Let the user choose scope.

4. **Run the triage — but it's gated.** A bare run **does not spend Fable**: it lists the exact
   projects it would probe, the current Fable-quota %, and the cost, then **stops**. Show that to the
   user, get their explicit OK on the projects and spend, then add `--yes`:
   ```bash
   python "$SR" --root <path> --repeat 3                    # lists projects + cost, STOPS (no Fable)
   python "$SR" --root <path> --repeat 3 --jobs 4 --yes     # runs it (arch default), after approval
   python "$SR" --projects <a> <b> --yes                    # a quick, approved subset
   ```
   Fable quota is the scarce resource (a 16-project × repeat-3 sweep can use ~a third of the weekly Fable
   window), so **always confirm before `--yes`**. Resumable — it appends one line per finished project and
   skips completed ones (`--refresh` to redo, `--only-errors` to retry).

5. **Read results.** Present each project's **verdict** and **recommendation** (never the raw category),
   including the per-component breakdown. Point to `references/interpreting-results.md`.

## Reading the verdicts

| verdict | meaning | do |
|---|---|---|
| `fable_friendly` | every component cooperates | build on Fable |
| `code_overtrigger` | Fable balks on some component(s), but Opus+Sonnet answer → benign | use Opus for those; rest on Fable |
| `code_sensitive` | genuinely sensitive to every model | Opus, with care |
| `*_ambiguous` | controls split | `--adjudicate`, or review |

The `components` column names which parts trip, e.g. `frontend=friendly; backend=sensitive`.

## Prerequisites

- **Default (CLI mode):** just Claude Code — probes go through `claude -p` (free Fable via the
  subscription). No API key.
- **CodexBar (optional, for the real % of plan):** the `codexbar` CLI (a public fork,
  github.com/konon4/CodexBar). scoperoute auto-discovers it and degrades to asking the tier if it's
  missing, config-broken, or not logged in.
- **`--api` mode** (clean raw-refusal signal, `summary` probe, `--batch` 50% off): `pip install anthropic`
  + API Fable access.
- **Fable needs ≥30-day data retention** — ZDR → 400 on every request; check this before the API key.

## Invariants (do not break)

- **Metadata only; categories private.** Reports carry verdicts, recommendations, per-component flags —
  never code. The shareable CSV never contains refusal categories; those stay in the gitignored JSONL
  (opt-in `.private.csv` via `--show-categories`). Never emit a refusal's explanation URL. Redact
  CodexBar identities.
- **Fail toward Opus, never coax.** `*_sensitive` → Opus.
