---
description: Add the current repo to a shared memory space (e.g. link your spec repo and your code repo to the same space), and optionally pin rule files to load in every session.
argument-hint: <space> [files to pin...]   e.g. chatbot RULES.md docs/conventions.md
---

`M='sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" memory.py'`

1. If no space name was given, run `$M status` and show existing spaces. Ask
   which to join, or suggest a short lowercase name for a new one (e.g. the
   product name).
2. Run `$M link $ARGUMENTS`. It snapshots this repo's docs (Markdown/text,
   secrets masked) into the space.
3. If nothing is pinned for this repo yet, look for obvious rule files (such as
   `RULES.md`, `CONVENTIONS.md`, `CLAUDE.md`, `docs/rules*.md`, `AGENTS.md`)
   and **ask** which to pin. Pinned files load at the start of every session
   in every repo of the space, capped at about 900 tokens in total, so
   suggest the most important one or two. Pin with `$M pin <file>`.
4. Confirm in 2–3 lines: the space, the repos now in it, the pinned files. Tell
   them to open the other repo and run `/memory-link <same space>` there too.
