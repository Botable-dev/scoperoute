<!--
VENDORED REFERENCE — not part of this project.
Source: github.com/konon4/claude-session-sync @ 1806a37 (~/workplace/claude-session-sync/README.md)
Why it's here: it documents the on-disk Claude Code session layout that Phase 2 (`fable_watch.py`)
must tail — `projects/<encoded-cwd>/<id>.jsonl` + the `<id>/` payload dir, subagents as a sidecar,
and the "live" mtime window for detecting the active session (PRD §4.1–4.5). Read for the domain
model only; don't treat its safety/sync invariants as requirements of this project.
-->

# claude-session-sync

Move a **Claude Code session** between config directories (i.e. between logins) — safely,
in either direction, without losing the pieces that make a session resumable.

## Why

Claude Code stores everything under `$CLAUDE_CONFIG_DIR` (default `~/.claude`). If you run more
than one login — e.g. a personal `~/.claude` and a work `~/.claude-adam` via a wrapper:

```bash
#!/usr/bin/env bash
export CLAUDE_CONFIG_DIR="$HOME/.claude-adam"
exec claude "$@"
```

— then `CLAUDE_CONFIG_DIR` carries the **login together with the data**. To continue a chat under
a different account you must physically copy that session between config dirs, repeatedly and in
**both directions** as work continues.

Doing it by hand is error-prone. A session is **not** just `<id>.jsonl`; it's the whole `<id>/`
payload directory:

```
projects/<encoded-cwd>/
├── <id>.jsonl                       # the transcript
└── <id>/
    ├── subagents/                   # subagent traces (sidecar)
    │   └── workflows/<wf>/journal.jsonl   # ← unfinished-workflow state = resumability
    ├── workflows/
    │   ├── wf_*.json                # workflow manifests
    │   └── scripts/*.js             # workflow scripts
    └── tool-results/
```

The failure modes this tool exists to prevent (all hit in practice):

- **Wrong direction / stale overwrite** — eyeballing bytes+mtime to guess which side is newer.
- **Missed payload** — copying only `<id>.jsonl` and losing subagents / the workflow `journal.jsonl`.
  Without the journal an interrupted workflow can't be resumed and re-runs from scratch.
- **Wrong session** — a workflow you care about lives in a *different* session id than you think.
- **Scattered scripts** — one session id spreads across sibling project dirs (`aip2026`,
  `aip2026-sba`, `aip2026-html`, `…module-tests…`) and its workflow scripts get left behind.
- **No backup before overwrite.**

## Install

Dependencies: `bash`, `rsync`, `jq`, `stat`, `find`, `comm` (all standard on macOS + Homebrew).

```bash
git clone https://github.com/konon4/claude-session-sync ~/workplace/claude-session-sync
ln -s ~/workplace/claude-session-sync/claude-session-sync ~/bin/claude-session-sync   # ~/bin on PATH
```

## Usage

```
claude-session-sync ls [--project <glob>] [SESSION-prefix]
claude-session-sync sync SESSION (--from A --to B | --auto) [flags]
```

### `ls` — what's where, and which way to sync

```
$ claude-session-sync ls --project aip2026
SESSION     PROJECT                          PRESENT       SIZE  MTIME             DIRECTION        FLAGS
369ab98f    -Users-konn4-workplace-aip2026   claude+adam  75.8M  2026-07-01 10:10  adam → claude    ⚑wf
59cf8582    -Users-konn4-workplace-aip2026   claude+adam  17.9M  2026-07-02 01:06  claude → adam    ⚑wf
6fbae755    -Users-konn4-workplace-aip2026   claude        0.2M  2026-07-02 01:06  only claude → adam
d7fbba13    -Users-konn4-workplace-aip2026   adam          0.4M  2026-07-02 01:12  only adam → claude   live
```

- **PRESENT** — which config roots hold the session.
- **DIRECTION** — suggested source→dest, by transcript mtime.
- **⚑wf** — carries an unfinished-workflow `journal.jsonl` (resumable).
- **live** — transcript touched within `CLAUDE_SYNC_LIVE_WINDOW` (default 90s); don't sync it.

### `sync` — move one session

```bash
claude-session-sync sync 59cf8582 --auto                       # newest side is the source
claude-session-sync sync 369ab98f --from adam --to claude      # explicit direction
claude-session-sync sync 369ab98f --from adam --to claude --with-memory --dry-run
```

| flag | meaning |
|---|---|
| `--from A --to B` | `A`/`B` = a root alias (`claude`, `adam`, …) or a path |
| `--auto` | newest transcript mtime is the source |
| `--project <glob>` | disambiguate when a session id lives under >1 project |
| `--with-memory` | also sync the project's `memory/` dir (backed up first) |
| `--no-consolidate` | don't merge workflow scripts scattered across sibling projects |
| `--dry-run` | show what would happen; write nothing |
| `--yes` | skip the confirmation prompt |
| `--force` | override the live-guard / non-newer-source refusal |

`sync` does, in order: live-guard → back up the dest (timestamped) → copy transcript → `rsync` the
payload dir → consolidate scattered scripts → (optional) memory → **verify gate**.

## Safety model

- **Source is never modified or deleted** — it stays as your reserve.
- **Every dest overwrite is preceded by a non-clobbering, timestamped backup**
  (`<id>.jsonl.bak-<size>-<ts>` and `<id>.payload.bak-<ts>.tar`).
- **No `rsync --delete`, ever** — dest keeps its own extra files; sync is additive.
- **Verify gate** at the end: transcript byte-equal + dest ⊇ source payload (0 files missing).
  Non-zero exit on mismatch.
- **Live-guard** refuses a source touched in the last `LIVE_WINDOW` seconds (unless `--force`).
- **Backwards-guard** refuses when the dest is newer/larger than the source (unless `--force`).
- **Credentials/login are never touched.** The tool moves session *data* only; resuming under a
  config uses that config's own login — which is the whole point.

## Config

| env | default | meaning |
|---|---|---|
| `CLAUDE_SYNC_ROOTS` | `claude=~/.claude adam=~/.claude-adam` | space-separated `alias=path` roots to add/override |
| `CLAUDE_SYNC_LIVE_WINDOW` | `90` | seconds; live-guard threshold |

```bash
# add more logins to the picture
export CLAUDE_SYNC_ROOTS="sr=$HOME/.config/claude-sr glm=$HOME/.glm_claude"
```

## License

MIT — see [LICENSE](LICENSE).
