# Interpreting scoperoute results

Each project gets a **verdict** and a plain-English **recommendation**. Verdicts come from two signals:

1. **Fable bucket** — from comparing the `bare` probe (code only) with the `full` probe (code +
   `CLAUDE.md` + `.claude/**/*.md`):
   - clean in both → `fable_friendly`
   - `full` refuses, `bare` clean → `config_triggered` (your config wording is what trips Fable)
   - `bare` refuses → `code_triggered` (the code/tree itself trips Fable)
2. **Calibration** — controls (Opus 4.8, Sonnet 5) run on the *variant that tripped*:
   - both answer → `fable_specific` (Fable over-triggered; the material is benign)
   - both refuse → `genuinely_sensitive` (every model declines — legitimately sensitive)
   - split → `ambiguous`

Combined:

| verdict | Fable | controls | meaning | what to do |
|---|---|---|---|---|
| `fable_friendly` | clean both | — | Fable cooperates | **Build on Fable.** |
| `config_overtrigger` | config trips | answer | benign config wording trips Fable | Reword the sensitive-looking phrasing in `CLAUDE.md`/`.claude` and re-run. A common one: `reasoning_extraction` fires on "explain your reasoning" — remove it. Confirm with `claude --safe-mode`. |
| `config_sensitive` | config trips | refuse | config reads sensitive to every model | Rework the wording, or keep the project on Opus. |
| `config_ambiguous` | config trips | split | mixed control signal | Re-run with `--adjudicate` (Opus 4.8 structured tie-break), or read the config by hand. |
| `code_overtrigger` | code trips | answer | Fable over-triggers on benign code | **Use Opus here** — don't fight the classifier. |
| `code_sensitive` | code trips | refuse | genuinely sensitive | Opus, with care. |
| `code_ambiguous` | code trips | split | mixed control signal | `--adjudicate`, or eyeball. |
| `config_triggered` / `code_triggered` | trips | errored | controls failed, cause unconfirmed | `--only-errors` to retry; treat as Opus meanwhile. |
| `error` | — | — | probe didn't complete | See how-it-works.md → troubleshooting (Fable retention first). |

## The point

- `*_overtrigger` = **benign**. Opus is a perfectly good home; you just learned Fable won't cooperate.
- `*_sensitive` = **legitimately Opus-only**. Every model declines the benign probe.
- `fable_friendly` = **go** — this is where free Fable pays off.

## Where the detail lives

- Shareable `scoperoute_report.csv` — verdicts + recommendations + per-model tripped flags. No categories.
- Local `scoperoute_report.jsonl` (gitignored) — full record incl. refusal `category`, served models, and
  any adjudicator reasoning. Use this to decide *what* config text to reword.
- `--show-categories` also writes `scoperoute_report.private.csv` (gitignored) with the `*_category`
  columns.
