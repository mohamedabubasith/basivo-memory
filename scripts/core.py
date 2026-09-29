#!/usr/bin/env python3
"""basivo-memory core: shared memory "spaces" across several repos.

A space groups repos that belong together (e.g. a spec/rules repo and the code
repo). Its memory = snapshots of the repos' docs + remembered notes (decisions,
conventions) + the conversations you had in any of those repos (read from
basivo-journal, if installed). Everything lives in ~/.basivo-memory/data and
syncs to your private repo / Google Drive via storage.py.
"""
import datetime as dt
import glob
import hashlib
import json
import os
import random
import re
import socket
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mask_pii import mask_prose  # noqa: E402

HOME = os.path.expanduser("~")
DIR = os.environ.get("BASIVO_MEMORY_HOME", os.path.join(HOME, ".basivo-memory"))
DATA = os.path.join(DIR, "data")
CONFIG = os.path.join(DIR, "config.json")
STATE = os.path.join(DIR, "state.json")
INDEX = os.path.join(DIR, "index.db")
JOURNAL_CHATS = os.path.join(os.environ.get("BASIVO_JOURNAL_HOME", os.path.join(HOME, ".basivo-journal")), "data", "chats")

DOC_EXT = (".md", ".mdx", ".txt", ".rst")
DOC_DIRS = ("docs", "doc", "spec", "specs", "rules", "adr", "adrs", "design", "requirements", ".claude")
SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", "vendor", "venv", ".venv", "__pycache__", "target", "coverage"}
MAX_DOC_BYTES = 200_000
MAX_DOCS_PER_REPO = 400
PINNED_BUDGET = 3500      # characters of pinned rules in the session card (~900 tokens)
DEFAULT_CARD_TOKENS = 1000  # whole session card; change per space with `memory.py budget <tokens>`
MIN_CARD_TOKENS, MAX_CARD_TOKENS = 150, 4000


def est_tokens(text):
    return (len(text) + 3) // 4  # rough: ~4 characters per token
RECENT_NOTES = 6
MAX_OUT = 6000
NOTE_KINDS = ("decision", "convention", "rule", "todo", "fact")


# ---------- small helpers ----------

