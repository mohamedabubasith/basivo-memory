#!/usr/bin/env python3
"""MCP server (stdio, standard library only): shared memory across the repos in a space."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

SPACE_ARG = {"type": "string", "description": "Space name (default: the space of the repo you're working in)"}
TOOLS = [
    {"name": "memory_search",
     "description": ("Search the shared memory of the current project space: docs/specs/rules from ALL linked repos "
                     "(e.g. the spec repo while you work in the code repo), saved decisions and conventions, and past "
                     "conversations in any of those repos. Use before assuming requirements, rules or past decisions."),
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "A few keywords, e.g. 'auth token refresh rule'"},
         "kind": {"type": "string", "enum": ["doc", "note", "chat"], "description": "Optional: only docs, notes or chats"},
         "space": SPACE_ARG, "limit": {"type": "integer", "minimum": 1, "maximum": 12, "default": 6}},
         "required": ["query"]}},
    {"name": "memory_read",
     "description": "Read one memory item by the ref from memory_search: a doc section (doc:…), a note (note:…) or part of a past chat (chat:…).",
     "inputSchema": {"type": "object", "properties": {
         "ref": {"type": "string"}, "query": {"type": "string", "description": "Optional words to pick the relevant doc sections"},
         "space": SPACE_ARG}, "required": ["ref"]}},
    {"name": "memory_remember",
     "description": ("Save a decision, convention, rule, todo or fact to the shared memory so every repo in the space "
                     "sees it in future sessions. ONLY call this after the user said yes to saving it."),
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "One or two clear sentences, e.g. 'Use pnpm, not npm, in all repos.'"},
         "kind": {"type": "string", "enum": list(core.NOTE_KINDS), "default": "decision"},
         "tags": {"type": "array", "items": {"type": "string"}}, "space": SPACE_ARG}, "required": ["text"]}},
    {"name": "memory_forget",
     "description": "Delete a saved note by id (only when the user asks).",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "space": SPACE_ARG}, "required": ["id"]}},
    {"name": "memory_spaces",
     "description": "List memory spaces, their repos, pinned files and note counts, and which space the current repo belongs to.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _space(args):
    name = args.get("space")
    if name:
        s = next((x for x in core.all_spaces() if x["name"] == name), None)
        return s, None if s else f"No space named '{name}'."
    rid, _, repo = core.repo_identity(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    s = core.space_for_repo(rid)
    if s and core.paused_until(rid):
        return None, f"Shared memory is paused in this repo ({repo}). Work only from this repo and the user; /memory-resume turns it back on."
    return s, None if s else f"This repo ({repo}) isn't in a memory space. The user can run /memory-link <space>."


def call(name, args):
    if name == "memory_spaces":
        rid, _, _ = core.repo_identity(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
        cur = core.space_for_repo(rid)
        out = [f"current repo: {rid} → space {cur['name'] if cur else '(none)'}"]
        for s in core.all_spaces():
            out.append(f"- {s['name']}: repos {', '.join(r['id'] for r in s['repos'])}; pinned {', '.join(s.get('pinned', [])) or '-'}; "
                       f"{len(core.notes(s['name']))} notes")
        return "\n".join(out) if len(out) > 1 else out[0] + "\nNo spaces yet."
    s, err = _space(args)
    if err:
        return err
    if name == "memory_search":
        return core.search(s, args.get("query", ""), args.get("kind"), args.get("limit", 6))
    if name == "memory_read":
        return core.read(s, args.get("ref", ""), args.get("query"))
    if name == "memory_remember":
        rid, _, _ = core.repo_identity(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
        n = core.remember(s["name"], args.get("text", ""), args.get("kind", "decision"), args.get("tags"), repo=rid)
        _background_sync()
        return f"Saved to '{s['name']}' as {n['kind']} (id {n['id']}). Every repo in the space will see it."
    if name == "memory_forget":
        ok = core.forget(s["name"], args.get("id", ""))
        _background_sync()
        return "Deleted." if ok else "No note with that id."
    raise ValueError(f"unknown tool {name}")


def _background_sync():
    try:
        import memory
        memory.sync_background(force=True)
    except Exception:
        pass


def reply(mid, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": mid}
    out["error" if error else "result"] = error or result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            continue
        if method == "initialize":
            reply(mid, {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-03-26"),
                        "capabilities": {"tools": {}}, "serverInfo": {"name": "basivo-memory", "version": "0.3.0"}})
        elif method == "tools/list":
            reply(mid, {"tools": TOOLS})
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                reply(mid, {"content": [{"type": "text", "text": call(p.get("name"), p.get("arguments") or {})}]})
            except Exception as e:
                reply(mid, {"content": [{"type": "text", "text": f"memory error: {e}"}], "isError": True})
        elif method == "ping":
            reply(mid, {})
        else:
            reply(mid, error={"code": -32601, "message": f"method not found: {method}"})


if __name__ == "__main__":
    main()
