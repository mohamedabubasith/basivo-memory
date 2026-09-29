#!/usr/bin/env python3
"""basivo-memory command line (also used by the hooks).

  memory.py link <space> [pin ...]   add the current repo to a space (and pin files)
  memory.py unlink                   remove the current repo from its space
  memory.py pin <file> / unpin <file>
  memory.py pause [hours] / resume   switch memory off in this repo (e.g. while building something new)
  memory.py status                   spaces, repos, pinned files, note counts (JSON)
  memory.py remember <kind> <text>   save a note (decision|convention|rule|todo|fact)
  memory.py forget <note-id>
  memory.py search <words...>        search the current repo's space
  memory.py setup <owner/repo>|<folder>   sync with a private GitHub repo (API) or a folder
  memory.py set-token                save a GitHub token (typed hidden)
  memory.py mirror auto|<folder>|off also keep a copy in Google Drive (or any folder)
  memory.py sync | doctor
  memory.py start | end              hooks (session start card / sync at end)
"""
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402
import storage  # noqa: E402

SYNC_EVERY_S = 120


def _hook_input():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def remotes():
    cfg = core.config()
    out = []
    if cfg.get("github_repo") and cfg.get("github_token"):
        out.append(storage.GitHubRemote(cfg["github_repo"], cfg["github_token"], cfg.get("github_branch", "main")))
    if cfg.get("mirror_dir"):
        out.append(storage.FolderRemote(cfg["mirror_dir"]))
    return out


def sync_now():
    os.makedirs(os.path.join(core.DATA, "spaces"), exist_ok=True)
    results = []
    for rm in remotes():
        try:
            r = storage.sync(core.DATA, rm, f"memory sync from {socket.gethostname().split('.')[0]}")
            r["ok"] = True
        except storage.HttpError as e:
            r = {"remote": rm.label, "ok": False, "status": e.status, "error": str(e)[:160]}
        except Exception as e:
            r = {"remote": rm.label, "ok": False, "error": str(e)[:160]}
        results.append(r)
    st = core.load(core.STATE, {}) or {}
    st["last_sync"] = {"at": time.time(), "results": results}
    core.save(core.STATE, st)
    return all(r["ok"] for r in results), results


def sync_background(force=False):
    st = core.load(core.STATE, {}) or {}
    if os.environ.get("BASIVO_MEMORY_NO_BACKGROUND") or not remotes() or (not force and time.time() - st.get("last_sync_try", 0) < SYNC_EVERY_S):
        return
    st["last_sync_try"] = time.time()
    core.save(core.STATE, st)
    kw = {"creationflags": 0x00000008} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "sync"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kw)


def sync_notice():
    res = ((core.load(core.STATE, {}) or {}).get("last_sync") or {}).get("results") or []
    bad = [r for r in res if not r.get("ok")]
    if any(r.get("status") == 401 for r in bad):
        return "Note: memory sync to GitHub is paused because the token expired; changes are kept locally. Tell the user to run /memory-setup."
    return ""


def token_url():
    return ("https://github.com/settings/personal-access-tokens/new?name=basivo-memory"
            "&description=basivo-memory+sync+for+your+private+memory+repo&expires_in=365&contents=write&metadata=read")


def current_space(start=None):
    rid, _, _ = core.repo_identity(start)
    return core.space_for_repo(rid), rid


# ---------- commands ----------

def cmd_start():
    hook = _hook_input()
    start = hook.get("cwd") or os.getcwd()
    # pull first (short, bounded) so another laptop's notes show up in this session's card
    if remotes():
        try:
            sync_now()
        except Exception:
            pass
    text = core.card(start)
    notice = sync_notice()
    if notice:
        text = (text + "\n" + notice).strip()
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    sync_background(force=True)  # push this repo's refreshed doc snapshot


def cmd_end():
    _hook_input()
    sync_background(force=True)


def cmd_setup(target):
    os.makedirs(os.path.join(core.DATA, "spaces"), exist_ok=True)
    looks_repo = target.count("/") == 1 and not target.startswith(("~", "/", ".")) and not os.path.exists(os.path.expanduser(target))
    if looks_repo:
        cfg = core.update_config(github_repo=target)
        if not cfg.get("github_token"):
            print(json.dumps({"ok": False, "need": "token", "repo": target, "create_token": token_url(), "then_run": "memory.py set-token"}))
            return
        ok, msg = storage.GitHubRemote(target, cfg["github_token"]).check()
        if not ok:
            print(json.dumps({"ok": False, "repo": target, "error": msg, "create_token": token_url()}))
            return
    else:
        core.update_config(mirror_dir=os.path.expanduser(target))
    ok, results = sync_now()
    print(json.dumps({"ok": ok, "spaces": [s["name"] for s in core.all_spaces()], "sync": results}))


def cmd_set_token(token=None):
    import getpass
    token = (token or os.environ.get("BASIVO_MEMORY_TOKEN") or getpass.getpass("Paste your GitHub token (hidden): ")).strip()
    if not token:
        sys.exit("no token given")
    repo = core.config().get("github_repo")
    if repo:
        ok, msg = storage.GitHubRemote(repo, token).check()
        if not ok:
            sys.exit(f"token not saved: {msg}")
    core.update_config(github_token=token)
    print(f"token saved to {core.CONFIG} (readable only by you)" + (f"; it can write to {repo}" if repo else ""))


