# How scoperoute works

## The probe

For each project, scoperoute builds two context strings and sends each as one message to Fable 5,
followed by a single benign instruction:

> In one short sentence, summarize what this project is. Just the summary, nothing else.

- **bare** = `# PROJECT` header + README + `git status` + file tree + a source sample (budgeted by
  `--max-context-chars`). No config.
- **full** = bare + `CLAUDE.md` + every `.claude/**/*.md`.

The instruction is deliberately benign and non-security, so **any refusal is attributable to the project
context, not the request** (PRD FR1). Comparing bare vs full localizes the trigger to the config or the
code.

## Why no fallbacks

Fable's refusal is opt-out at the API: a plain request with **no `fallbacks` parameter** stops on a
refusal (`stop_reason == "refusal"`). scoperoute never sends `fallbacks` — the whole point is to observe
the raw refusal, not a rescued Opus answer. (Adding `fallbacks` would mask exactly what we're measuring.)

## Why controls

Fable's classifier is the aggressive one; Opus 4.8 and Sonnet 5 rarely refuse a benign summarize request.
Running the identical probe through them on the tripped variant tells us whether Fable's refusal is
Fable-specific over-caution (controls answer → benign) or a genuine sensitivity every model shares
(controls refuse). Controls run **only on the variant that tripped**, which halves control spend and adds
no value on `fable_friendly` projects.

## Two backends

- **CLI (default)** — each probe is `claude -p --model claude-fable-5 --output-format json
  --session-id <uuid> "<context + instruction>"`, run from a neutral empty working directory (so the
  *project's* `CLAUDE.md` is never auto-loaded — bare vs full is controlled entirely by the context we
  build). The outcome is read from the pinned session transcript: a `stop_reason == "refusal"` turn, or a
  served model that differs from Fable (a built-in fallback that masked the refusal) — either way, Fable
  tripped. Free Fable via your subscription; no API key.
- **API (`--api`)** — `messages.create(model="claude-fable-5", max_tokens=16, output_config={effort})`,
  no `thinking`, no `fallbacks`. The refusal is read directly from `stop_reason`/`stop_details.category`.
  Cleanest signal; `--batch` submits the probes through the Batch API at 50 % off (a refusal comes back
  as a *succeeded* result there, not an error).

## Privacy

- Reports are **metadata only** — verdict, recommendation, per-model tripped flags. No prompt content.
- The refusal **category** (`cyber`/`bio`/`reasoning_extraction`/…) is private: only the local
  `scoperoute_report.jsonl` and the opt-in `.private.csv` carry it. The shareable `.csv` never does.
- A refusal's `explanation` field carries a live, tokened URL — scoperoute never reads or emits it, only
  the category label.

## Troubleshooting an all-error run

1. **Fable data retention** — Fable requires ≥30-day retention. Under zero / under-30-day retention every
   request 400s. Check this **before** the API key.
2. **CLI mode** — confirm `claude` is logged in (`claude` works interactively) and the model id resolves.
3. **API mode** — confirm Fable API access; if `ANTHROPIC_API_KEY` is unset, `ant auth status` shows
   whether a login profile is active.
