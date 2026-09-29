---
description: Set up basivo-memory sync on this machine (any OS). Private GitHub repo via a token, optional Google Drive mirror. No git, gh or brew needed.
argument-hint: <owner/repo>   e.g. yourname/basivo-memory-data
---

`M='sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" memory.py'`. **Never ask the user to paste a token
into the chat, and never echo one.**

1. `$M doctor`. If Python is missing, give the OS install command
   (Windows `winget install Python.Python.3.12`, macOS `xcode-select --install`,
   Linux their package manager) and stop.
2. `$M setup <owner/repo>` (from `$ARGUMENTS`, or suggest `<user>/basivo-memory-data`).
   If it returns `"need": "token"`:
   1. The repo must exist and be **Private** (create it at https://github.com/new).
   2. Open the `create_token` link. Choose **Only select repositories →** that repo,
      set **Contents: Read and write**, then Generate.
   3. In **their own terminal**: `python3 "<plugin>/scripts/memory.py" set-token`
      (Windows: `py`). Give the absolute path from `${CLAUDE_PLUGIN_ROOT}`.
   4. Run `$M setup <owner/repo>` again. It downloads every space (new laptop = full memory).
3. Offer the Google Drive backup: `$M mirror auto` (needs Google Drive for desktop).
4. Offer to link this repo: `/memory-link <space>`.
5. Say: restart Claude Code so the memory tools load. If basivo-journal is
   installed and set up, past conversations are searchable too.