def cmd_mirror(arg):
    if arg == "off":
        core.update_config(mirror_dir=None)
        print("mirror turned off")
        return
    if arg == "auto":
        gd = storage.find_google_drive()
        if not gd:
            sys.exit("Google Drive for desktop not found. Install it from https://www.google.com/drive/download/ , sign in, then run: memory.py mirror auto")
        arg = os.path.join(gd, "basivo-memory")
    core.update_config(mirror_dir=os.path.expanduser(arg))
    ok, results = sync_now()
    print(json.dumps({"ok": ok, "mirror": arg, "sync": results}))


def cmd_status(start=None):
    s, rid = current_space(start)
    spaces = []
    for sp in core.all_spaces():
        spaces.append({"name": sp["name"], "repos": [r["id"] for r in sp["repos"]], "pinned": sp.get("pinned", []),
                       "notes": len(core.notes(sp["name"])),
                       "docs": sum(len((core.load(os.path.join(core.space_dir(sp["name"]), "docs", core.repo_key(r["id"]), "_manifest.json")) or {}).get("files", []))
                                   for r in sp["repos"])})
    cfg = core.config()
    until = core.paused_until(rid)
    print(json.dumps({"this_repo": rid, "this_space": s["name"] if s else None,
                      "paused": ("until resumed" if until == -1 else time.strftime("%Y-%m-%d %H:%M", time.localtime(until))) if until else False,
                      "spaces": spaces,
                      "github": cfg.get("github_repo"), "token": bool(cfg.get("github_token")),
                      "mirror": cfg.get("mirror_dir")}, indent=1))


def cmd_doctor():
    cfg = core.config()
    checks = {"os": sys.platform, "python": sys.version.split()[0], "data": core.DATA,
              "spaces": len(core.all_spaces()), "google_drive": storage.find_google_drive(),
              "journal_chats": os.path.isdir(core.JOURNAL_CHATS)}
    fix = []
    if cfg.get("github_repo"):
        if cfg.get("github_token"):
            ok, msg = storage.GitHubRemote(cfg["github_repo"], cfg["github_token"]).check()
            checks["github"] = {"repo": cfg["github_repo"], "ok": ok, "detail": msg}
            if not ok:
                fix.append(f"GitHub: {msg}. New token: {token_url()} then memory.py set-token")
        else:
            checks["github"] = {"repo": cfg["github_repo"], "ok": False, "detail": "no token on this machine"}
            fix.append(f"add a token: {token_url()} then memory.py set-token")
    elif not cfg.get("mirror_dir"):
        fix.append("no sync place yet: run /memory-setup <owner/repo> (memory still works on this machine)")
    if not checks["journal_chats"]:
        checks["note"] = "basivo-journal not set up here: conversations won't be searchable (docs and notes still are)"
    checks["ready"], checks["fix"] = not fix, fix
    print(json.dumps(checks, indent=1))


def main(args):
    cmd = args[0] if args else ""
    if cmd == "start":
        cmd_start()
    elif cmd == "end":
        cmd_end()
    elif cmd == "link" and len(args) >= 2:
        s, rid, n = core.link(args[1], pins=args[2:])
        sync_background(force=True)
        print(json.dumps({"ok": True, "space": s["name"], "repo": rid, "repos": [r["id"] for r in s["repos"]],
                          "docs_snapshotted": n, "pinned": s.get("pinned", [])}))
    elif cmd == "unlink":
        s = core.unlink()
        sync_background(force=True)
        print(json.dumps({"ok": bool(s), "space": s["name"] if s else None}))
    elif cmd in ("pin", "unpin") and len(args) == 2:
        s, ref = core.pin(args[1], remove=cmd == "unpin")
        sync_background(force=True)
        print(json.dumps({"ok": True, "space": s["name"], cmd + "ned": ref, "pinned": s["pinned"]}))
    elif cmd == "pause":
        rid, _, _ = core.repo_identity()
        hours = float(args[1]) if len(args) > 1 else None
        until = core.pause(rid, hours)
        print(json.dumps({"ok": True, "repo": rid, "paused_until": "until resumed" if until == -1 else time.strftime("%Y-%m-%d %H:%M", time.localtime(until))}))
    elif cmd == "resume":
        rid, _, _ = core.repo_identity()
        core.resume(rid)
        print(json.dumps({"ok": True, "repo": rid, "paused": False}))
    elif cmd == "status":
        cmd_status()
    elif cmd == "remember" and len(args) >= 3:
        s, rid = current_space()
        if not s:
            sys.exit("this repo isn't in a space: memory.py link <space>")
        n = core.remember(s["name"], " ".join(args[2:]), args[1], repo=rid)
        sync_background(force=True)
        print(json.dumps({"ok": True, "id": n["id"]}))
    elif cmd == "forget" and len(args) == 2:
        s, _ = current_space()
        print(json.dumps({"ok": bool(s and core.forget(s["name"], args[1]))}))
        sync_background(force=True)
    elif cmd == "search" and len(args) >= 2:
        s, _ = current_space()
        print(core.search(s, " ".join(args[1:])) if s else "this repo isn't in a space")
    elif cmd == "setup" and len(args) == 2:
        cmd_setup(args[1])
    elif cmd == "set-token":
        cmd_set_token(args[1] if len(args) > 1 else None)
    elif cmd == "mirror" and len(args) == 2:
        cmd_mirror(args[1])
    elif cmd == "sync":
        ok, results = sync_now()
        print(json.dumps({"ok": ok, "sync": results}))
    elif cmd == "doctor":
        cmd_doctor()
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except SystemExit:
        raise
    except ValueError as e:
        sys.exit(f"memory: {e}")
    except Exception:
        if (sys.argv[1:2] or [""])[0] in ("start", "end", "sync"):
            sys.exit(0)  # hooks must never break the session
        raise