def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{time.time_ns()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, path)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{time.time_ns()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def clip(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def cap(text):
    return text if len(text) <= MAX_OUT else text[:MAX_OUT] + "\n… [truncated]"


def config():
    return load(CONFIG, {}) or {}


def update_config(**changes):
    os.makedirs(DIR, exist_ok=True)
    cfg = config()
    for k, v in changes.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    save(CONFIG, cfg)
    try:
        os.chmod(CONFIG, 0o600)
    except OSError:
        pass
    return cfg


# ---------- repo identity (no git binary needed) ----------

def repo_root(start):
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _git_config(root):
    g = os.path.join(root, ".git")
    if os.path.isfile(g):  # worktree / submodule: "gitdir: <path>"
        try:
            target = open(g).read().split("gitdir:", 1)[1].strip()
            g = os.path.normpath(os.path.join(root, target))
            common = os.path.join(g, "commondir")
            if os.path.exists(common):
                g = os.path.normpath(os.path.join(g, open(common).read().strip()))
        except Exception:
            return ""
    try:
        return open(os.path.join(g, "config"), encoding="utf-8").read()
    except Exception:
        return ""


def normalize_remote(url):
    u = url.strip()
    u = re.sub(r"^[a-z+]+://", "", u)          # https://, ssh://, git://
    u = re.sub(r"^[^@/]+@", "", u)             # git@, user:token@
    u = u.replace(":", "/", 1) if re.match(r"^[^/]+:[^/]", u) else u
    u = re.sub(r"\.git$", "", u).rstrip("/")
    return u.lower()


def repo_identity(start=None):
    """(repo_id, local_root, display_name). repo_id is stable across laptops (the origin URL)."""
    root = repo_root(start)
    if not root:
        root = os.path.abspath(start or os.getcwd())
        return "local/" + os.path.basename(root).lower(), root, os.path.basename(root)
    cfgtxt = _git_config(root)
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', cfgtxt, re.S)
    rid = normalize_remote(m.group(1)) if m else "local/" + os.path.basename(root).lower()
    return rid, root, rid.split("/")[-1]


def repo_key(repo_id):
    return re.sub(r"[^a-z0-9._-]+", "__", repo_id.lower())


# ---------- spaces ----------

def space_dir(name):
    return os.path.join(DATA, "spaces", name)


def valid_space(name):
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", name or ""))


def all_spaces():
    out = []
    for p in sorted(glob.glob(os.path.join(DATA, "spaces", "*", "space.json"))):
        s = load(p)
        if s and s.get("name"):
            out.append(s)
    return out


def space_for_repo(repo_id):
    for s in all_spaces():
        if any(r["id"] == repo_id for r in s.get("repos", [])):
            return s
    return None


def remember_path(repo_id, root):
    """This machine's folder for a repo (paths differ per laptop)."""
    cfg = config()
    paths = cfg.get("paths", {})
    if paths.get(repo_id) != root:
        paths[repo_id] = root
        update_config(paths=paths)


def link(space, start=None, pins=None):
    if not valid_space(space):
        raise ValueError("space names use lowercase letters, digits, . _ - (e.g. chatbot)")
    rid, root, name = repo_identity(start)
    other = space_for_repo(rid)
    if other and other["name"] != space:
        raise ValueError(f"this repo is already in space '{other['name']}' (run memory unlink first)")
    path = os.path.join(space_dir(space), "space.json")
    s = load(path) or {"name": space, "created_at": now_iso(), "repos": [], "pinned": []}
    if not any(r["id"] == rid for r in s["repos"]):
        s["repos"].append({"id": rid, "name": name, "added_at": now_iso()})
    for p in pins or []:
        ref = f"{repo_key(rid)}/{p.strip('/')}"
        if ref not in s["pinned"]:
            s["pinned"].append(ref)
    s["updated_at"] = now_iso()
    save(path, s)
    remember_path(rid, root)
    n = snapshot_docs(s, rid, root)
    return s, rid, n


def set_budget(space_name, tokens):
    tokens = max(MIN_CARD_TOKENS, min(int(tokens), MAX_CARD_TOKENS))
    path = os.path.join(space_dir(space_name), "space.json")
    s = load(path)
    if not s:
        raise ValueError(f"no space named '{space_name}'")
    s["card_budget_tokens"], s["updated_at"] = tokens, now_iso()
    save(path, s)
    return tokens


def unlink(start=None):
    rid, _, _ = repo_identity(start)
    s = space_for_repo(rid)
    if not s:
        return None
    s["repos"] = [r for r in s["repos"] if r["id"] != rid]
    key = repo_key(rid) + "/"
    s["pinned"] = [p for p in s.get("pinned", []) if not p.startswith(key)]
    s["updated_at"] = now_iso()
    save(os.path.join(space_dir(s["name"]), "space.json"), s)
    man = os.path.join(space_dir(s["name"]), "docs", repo_key(rid), "_manifest.json")
    if os.path.exists(man):
        save(man, {"repo": rid, "files": [], "updated_at": now_iso()})  # logical removal syncs everywhere
    return s


def pin(ref_path, start=None, remove=False):
    rid, _, _ = repo_identity(start)
    s = space_for_repo(rid)
    if not s:
        raise ValueError("this repo isn't in a space yet: run /memory-link <space>")
    ref = ref_path if "/" in ref_path and ref_path.split("/")[0] in {repo_key(r["id"]) for r in s["repos"]} \
        else f"{repo_key(rid)}/{ref_path.strip('/')}"
    s.setdefault("pinned", [])
    if remove:
        s["pinned"] = [p for p in s["pinned"] if p != ref]
    elif ref not in s["pinned"]:
        s["pinned"].append(ref)
    s["updated_at"] = now_iso()
    save(os.path.join(space_dir(s["name"]), "space.json"), s)
    return s, ref


# ---------- pause (per machine, per repo) ----------

def paused_until(repo_id):
    """Epoch seconds the repo's memory is paused until (0 = not paused, -1 = until resumed)."""
    v = (config().get("paused") or {}).get(repo_id, 0)
    if v and v != -1 and v < time.time():
        return 0
    return v


def pause(repo_id, hours=None):
    p = config().get("paused") or {}
    p[repo_id] = -1 if not hours else time.time() + float(hours) * 3600
    update_config(paused=p)
    return p[repo_id]


def resume(repo_id):
    p = config().get("paused") or {}
    p.pop(repo_id, None)
    update_config(paused=p)


# ---------- doc snapshots (so docs travel to laptops without the repo) ----------

def doc_files(root):
    """Markdown / text docs anywhere in the repo (not build or dependency folders).
    If there are more than MAX_DOCS_PER_REPO, the top level and docs-like folders win."""
    found = []
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not (d.startswith(".") and d != ".claude")]
        for fn in files:
            if fn.lower().endswith(DOC_EXT):
                p = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(p) <= MAX_DOC_BYTES:
                        found.append(os.path.relpath(p, root).replace(os.sep, "/"))
                except OSError:
                    pass
    def priority(rel):
        parts = rel.split("/")
        return (0 if len(parts) == 1 else 1 if parts[0].lower() in DOC_DIRS else 2, len(parts), rel)
    return sorted(sorted(found, key=priority)[:MAX_DOCS_PER_REPO])


def snapshot_docs(space, rid, root):
    """Copy this repo's docs (secrets masked) into the space. Returns files written."""
    base = os.path.join(space_dir(space["name"]), "docs", repo_key(rid))
    files = doc_files(root)
    written = 0
    for rel in files:
        try:
            text = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        text = mask_prose(text)
        dest = os.path.join(base, *rel.split("/"))
        try:
            if open(dest, encoding="utf-8").read() == text:
                continue
        except OSError:
            pass
        write_text(dest, text)
        written += 1
    man_path = os.path.join(base, "_manifest.json")
    man = load(man_path) or {}
    if written or man.get("files") != files:
        save(man_path, {"repo": rid, "files": files, "updated_at": now_iso()})
    return written


# ---------- notes ----------

def notes(space_name, include_deleted=False):
    out = []
    for p in glob.glob(os.path.join(space_dir(space_name), "notes", "*.json")):
        n = load(p)
        if n and (include_deleted or not n.get("deleted")):
            out.append(n)
    return sorted(out, key=lambda n: n.get("created_at", ""), reverse=True)


def remember(space_name, text, kind="decision", tags=None, repo=None):
    text = mask_prose((text or "").strip())
    if not text:
        raise ValueError("nothing to remember")
    kind = kind if kind in NOTE_KINDS else "fact"
    nid = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + "%04x" % random.getrandbits(16)
    note = {"id": nid, "kind": kind, "text": text[:4000], "tags": [t for t in (tags or []) if t][:8],
            "repo": repo, "machine": socket.gethostname().split(".")[0], "created_at": now_iso(), "updated_at": now_iso()}
    save(os.path.join(space_dir(space_name), "notes", nid + ".json"), note)
    return note


def forget(space_name, nid):
    p = os.path.join(space_dir(space_name), "notes", nid + ".json")
    n = load(p)
    if not n:
        return False
    n["deleted"], n["updated_at"] = True, now_iso()  # tombstone, so deletion syncs to other laptops
    save(p, n)
    return True


# ---------- search index ----------

def _chunks(text, size=1400):
    """Split a doc by headings, then by size."""
    parts, cur, head = [], [], ""
    for line in text.splitlines():
        if re.match(r"^#{1,4}\s", line) and cur:
            parts.append((head, "\n".join(cur)))
            cur = []
        if re.match(r"^#{1,4}\s", line):
            head = line.lstrip("#").strip()
        cur.append(line)
    if cur:
        parts.append((head, "\n".join(cur)))
    out = []
    for h, body in parts:
        for i in range(0, len(body), size):
            out.append((h, body[i:i + size]))
    return out


# ---------- conversations (saved by this plugin; basivo-journal chats are read too) ----------

MSG_MAX_CHARS = 6000
_NOISE = re.compile(r"<(system-reminder|ide_[a-z_]+|local-command-stdout|local-command-caveat|command-message|command-args)>.*?</\1>", re.S)
CLAUDE_PROJECTS = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"), "projects")


def _clean(text):
    text = _NOISE.sub("", text or "")
    text = re.sub(r"<command-name>(.*?)</command-name>", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MSG_MAX_CHARS:
        text = text[:MSG_MAX_CHARS] + f"\n… [{len(text) - MSG_MAX_CHARS} more characters not stored]"
    return mask_prose(text)


def _content_text(content):
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def parse_transcript(path):
    """A Claude Code transcript (.jsonl) as {session_id, title, cwd, started_at, messages}: only what was said, masked."""
    sid = cwd = title = started = None
    msgs = []
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            sid, cwd = sid or d.get("sessionId"), cwd or d.get("cwd")
            started = started or d.get("timestamp")
            typ, msg = d.get("type"), d.get("message") or {}
            if typ == "ai-title" and d.get("aiTitle"):
                title = d["aiTitle"]
            if d.get("isSidechain"):
                continue
            if typ == "user" and (d.get("turnOrigin", (d.get("origin") or {}).get("kind")) == "human"
                                  or (not d.get("toolUseResult") and isinstance(msg.get("content"), str))):
                txt = _clean(_content_text(msg.get("content")))
                if txt:
                    msgs.append({"role": "user", "ts": d.get("timestamp"), "text": txt})
            elif typ == "assistant":
                txt = _content_text(msg.get("content"))
                if txt.strip():
                    txt = _clean(txt)
                    if msgs and msgs[-1]["role"] == "assistant":  # one reply may span several records
                        msgs[-1]["text"] = (msgs[-1]["text"] + "\n\n" + txt)[: MSG_MAX_CHARS * 2]
                    else:
                        msgs.append({"role": "assistant", "ts": d.get("timestamp"), "text": txt})
    if not sid or not msgs:
        return None
    if not title:
        title = clip(next((m["text"] for m in msgs if m["role"] == "user"), ""), 80)
    return {"session_id": sid, "title": title, "cwd": cwd, "started_at": started, "messages": msgs}


def save_chat(transcript_path, start=None):
    """Store this session's conversation in its repo's space. Skipped when unlinked, paused or chats are off."""
    if config().get("record_chat") is False or not transcript_path:
        return None
    c = parse_transcript(transcript_path)
    if not c:
        return None
    rid, root, _ = repo_identity(start or c.get("cwd"))
    s = space_for_repo(rid)
    if not s or paused_until(rid):
        return None
    doc = {"session_id": c["session_id"], "repo": rid, "title": c["title"],
           "project": os.path.basename(root), "started_at": c["started_at"], "messages": c["messages"]}
    path = os.path.join(space_dir(s["name"]), "chats", c["session_id"] + ".json")
    if load(path) == doc:
        return None
    save(path, doc)
    return path


def import_chats(start=None):
    """Save the past conversations Claude Code still has on this machine for a repo (run when it's linked)."""
    _, root, _ = repo_identity(start)
    folders = {os.path.join(CLAUDE_PROJECTS, re.sub(r"[^A-Za-z0-9]", "-", r)) for r in (root, os.path.realpath(root))}
    return sum(1 for d in folders for p in glob.glob(os.path.join(d, "*.jsonl")) if save_chat(p, root))


def _chat_files(space):
    """(path, chat) for every conversation in the space: this plugin's own, then basivo-journal's for the same repos."""
    ids = {r["id"] for r in space.get("repos", [])}
    out, seen = [], set()
    for p in glob.glob(os.path.join(space_dir(space["name"]), "chats", "*.json")):
        c = load(p)
        if c and c.get("repo") in ids:
            out.append((p, c))
            seen.add(c["session_id"])
    out += [(p, c) for p, c in _journal_chat_files(space) if c.get("session_id") not in seen]
    return out


def _journal_chat_files(space):
    names = {r["id"].split("/")[-1].lower() for r in space.get("repos", [])}
    names |= {os.path.basename(p).lower() for rid, p in config().get("paths", {}).items()
              if any(r["id"] == rid for r in space.get("repos", []))}
    out = []
    for p in glob.glob(os.path.join(JOURNAL_CHATS, "*", "*", "*.json")):
        c = load(p)
        if c and (c.get("project") or "").rstrip("/").split("/")[-1].lower() in names:
            out.append((p, c))
    return out


def _signature(space):
    items = []
    for p in glob.glob(os.path.join(space_dir(space["name"]), "**", "*"), recursive=True):
        if os.path.isfile(p):
            items.append((p, os.path.getmtime(p)))
    for p, _ in _journal_chat_files(space):  # own chats are already under space_dir
        items.append((p, os.path.getmtime(p)))
    return hashlib.sha1(json.dumps(sorted(items)).encode()).hexdigest()


def index(space):
    """Open the index for a space, rebuilding it only when its sources changed."""
    os.makedirs(DIR, exist_ok=True)
    db = sqlite3.connect(INDEX)
    db.executescript("""
      create table if not exists meta(space text primary key, sig text);
      create virtual table if not exists chunks using fts5(
        space unindexed, kind unindexed, ref unindexed, title unindexed, day unindexed, text,
        tokenize='porter unicode61');""")
    sig = _signature(space)
    row = db.execute("select sig from meta where space = ?", (space["name"],)).fetchone()
    if row and row[0] == sig:
        return db
    name = space["name"]
    db.execute("delete from chunks where space = ?", (name,))
    names = {repo_key(r["id"]): r["name"] for r in space.get("repos", [])}
    for key, rname in names.items():
        man = load(os.path.join(space_dir(name), "docs", key, "_manifest.json")) or {}
        for rel in man.get("files", []):
            try:
                text = open(os.path.join(space_dir(name), "docs", key, *rel.split("/")), encoding="utf-8").read()
            except OSError:
                continue
            for i, (head, body) in enumerate(_chunks(text)):
                db.execute("insert into chunks values (?,?,?,?,?,?)",
                           (name, "doc", f"doc:{key}/{rel}#{i}", f"{rname}/{rel}" + (f" › {head}" if head else ""), "", body))
    for n in notes(name):
        db.execute("insert into chunks values (?,?,?,?,?,?)",
                   (name, "note", f"note:{n['id']}", n["kind"] + (" · " + ", ".join(n["tags"]) if n.get("tags") else ""),
                    n["created_at"][:10], n["text"]))
    seen = set()
    for _, c in _chat_files(space):
        for i, m in enumerate(c.get("messages") or []):
            h = hashlib.sha1((m.get("text") or "").encode()).hexdigest()
            if not (m.get("text") or "").strip() or (c["session_id"], h) in seen:
                continue
            seen.add((c["session_id"], h))
            proj = (c.get("project") or "").rstrip("/").split("/")[-1]
            db.execute("insert into chunks values (?,?,?,?,?,?)",
                       (name, "chat", f"chat:{c['session_id']}#{i}", f"{proj} · {clip(c.get('title') or '', 60)} · {m['role']}",
                        (m.get("ts") or c.get("started_at") or "")[:10], m["text"]))
    db.execute("insert or replace into meta values (?,?)", (name, sig))
    db.commit()
    return db


def _fts(q, mode):
    words = [w for w in re.findall(r"[\w][\w.+#-]*", (q or "").lower()) if len(w) > 1][:12]
    return (" AND " if mode == "and" else " OR ").join('"' + w + '"' for w in words) if words else None


def search(space, query, kind=None, limit=6):
    db = index(space)
    limit = max(1, min(int(limit or 6), 12))
    hits = []
    for mode in ("and", "or"):
        fq = _fts(query, mode)
        if not fq:
            return "Give me a few words to search for."
        sql = ("select kind, ref, title, day, snippet(chunks, 5, '«', '»', '…', 26) from chunks "
               "where chunks match ? and space = ?" + (" and kind = ?" if kind else "") + " order by rank limit ?")
        args = [fq, space["name"]] + ([kind] if kind else []) + [limit * 3]
        hits = db.execute(sql, args).fetchall()
        if hits:
            break
    out, per_ref = [], {}
    for k, ref, title, day, snip in hits:
        base = ref.split("#")[0]
        if per_ref.get(base, 0) >= 2:
            continue
        per_ref[base] = per_ref.get(base, 0) + 1
        out.append(f"- [{k}] {title}" + (f" · {day}" if day else "") + f" · ref {ref}\n  {clip(snip, 260)}")
        if len(out) >= limit:
            break
    if not out:
        return f"Nothing in the '{space['name']}' memory matches “{query}”."
    return cap(f"{len(out)} result(s) in space '{space['name']}' (open with memory_read):\n" + "\n".join(out))


def read(space, ref, query=None):
    ref = (ref or "").strip()
    if ref.startswith("note:"):
        n = load(os.path.join(space_dir(space["name"]), "notes", ref[5:] + ".json"))
        if not n or n.get("deleted"):
            return "No such note."
        return f"{n['kind']} · {n['created_at'][:10]} · {', '.join(n.get('tags') or []) or '-'} · repo {n.get('repo') or '-'}\n{n['text']}"
    if ref.startswith("doc:"):
        body = ref[4:].split("#")[0]
        key, _, rel = body.partition("/")
        p = os.path.join(space_dir(space["name"]), "docs", key, *rel.split("/"))
        try:
            text = open(p, encoding="utf-8").read()
        except OSError:
            return "No such document in this space."
        if query:
            parts = [b for h, b in _chunks(text) if any(w in b.lower() for w in (query or "").lower().split())]
            text = "\n\n".join(parts[:4]) or text
        elif "#" in ref:
            i = int(ref.split("#")[1] or 0)
            ch = _chunks(text)
            text = "\n\n".join(b for _, b in ch[max(0, i - 0): i + 2])
        return cap(f"{rel}\n\n{text}")
    if ref.startswith("chat:"):
        sid, _, pos = ref[5:].partition("#")
        for p in glob.glob(os.path.join(space_dir(space["name"]), "chats", sid + ".json")) + \
                glob.glob(os.path.join(JOURNAL_CHATS, "*", "*", sid + ".json")):
            c = load(p) or {}
            msgs = c.get("messages") or []
            i = int(pos or 0)
            pick = msgs[max(0, i - 2): i + 3]
            return cap(f"session {sid[:8]} · {c.get('title', '')}\n" + "\n".join(
                f"[{max(0, i - 2) + j}] {m['role']}: {clip(m['text'], 1200)}" for j, m in enumerate(pick)))
        return "That chat isn't on this machine (is basivo-journal set up here?)."
    return "Refs look like doc:…, note:…, or chat:… (from memory_search)."


# ---------- session card ----------

def card(start):
    rid, root, name = repo_identity(start)
    s = space_for_repo(rid)
    if not s:
        spaces = [x["name"] for x in all_spaces()]
        if not spaces:
            return ""
        return (f"basivo-memory: this repo ({name}) isn't in a memory space. Spaces: {', '.join(spaces)}. "
                "If it belongs with one, the user can run /memory-link <space>.")
    remember_path(rid, root)
    until = paused_until(rid)
    if until:
        when = "until you resume it" if until == -1 else "until " + dt.datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")
        return (f"basivo-memory: shared memory for space '{s['name']}' is PAUSED in this repo {when}. "
                "Don't use memory tools here; work only from this repo and the user. (/memory-resume turns it back on.)")
    try:
        snapshot_docs(s, rid, root)
    except Exception:
        pass
    repos = ", ".join(r["name"] for r in s["repos"])
    total = int(s.get("card_budget_tokens") or DEFAULT_CARD_TOKENS) * 4  # characters
    footer = ("Use memory_search to look up specs, rules, past decisions and conversations from ANY repo in this space "
              "before assuming. Memory is reference and can be out of date: if it conflicts with the current code or "
              "with what the user asks now, follow the code and the user, and mention the conflict (offer to update "
              "or forget the old note). When the user agrees on a decision, convention or rule, ask \"Save this to the "
              f"'{s['name']}' memory?\" and call memory_remember only after they say yes.")
    header = (f"Shared memory space '{s['name']}' (repos: {repos}). This repo is one of them, so the rules and "
              "decisions below apply here too.")
    lines = [header]
    budget = max(0, min(PINNED_BUDGET * total // (DEFAULT_CARD_TOKENS * 4), total - len(header) - len(footer) - 600))
    for ref in s.get("pinned", []):
        key, _, rel = ref.partition("/")
        try:
            text = open(os.path.join(space_dir(s["name"]), "docs", key, *rel.split("/")), encoding="utf-8").read().strip()
        except OSError:
            continue
        repo_name = next((r["name"] for r in s["repos"] if repo_key(r["id"]) == key), key)
        chunk = text[: max(0, budget)]
        if not chunk:
            lines.append(f"(more pinned files not shown: {ref}; use memory_read)")
            break
        lines.append(f"--- pinned: {repo_name}/{rel} ---\n{chunk}" + ("\n… (truncated, use memory_read)" if len(text) > len(chunk) else ""))
        budget -= len(chunk)
    room = total - sum(len(x) + 1 for x in lines) - len(footer) - 40
    ns = [n for n in notes(s["name"]) if n["kind"] in ("decision", "convention", "rule")][:RECENT_NOTES]
    note_lines = []
    for n in ns:
        line = f"- [{n['kind']}] {clip(n['text'], 220)} ({n['created_at'][:10]})"
        if room - len(line) < 0:
            break
        note_lines.append(line)
        room -= len(line) + 1
    if note_lines:
        lines.append("Recent decisions and conventions:\n" + "\n".join(note_lines))
    lines.append(footer)
    text = "\n".join(lines)
    return text if len(text) <= total else text[: total - 1] + "…"
