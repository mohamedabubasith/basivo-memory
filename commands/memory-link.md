---
description: Add the current repo to a shared memory space (e.g. link your spec repo and your code repo to the same space), and optionally pin rule files to load in every session.
argument-hint: <space> [files to pin...] [--path <folder>]   e.g. chatbot RULES.md, or plugins --path ~/code/basivo-qa
---

`M='sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" memory.py'`

1. If no space name was given, run `$M status` and show existing spaces. Ask
   which to join, or suggest a short lowercase name for a new one (e.g. the
   product name).
2. Run `$M link $ARGUMENTS`. It snapshots the repo's docs (Markdown/text,
   secrets masked) into the space. With `--path <folder>`, it links that repo
   without opening it. To build a whole project at once, run it once per
   folder, e.g. every plugin repo into a `plugins` space.
3. If nothing is pinned for this repo yet, look for obvious rule files (such as
   `RULES.md`, `CONVENTIONS.md`, `CLAUDE.md`, `docs/rules*.md`, `AGENTS.md`)
   and **ask** which to pin. Pinned files load at the start of every session
   in every repo of the space, capped at about 900 tokens in total, so
   suggest the most important one or two. Pin with `$M pin <file>`.
4. Confirm in 2–3 lines: the space, the repos now in it, the pinned files. Tell
   them to open the other repo and run `/memory-link <same space>` there too.
