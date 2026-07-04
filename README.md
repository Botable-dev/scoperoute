# scoperoute

**Which of your projects can you actually build with Claude Fable 5?**

Fable 5 is the strongest model, and free through the current Claude Code window. But its safety classifier
reads the *context* of your request — your `CLAUDE.md`, the file tree, the code — and quietly refuses
benign work on some projects, falling back to Opus. "Use Fable everywhere" wastes the window on repos
where it keeps bailing. scoperoute tells you, **per project and per component**, where Fable will
cooperate — and what the run will cost, in tokens, dollars, and a slice of your Claude plan.

It isn't another code reviewer — Claude Code already ships `/code-review` and `/security-review` for
finding bugs and vulnerabilities; scoperoute answers the question that comes *before* you build: which
model each repo should run on, and what that costs.

## Try it in one paste

Install the plugin (below), then paste this to Claude Code:

> install this repo: `github.com/botable/scoperoute` and evaluate the amount of tokens/$/part, ask what my
> Claude subscription is (Pro/Max/Team) to calculate my spending estimate in $ and % of the subscription,
> and tell me what to run first.

## Install

Two commands — works with just Claude Code, no API key:

```bash
claude plugin marketplace add botable/scoperoute
claude plugin install scoperoute@hublab
```

Then run `/scoperoute` in Claude Code.

## What you get

**A cost estimate before you spend anything** — tokens and dollars *per pipeline part*, plus your plan:

```
By stage (tokens / $ per part):
  recon     sonnet-5    calls=16  in=1,830,000 out=6,200   $ 3.72
  summary   opus-4-8    calls=16  in=  40,000  out=22,000  $ 0.75
  probe     fable-5     calls=48  in=  57,600  out=24,000  $ 1.78
  controls  opus+sonnet calls=?   …                        (only components that trip)

Subscription
  detected plan: Claude Max  (via CodexBar)
  this run: ~$6.25–$9.10 notional  =  6.3–9.1% of your monthly plan
  run these first (cheapest → dearest):
    $0.15  vpn      $0.33  reels      $0.75  kardan-repair …
```

**A per-component verdict** — so you keep Fable where it works and route the rest to Opus:

```
PROJECT            VERDICT             RECOMMENDATION
api-gateway        fable_friendly      Build on Fable — every component cooperates.
acme-monorepo      code_overtrigger    Fable balks on: backend (sensitive). Use Opus there; frontend is fine.
crispr-notes       code_sensitive      Genuinely sensitive. Opus, with care.
```

## How it works

The default mode is **`arch` — no trimming**. Instead of truncating your code to a byte budget (an
anti-pattern that judges a mutilated fragment), it distills:

1. **Recon** — Sonnet 5 reads the project's files *itself* and inventories its components.
2. **Summary** — Opus 4.8 writes a clean architecture summary per component.
3. **Probe** — Fable 5 is asked to *improve the architecture* of each component; a refusal (or a silent
   Opus fallback) means it won't cooperate there.
4. **Rollup** — per-component verdicts become a project verdict, naming what to keep off Fable.

If Fable refuses, the same task runs through **Opus 4.8 + Sonnet 5** controls: both answer → Fable
over-triggered (benign, use Opus); every model refuses → genuinely sensitive. `--repeat N` turns a
borderline coin-flip into a trip fraction. A cheaper `--probe summary` mode is available for a fast pass.

Details: [`interpreting-results.md`](skills/scoperoute/references/interpreting-results.md) ·
[`how-it-works.md`](skills/scoperoute/references/how-it-works.md).

## Cost, transparently

Because `arch` reads whole codebases, always estimate first:

```bash
python skills/scoperoute/scripts/scoperoute.py --root ~/dev --repeat 3 --estimate
```

It prices the run per project *and* per part (Sonnet → Opus → Fable), and — via
[CodexBar](https://github.com/konon4/CodexBar) — reads your real Claude tier, current window usage, and
spend to date, so you see the run as a **% of your plan** and which projects to run first. No CodexBar or
not logged in? It asks your tier (Pro/Max/Team) and uses a transparent price table.

## Privacy

Metadata only. Reports carry verdicts, recommendations, and per-component flags — never your code.
Refusal categories stay in a local, gitignored file; the shareable CSV never contains them, and a
refusal's explanation URL is never read or emitted. Identities from CodexBar are redacted.

## Fine print

- **Fable needs ≥30-day data retention** — under zero/under-30-day retention every Fable request 400s.
- It only detects and routes. For a genuinely sensitive project the answer is Opus — scoperoute never
  coaxes Fable.
- Dollar figures are *notional* (API-equivalent); on a subscription you spend quota, not dollars — the
  numbers let you compare runs and size them against your plan.

## Credits

Real tier/usage/spend comes from [CodexBar](https://github.com/konon4/CodexBar) (a fork of
[steipete/CodexBar](https://github.com/steipete/CodexBar), MIT). Session-transcript reading is grounded in
[claude-session-sync](https://github.com/konon4/claude-session-sync).

---

Built by **[HubLab.ai](https://hublab.ai)** — a boutique AI & data development agency · Astana. MIT.
