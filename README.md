# scoperoute

**Point free Claude Fable 5 at the projects it will actually cooperate on.**

Fable 5 is the most capable model — and free through the current Claude Code window (through
**2026-07-07** at the time of writing). But its safety classifier reads the *context* of your request
(`CLAUDE.md`, the file tree, the code) and quietly refuses benign work on some projects, falling back to
Opus. So "just use Fable everywhere" wastes the window on repos where it keeps bailing.

`scoperoute` tells you, **per project**, whether Fable will cooperate — and when a refusal is Fable being
over-cautious (use Opus, it's fine) versus the project being genuinely sensitive.

```
$ /scoperoute   # or: python skills/scoperoute/scripts/scoperoute.py --root ~/dev

PROJECT            VERDICT             RECOMMENDATION
api-gateway        fable_friendly      Build on Fable — it cooperates here.
netsec-scanner     code_overtrigger    Benign; Fable over-triggers. Use Opus here.
crispr-notes       code_sensitive      Genuinely sensitive. Opus, with care.
my-agent           config_overtrigger  CLAUDE.md trips Fable — reword & re-run.
```

## Install

Two commands (works with just Claude Code — no API key):

```bash
claude plugin marketplace add konon4/scoperoute
claude plugin install scoperoute@konn4-tools
```

Then run `/scoperoute` in Claude Code, or call the engine directly:

```bash
python skills/scoperoute/scripts/scoperoute.py --root ~/dev --jobs 4
```

## How it works

- Two probes per project — **bare** (code only) and **full** (code + `CLAUDE.md` + `.claude/`) — with one
  benign question: *"in one sentence, summarize what this project is."* Comparing them separates a
  **config** trigger from a **code** trigger.
- If Fable refuses, the same benign probe runs through **Opus 4.8** and **Sonnet 5** as controls. Both
  answer → Fable over-triggered (benign). Every model refuses → genuinely sensitive.
- It never sends a `fallbacks` param — the point is to see the *raw* refusal, not a rescued Opus answer.

Six verdicts: `fable_friendly` · `config_overtrigger` · `config_sensitive` · `code_overtrigger` ·
`code_sensitive` · `*_ambiguous`. Full table: [`interpreting-results.md`](skills/scoperoute/references/interpreting-results.md).
Method + privacy: [`how-it-works.md`](skills/scoperoute/references/how-it-works.md).

## Two modes

| | how | needs | signal |
|---|---|---|---|
| **CLI** (default) | `claude -p --model claude-fable-5` | just Claude Code (free Fable) | refusal read from the session transcript |
| **API** (`--api`) | Anthropic SDK | `pip install anthropic` + API Fable | raw `stop_reason=="refusal"`; `--batch` = 50 % off |

**Probe depth.** `--probe summary` (default) is a cheap one-liner probe. `--probe arch` is the accurate,
**no-trim** path: Sonnet reads the whole codebase itself, Opus writes a per-component architecture
summary, and Fable is asked to *improve* each component — so you get per-component verdicts
(`frontend=friendly; backend=sensitive`). Add `--repeat 3` to turn a borderline coin-flip into a trip
fraction. Always `--estimate` first — it prices the run per project and for the whole root before you
spend anything.

## Privacy

Metadata only. Reports carry verdicts, recommendations, and per-model tripped flags — never prompt
content. Refusal **categories stay local** (`scoperoute_report.jsonl`, gitignored); the shareable
`scoperoute_report.csv` never contains them. A refusal's explanation URL is never read or emitted.

## Fine print

- **Fable needs ≥30-day data retention.** Under zero / under-30-day retention every Fable request 400s —
  that's the usual cause of an all-error run, not your API key.
- It only detects and routes. For a genuinely sensitive project the answer is Opus — scoperoute never
  tries to coax Fable into complying.

## Roadmap

- **Phase 2 — live monitor** (`fable_watch.py`): tail your active Claude Code session and flag each turn
  where a Fable session fell back to Opus, with per-project fallback-rate and streak alerts.
- **Workflow** (`scoperoute.workflow.js`): fan out one clean-context probe per project in parallel with a
  resumable journal.

## License

MIT.
