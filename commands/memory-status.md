---
description: Show your memory spaces, which repos are linked, pinned files, and how many notes and docs each space has.
---

Run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" memory.py status` and summarize the JSON in a short
list: the current repo's space first, then the other spaces with their repos, pinned files,
note and doc counts, and where it syncs (GitHub repo / mirror folder). If sync isn't set up,
suggest `/memory-setup`.
