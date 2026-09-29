# Changelog

## [0.3.0] - 2026-09-29

### Added
- **basivo-memory saves conversations itself.** After every reply (Stop hook) the
  chat so far is saved to the repo's space, secrets masked, and synced. Search
  and read them with `memory_search` / `memory_read`, from any repo in the space
  and any laptop. basivo-journal is no longer needed for this (its chats are
  still searched, without duplicates).
- Linking a repo imports the past conversations Claude Code still has for it.
- `memory.py chats on|off` to stop recording on a laptop. Paused and unlinked
  repos never record, and unlinking removes their chats from search.

## [0.2.0] - 2026-09-29

### Added
- **Build a project space from one place**: `memory.py link <space> --path <folder>`
  and `memory.py unlink --path <folder>`, with no need to open each repo.
- **Per-space token budget** for the session card: `/memory-budget <tokens>`
  (150–4000, default 1000). Pinned files, notes and the header all fit inside
  it; the card never exceeds it.
- `/memory-status` shows `session_card_tokens`, the card's actual cost here.
- README: "A space is a project" and a token budget table.

## [0.1.0] - 2026-09-29

### Added
- **Spaces**: group related repos (e.g. a spec/rules repo and a code repo) into
  one shared memory. Repos are identified by their GitHub URL, so any folder
  and any laptop works.
- **Doc snapshots** of every linked repo (Markdown/text, secrets masked). They
  travel with the memory, so a laptop without the spec repo still sees its docs.
- **Notes** (decision, convention, rule, todo, fact), saved only after the user
  says yes; deletions sync as tombstones.
- **Conversations** from basivo-journal for any repo in the space.
- **Session card**: pinned rules (about 900 tokens max), recent decisions, and a
  "current code and the user win over memory" rule.
- MCP tools: `memory_search`, `memory_read`, `memory_remember`,
  `memory_forget`, `memory_spaces`.
- Commands: `/memory-setup`, `/memory-link`, `/memory-status`,
  `/memory-pause`, `/memory-resume`, `/memory-unlink`.
- Sync via the GitHub API (fine-grained token) and/or a folder such as Google
  Drive. No git, gh or brew; runs on Windows, macOS and Linux.
