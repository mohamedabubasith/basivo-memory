#!/usr/bin/env python3
"""End-to-end: two repos share one space; a second laptop syncs through a folder.
Run: python3 tests/test_memory.py   (exit 0 = pass, no network, no git needed)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(HERE, "scripts")


def make_repo(path, origin, files):
    os.makedirs(os.path.join(path, ".git"))
    with open(os.path.join(path, ".git", "config"), "w") as f:
        f.write(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {origin}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    for rel, text in files.items():
        p = os.path.join(path, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)


def run(home, journal, cwd, *args, stdin=None):
    env = {**os.environ, "BASIVO_MEMORY_HOME": home, "BASIVO_JOURNAL_HOME": journal, "BASIVO_MEMORY_NO_BACKGROUND": "1",
           "CLAUDE_CONFIG_DIR": os.path.join(home, "claude")}
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "memory.py"), *args], cwd=cwd, env=env,
                       input=stdin, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout


def mcp(home, journal, cwd, calls):
    reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
    reqs += [{"jsonrpc": "2.0", "id": i + 2, "method": "tools/call", "params": {"name": n, "arguments": a}} for i, (n, a) in enumerate(calls)]
    env = {**os.environ, "BASIVO_MEMORY_HOME": home, "BASIVO_JOURNAL_HOME": journal, "CLAUDE_PROJECT_DIR": cwd, "BASIVO_MEMORY_NO_BACKGROUND": "1"}
    out = subprocess.run([sys.executable, os.path.join(SCRIPTS, "mcp_server.py")], input="\n".join(map(json.dumps, reqs)) + "\n",
                         cwd=cwd, env=env, capture_output=True, text=True, timeout=60).stdout
    res = {m["id"]: m for m in map(json.loads, out.strip().splitlines())}
    return [res[i + 2]["result"]["content"][0]["text"] for i in range(len(calls))]


with tempfile.TemporaryDirectory() as t:
    home_a, home_b, journal, drive = (os.path.join(t, n) for n in ("mem-a", "mem-b", "journal", "drive"))
    spec, app = os.path.join(t, "work", "chatbot-spec"), os.path.join(t, "work", "chatbot-app")
    make_repo(spec, "git@github.com:acme/chatbot-spec.git", {
        "RULES.md": "# Rules\n- All API routes require JWT auth.\n- Use pnpm, never npm.\n",
        "docs/api.md": "# API\n## Tokens\nAccess tokens expire after 15 minutes; refresh with /auth/refresh.\n",
        "secrets.md": "db password: hunter2secret\n"})
    make_repo(app, "https://github.com/acme/chatbot-app.git", {"README.md": "# Chatbot app\nThe code.\n"})
    # a past conversation in the spec repo, saved by basivo-journal
    cdir = os.path.join(journal, "data", "chats", "2026", "09")
    os.makedirs(cdir)
    json.dump({"session_id": "c0ffee00-1111-2222-3333-444444444444", "project": "~/work/chatbot-spec", "title": "Pick a queue",
               "started_at": "2026-09-20T10:00:00Z", "messages": [
                   {"role": "user", "ts": "2026-09-20T10:00:00Z", "text": "should we use redis or sqs for the job queue?"},
                   {"role": "assistant", "ts": "2026-09-20T10:01:00Z", "text": "Go with SQS: managed, and you're already on AWS."}]},
              open(os.path.join(cdir, "c0ffee00-1111-2222-3333-444444444444.json"), "w"))

    # laptop A: link both repos, pin the rules
    r = json.loads(run(home_a, journal, spec, "link", "chatbot", "RULES.md"))
    assert r["repo"] == "github.com/acme/chatbot-spec" and r["docs_snapshotted"] == 3, r
    r = json.loads(run(home_a, journal, app, "link", "chatbot"))
    assert r["repos"] == ["github.com/acme/chatbot-spec", "github.com/acme/chatbot-app"], r
    snap = open(os.path.join(home_a, "data", "spaces", "chatbot", "docs", "github.com__acme__chatbot-spec", "secrets.md")).read()
    assert "hunter2secret" not in snap, "doc snapshots must be masked"

    # working in the CODE repo, the session card carries the SPEC repo's pinned rules
    card = json.loads(run(home_a, journal, app, "start", stdin=json.dumps({"cwd": app})))["hookSpecificOutput"]["additionalContext"]
    assert "Shared memory space 'chatbot'" in card and "JWT auth" in card and "pnpm" in card, card
    assert "memory_remember only after they say yes" in card

    # tools from the code repo reach the spec docs, notes and past chats
    s_doc, s_chat, save, s_note = mcp(home_a, journal, app, [
        ("memory_search", {"query": "token expire refresh"}),
        ("memory_search", {"query": "job queue sqs", "kind": "chat"}),
        ("memory_remember", {"text": "Deploy the app with SST, not raw CDK.", "kind": "decision", "tags": ["deploy"]}),
        ("memory_search", {"query": "SST deploy"})])
    assert "chatbot-spec/docs/api.md" in s_doc and "15 minutes" in s_doc, s_doc
    assert "Pick a queue" in s_chat and "sqs" in s_chat.lower(), s_chat
    assert "Saved to 'chatbot'" in save
    assert "[note] decision · deploy" in s_note, s_note
    ref = [l for l in s_doc.splitlines() if "ref doc:" in l][0].split("ref ")[1]
    doc = mcp(home_a, journal, app, [("memory_read", {"ref": ref})])[0]
    assert "/auth/refresh" in doc, doc
    card = json.loads(run(home_a, journal, spec, "start", stdin=json.dumps({"cwd": spec})))["hookSpecificOutput"]["additionalContext"]
    assert "Deploy the app with SST" in card, "new decisions show up in every repo's card"
    print("PASS  one space, two repos: pinned rules in card, search docs/notes/chats, remember, read")

    # laptop B: different folder layout, syncs through a shared folder
    json.loads(run(home_a, journal, app, "setup", drive))
    b_app = os.path.join(t, "elsewhere", "chatbot-app")
    make_repo(b_app, "git@github.com:acme/chatbot-app.git", {"README.md": "# Chatbot app\n"})
    r = json.loads(run(home_b, os.path.join(t, "no-journal"), b_app, "setup", drive))
    assert r["spaces"] == ["chatbot"] and r["ok"], r
    card_b = json.loads(run(home_b, os.path.join(t, "no-journal"), b_app, "start", stdin=json.dumps({"cwd": b_app})))["hookSpecificOutput"]["additionalContext"]
    assert "JWT auth" in card_b and "Deploy the app with SST" in card_b, card_b
    s_b = mcp(home_b, os.path.join(t, "no-journal"), b_app, [("memory_search", {"query": "refresh token"})])[0]
    assert "api.md" in s_b, "spec docs are available on a laptop that never cloned the spec repo"
    print("PASS  second laptop: same space by repo URL, spec docs without cloning the spec repo")

    # forget + unlink
    nid = json.loads(run(home_a, journal, app, "remember", "todo", "add rate limiting"))["id"]
    assert json.loads(run(home_a, journal, app, "forget", nid))["ok"]
    gone = mcp(home_a, journal, app, [("memory_search", {"query": "rate limiting", "kind": "note"})])[0]
    assert gone.startswith("Nothing in the 'chatbot' memory"), gone
    json.loads(run(home_a, journal, spec, "unlink"))
    st = json.loads(run(home_a, journal, app, "status"))
    assert st["this_space"] == "chatbot" and st["spaces"][0]["repos"] == ["github.com/acme/chatbot-app"], st
    left = mcp(home_a, journal, app, [("memory_search", {"query": "JWT auth"})])[0]
    assert left.startswith("Nothing in the 'chatbot' memory"), "unlinked repo's docs leave the index: " + left
    # an unlinked repo gets a one-line pointer, not an error
    other = os.path.join(t, "work", "other")
    make_repo(other, "git@github.com:acme/other.git", {})
    out = run(home_a, journal, other, "start", stdin=json.dumps({"cwd": other}))
    assert "isn't in a memory space" in out
    print("PASS  forget, unlink, unlinked repo pointer")

    # pause: memory off in this repo (card says paused, tools refuse), resume brings it back
    r = json.loads(run(home_a, journal, app, "pause", "2"))
    assert r["ok"] and r["paused_until"] != "until resumed"
    card = json.loads(run(home_a, journal, app, "start", stdin=json.dumps({"cwd": app})))["hookSpecificOutput"]["additionalContext"]
    assert "PAUSED" in card and "Deploy the app with SST" not in card, card
    assert "paused" in mcp(home_a, journal, app, [("memory_search", {"query": "SST"})])[0]
    assert json.loads(run(home_a, journal, app, "status"))["paused"]
    run(home_a, journal, app, "resume")
    card = json.loads(run(home_a, journal, app, "start", stdin=json.dumps({"cwd": app})))["hookSpecificOutput"]["additionalContext"]
    assert "Deploy the app with SST" in card and "follow the code and the user" in card, card
    run(home_a, journal, app, "pause")  # no hours = until resumed
    assert json.loads(run(home_a, journal, app, "status"))["paused"] == "until resumed"
    print("PASS  pause (timed and open-ended), resume, conflict rule in card")

    # a "project" space linked entirely from one place with --path, and a hard token budget
    run(home_a, journal, app, "resume")
    hub = os.path.join(t, "work", "plugins-hub")
    os.makedirs(hub)
    repos = []
    for name in ("plugin-a", "plugin-b", "plugin-c"):
        p = os.path.join(t, "work", name)
        make_repo(p, f"git@github.com:acme/{name}.git", {"RULES.md": f"# {name} rules\n" + ("- keep it simple and tested. " * 600)})
        repos.append(p)
    for p in repos:
        json.loads(run(home_a, journal, hub, "link", "plugins", "RULES.md", "--path", p))
    st = json.loads(run(home_a, journal, repos[0], "status"))
    sp = next(x for x in st["spaces"] if x["name"] == "plugins")
    assert sp["repos"] == [f"github.com/acme/plugin-{c}" for c in "abc"] and len(sp["pinned"]) == 3, sp
    assert st["session_card_tokens"] <= 1000, st["session_card_tokens"]           # default cap holds with 3 huge pins
    run(home_a, journal, repos[0], "budget", "300")
    card = json.loads(run(home_a, journal, repos[0], "start", stdin=json.dumps({"cwd": repos[0]})))["hookSpecificOutput"]["additionalContext"]
    assert len(card) <= 300 * 4 and "memory_search" in card, (len(card), card[-200:])  # budget respected, tools hint kept
    assert json.loads(run(home_a, journal, repos[0], "status"))["session_card_tokens"] <= 300
    out = json.loads(run(home_a, journal, hub, "unlink", "--path", repos[2]))
    assert out["ok"] and out["space"] == "plugins"
    st = json.loads(run(home_a, journal, repos[0], "status"))
    assert len(next(x for x in st["spaces"] if x["name"] == "plugins")["repos"]) == 2
    big = mcp(home_a, journal, repos[0], [("memory_search", {"query": "simple tested", "limit": 12})])[0]
    assert len(big) <= 6100, len(big)                                            # tool output capped too
    print("PASS  project space via --path, token budget (default 1000, custom 300), unlink --path, tool cap")


def transcript(path, sid, cwd, turns):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for i, (user, bot) in enumerate(turns):
            ts = f"2026-09-29T10:0{i}:00Z"
            f.write(json.dumps({"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": ts, "message": {"content": user}}) + "\n")
            f.write(json.dumps({"type": "assistant", "sessionId": sid, "cwd": cwd, "timestamp": ts,
                                "message": {"content": [{"type": "text", "text": bot}]}}) + "\n")


# conversations saved by basivo-memory itself (no basivo-journal), synced, and skipped when unlinked or paused
with tempfile.TemporaryDirectory() as t:
    t = os.path.realpath(t)  # Claude Code names project folders after the real path
    home_a, home_b, drive, nj = (os.path.join(t, n) for n in ("mem-a", "mem-b", "drive", "no-journal"))
    spec, app = os.path.join(t, "w", "spec"), os.path.join(t, "w", "app")
    make_repo(spec, "git@github.com:acme/spec.git", {"RULES.md": "# Rules\n"})
    make_repo(app, "git@github.com:acme/app.git", {})
    # a past chat Claude Code already has for the spec repo is imported when the repo is linked
    old = os.path.join(home_a, "claude", "projects", "".join(c if c.isalnum() else "-" for c in spec), "old.jsonl")
    transcript(old, "0ld00000-0000-0000-0000-000000000000", spec,
               [("which payment provider?", "Use Stripe; Razorpay later for India.")])
    assert json.loads(run(home_a, nj, spec, "link", "shop"))["chats_imported"] == 1
    run(home_a, nj, app, "link", "shop")
    # a live session in the app repo: each Stop saves the conversation so far
    tp = os.path.join(t, "live.jsonl")
    transcript(tp, "11ve0000-0000-0000-0000-000000000000", app,
               [("how do we cache product pages?", "Cache them at the CDN for 5 minutes."),
                ("my password is hunter2secret", "Noted, don't share passwords here.")])
    run(home_a, nj, app, "checkpoint", stdin=json.dumps({"transcript_path": tp, "cwd": app}))
    saved = open(os.path.join(home_a, "data", "spaces", "shop", "chats", "11ve0000-0000-0000-0000-000000000000.json")).read()
    assert "CDN for 5 minutes" in saved and "hunter2secret" not in saved, saved
    hits = mcp(home_a, nj, spec, [("memory_search", {"query": "cache product pages", "kind": "chat"}),
                                  ("memory_search", {"query": "payment provider stripe", "kind": "chat"})])
    assert "cache" in hits[0] and "Stripe" in hits[1], hits
    ref = [l for l in hits[0].splitlines() if "ref chat:" in l][0].split("ref ")[1]
    assert "5 minutes" in mcp(home_a, nj, spec, [("memory_read", {"ref": ref})])[0]
    # second laptop with no basivo-journal still finds the conversation
    run(home_a, nj, app, "setup", drive)
    b_app = os.path.join(t, "b", "app")
    make_repo(b_app, "https://github.com/acme/app", {})
    run(home_b, nj, b_app, "setup", drive)
    assert "cache" in mcp(home_b, nj, b_app, [("memory_search", {"query": "cache product pages"})])[0]
    # paused or unlinked repos don't record; unlinking removes their chats from search
    run(home_a, nj, app, "pause")
    tp2 = os.path.join(t, "paused.jsonl")
    transcript(tp2, "9a05ed00-0000-0000-0000-000000000000", app, [("secret new feature idea", "ok")])
    run(home_a, nj, app, "checkpoint", stdin=json.dumps({"transcript_path": tp2, "cwd": app}))
    assert not os.path.exists(os.path.join(home_a, "data", "spaces", "shop", "chats", "9a05ed00-0000-0000-0000-000000000000.json"))
    run(home_a, nj, app, "resume")
    run(home_a, nj, spec, "unlink")
    assert "Stripe" not in mcp(home_a, nj, app, [("memory_search", {"query": "payment provider stripe", "kind": "chat"})])[0]
    run(home_a, nj, app, "chats", "off")
    tp3 = os.path.join(t, "off.jsonl")
    transcript(tp3, "0ff00000-0000-0000-0000-000000000000", app, [("hello", "hi")])
    run(home_a, nj, app, "checkpoint", stdin=json.dumps({"transcript_path": tp3, "cwd": app}))
    assert not os.path.exists(os.path.join(home_a, "data", "spaces", "shop", "chats", "0ff00000-0000-0000-0000-000000000000.json"))
    print("PASS  own conversations: import on link, save on Stop (masked), search/read, sync without journal, pause/unlink/off")

print("\nAll basivo-memory tests passed.")
