---
description: Set how many tokens the shared-memory card may use at the start of each session in this space (150–4000, default 1000), and show what it costs now.
argument-hint: <tokens>   e.g. 500
---

`M='sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" memory.py'`

1. If a number was given: `$M budget $ARGUMENTS`.
2. Run `$M status` and report in two lines: the space's `card_budget_tokens` and the actual
   `session_card_tokens` for this repo. Remind them that search results are separate and small
   (about 6,000 characters max per call), and that nothing else loads automatically.
