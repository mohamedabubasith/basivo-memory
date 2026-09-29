---
description: Remove the current repo from its shared memory space. Its docs leave the space's search and its pinned files stop loading, in every repo and on every laptop. Saved notes stay.
---

1. Run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" memory.py status` and tell the user which space this
   repo is in and what will stop: its docs leave the space's search, and its pinned files stop
   loading. Notes and past chats already saved stay in the space.
2. Ask them to confirm. If they only want a break (e.g. while building something new), suggest
   `/memory-pause` instead: it keeps the link and switches memory off here for a while.
3. On yes: `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" memory.py unlink` and confirm in one line.
