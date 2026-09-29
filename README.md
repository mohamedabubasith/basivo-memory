# basivo-memory

**One shared memory for related repos.** Put your *spec* repo (rules, tools,
requirements, the plan) and your *code* repo in the same **space**. Then
Claude in the code repo knows the spec repo's rules, the decisions you made,
and what you discussed in either repo, as if they were one project.

```
space "chatbot"
  ├─ chatbot-spec   RULES.md, docs/api.md, requirements…   ← what to build and the rules
  └─ chatbot-app    the code                                ← where you build it

In chatbot-app, every session starts with:
  • chatbot-spec's pinned RULES.md ("All API routes require JWT auth", "Use pnpm")
  • recent decisions ("Deploy with SST, not raw CDK", saved last week in chatbot-spec)
and Claude can search both repos' docs, notes and past conversations on demand.
```

## A space is a project

Like a Claude Project, a space groups everything that belongs together. For example:

```
space "plugins"                         space "chatbot"
  ├─ basivo-operator                      ├─ chatbot-spec  (rules, plan)
  ├─ basivo-qa                            └─ chatbot-app   (code)
  ├─ basivo-journal
  └─ basivo-memory
```

Build one from a single session, without opening each repo:

```
memory.py link plugins RULES.md --path ~/code/basivo-operator
memory.py link plugins --path ~/code/basivo-qa
memory.py unlink --path ~/code/basivo-qa        # take one out again
```

Any number of spaces is fine, but each repo belongs to one space.

## What's in a space's memory

| Source | What | How it gets there |
|---|---|---|
| **Docs** of every linked repo | Markdown and text files (README, `docs/`, specs, rules, `CLAUDE.md`, …), secrets masked | Snapshotted when you link the repo, and refreshed at each session start in that repo |
| **Notes** | Decisions, conventions, rules, to-dos, facts | Claude asks *"Save this to the 'chatbot' memory?"* and saves only on **yes** |
| **Conversations** | Your chats in any linked repo | Read from [basivo-journal](https://github.com/mohamedabubasith/basivo-journal), if installed |

**Doc snapshots travel with the memory**, so a laptop that never cloned the
spec repo still sees its rules and docs.

## How Claude uses it

**Session start** (in any linked repo), capped at about 1,000 tokens:
- the space and its repos
- **pinned files** (the rules you choose), up to about 900 tokens in total
- the last few decisions and conventions
- a rule: *memory is reference. If it conflicts with the current code or what
  you ask now, Claude follows the code and you, and points out the conflict.*

### Token budget: nothing is dumped into context

| What | When it's used | Cost |
|---|---|---|
| **Session card** (pinned rules, recent decisions, one usage rule) | Once per session | **Capped per space, default 1,000 tokens** (`/memory-budget 150…4000`). Pinned files beyond the cap are cut, with a pointer to `memory_read`. |
| **Search results** | Only when Claude calls `memory_search` | Short snippets, max 12 results, about 1,500 tokens max |
| **Reading** a doc, note or chat | Only when Claude calls `memory_read` | Only the relevant sections, about 1,500 tokens max |
| Everything else (all docs, all chats) | **Never loaded automatically** | 0, it stays in the local index |

`/memory-status` shows what the card actually costs in the current repo (`session_card_tokens`).

**Tools** (called only when useful, each returns short snippets):

| Tool | What it does |
|---|---|
| `memory_search(query, kind?)` | Search docs, notes and chats across all repos in the space |
| `memory_read(ref)` | Read the relevant doc section, note, or part of a chat |
| `memory_remember(text, kind)` | Save a decision, convention, rule, todo or fact (**only after you say yes**) |
| `memory_forget(id)` | Delete a note |
| `memory_spaces()` | List spaces, repos, pinned files, note counts |

## Commands

| Command | What it does |
|---|---|
| `/memory-setup <owner/repo>` | Set up sync on this laptop (private GitHub repo and/or Google Drive) |
| `/memory-link <space> [files to pin] [--path DIR]` | Add this repo (or another folder) to a space; offers to pin rule files |
| `/memory-budget <tokens>` | Cap the session card for this space and show its current cost |
| `/memory-status` | Spaces, linked repos, pinned files, counts, paused or not |
| `/memory-pause [hours]` | **Switch memory off here for a while**, e.g. while building a brand-new feature, so old context can't mislead. Keeps the link. |
| `/memory-resume` | Turn it back on |
| `/memory-unlink` | Remove this repo from its space: its docs leave search and its pins stop loading, everywhere (`memory.py unlink --path DIR` for another folder) |

**Too much or outdated context?** Use the lightest fix first:
1. `memory_forget` an outdated note.
2. Unpin a file: `memory.py unpin <file>`.
3. `/memory-pause` while working on something new.
4. `/memory-unlink` if the repo doesn't belong in the space.

## Setup (any OS: Windows, macOS, Linux)

You need **Claude Code**, **Python 3.8+** (Windows: `winget install Python.Python.3.12`)
and a free **GitHub** account. **No git, GitHub CLI or Homebrew.**

1. Install:
   ```
   claude plugin marketplace add mohamedabubasith/basivo-plugins
   claude plugin install basivo-memory@basivo
   ```
2. Restart Claude Code, then run `/memory-setup <you>/basivo-memory-data`:
   - create the repo at https://github.com/new and make it **Private**
   - create a token from the link setup gives you: *Only select repositories →*
     that repo, *Contents: Read and write*
   - save it **in your own terminal** (hidden input, never in the chat):
     `python3 <plugin>/scripts/memory.py set-token` (Windows: `py`)
   - optional Google Drive backup: `memory.py mirror auto`
3. In each repo that belongs together: `/memory-link chatbot`
   (in the spec repo, pin the rules: `/memory-link chatbot RULES.md`).

**New laptop:** repeat steps 1 and 2 with the same repo name. Every space, doc
snapshot and note comes back. Repos are recognised by their **GitHub URL**, so
it doesn't matter which folder you clone them into.

**Conversations:** install and set up [basivo-journal](https://github.com/mohamedabubasith/basivo-journal)
too. Without it, docs and notes still work.

## What is stored, and where

| Path | What |
|---|---|
| `~/.basivo-memory/data/spaces/<space>/space.json` | Repos in the space (by GitHub URL) and the pinned files |
| `…/docs/<repo>/…` + `_manifest.json` | Doc snapshots (secrets masked) |
| `…/notes/<id>.json` | One note per file; deleted notes become tombstones so the deletion syncs |
| `~/.basivo-memory/config.json` | Token, repo, mirror, this laptop's repo folders, pauses (readable only by you) |
| `~/.basivo-memory/index.db` | Local search index (a cache, rebuilt automatically) |

Synced to your **private** GitHub repo and, optionally, a Google Drive folder.
Pauses are per laptop and not synced.

## Tests

```
python3 tests/test_memory.py
```

It runs two repos in one space, a second laptop syncing through a folder,
forget, unlink and pause.

Part of the [Basivo plugins](https://github.com/mohamedabubasith/basivo-plugins).
Made by [Basivo](https://basivo.in).
