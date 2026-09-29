---
description: Switch shared memory off in this repo for a while (e.g. while building a brand-new feature, so old context can't mislead). Keeps the link. /memory-resume turns it back on.
argument-hint: [hours]   e.g. 24   (no number = until you resume)
---

Run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" memory.py pause $ARGUMENTS` and confirm in one line
until when. From now on, **don't** call memory tools in this repo during this session; work only
from the repo and the user. Mention that it applies to new sessions too, on this laptop only.
