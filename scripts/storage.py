#!/usr/bin/env python3
"""Sync your journal files between this machine and any number of "remotes",
with no git, gh or brew: standard-library Python only, on Windows, macOS and Linux.

Remotes:
  GitHubRemote   a private GitHub repo, via the REST API and a fine-grained token
  FolderRemote   any folder, e.g. inside Google Drive / OneDrive / Dropbox / iCloud

basivo-memory stores everything under spaces/<space>/ (space.json, notes/,
docs/). Notes are one file each and append-only, so machines rarely touch the
same file; when both sides changed a file, the fresher copy wins
(updated_at, then content length).
"""
import base64
import glob
import hashlib
import json
import os
import io
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request

KINDS = ("spaces",)
TEXT_EXT = (".json", ".md", ".mdx", ".txt", ".rst", ".yaml", ".yml", ".toml")


def blob_sha(data: bytes) -> str:
    """Git's blob id, so local files compare directly with GitHub's tree listing."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def local_files(root):
    out = {}
    for kind in KINDS:
        for p in glob.glob(os.path.join(root, kind, "**", "*"), recursive=True):
            if os.path.isfile(p) and p.endswith(TEXT_EXT) and not p.endswith(".tmp"):
                rel = os.path.relpath(p, root).replace(os.sep, "/")
                with open(p, "rb") as f:
                    out[rel] = f.read()
    return out


def freshness(data: bytes) -> str:
    """How up to date a file is: its updated_at (JSON) or, for docs, its length."""
    try:
        d = json.loads(data)
        if isinstance(d, dict):
            return (d.get("updated_at") or d.get("created_at") or "") + "|%09d" % len(data)
    except Exception:
        pass
    return "|%09d" % len(data)


def write_local(root, rel, data):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{time.time_ns()}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


# ---------- HTTP (urllib, with curl fallback for Pythons missing CA certs) ----------

class HttpError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


def http(method, url, token, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "basivo-memory"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode(errors="replace"))
    except (urllib.error.URLError, OSError) as e:
        if "CERTIFICATE" not in str(e).upper() or not shutil.which("curl"):
            raise
    # curl fallback (some macOS python.org builds ship without CA certificates)
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-X", method, "-w", "\n%{http_code}"]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["--data-binary", "@-"]
    out = subprocess.run(cmd + [url], input=data, capture_output=True, timeout=timeout + 5).stdout.decode()
    text, _, code = out.rpartition("\n")
    if not code.isdigit() or int(code) >= 400:
        raise HttpError(int(code or 0), text)
    return json.loads(text) if text.strip() else {}


# ---------- remotes ----------

class FolderRemote:
    kind = "folder"

    def __init__(self, path):
        self.path = os.path.expanduser(path)
        self.label = f"folder {self.path}"

    def available(self):
        return os.path.isdir(os.path.dirname(self.path.rstrip("/\\")) or self.path)

    def listing(self):
        os.makedirs(self.path, exist_ok=True)
        return {rel: blob_sha(data) for rel, data in local_files(self.path).items()}

    def get(self, rel, _sha):
        with open(os.path.join(self.path, *rel.split("/")), "rb") as f:
            return f.read()

    def put(self, files, message):
        for rel, data in files.items():
            write_local(self.path, rel, data)


class GitHubRemote:
    kind = "github"
    API = "https://api.github.com"

    def __init__(self, repo, token, branch="main"):
        self.repo, self.token, self.branch = repo, token, branch
        self.label = f"github {repo}"

    def _url(self, path):
        return f"{self.API}/repos/{self.repo}/{path}"

    def available(self):
        return bool(self.token and self.repo)

    def check(self):
        """(ok, message): token valid and allowed to write to the repo?"""
        try:
            info = http("GET", f"{self.API}/repos/{self.repo}", self.token)
        except HttpError as e:
            if e.status == 401:
                return False, "token rejected (expired or revoked)"
            if e.status == 404:
                return False, "repo not found, or the token has no access to it"
            return False, str(e)
        if not info.get("private"):
            return False, "repo is PUBLIC: make it private first"
        if not (info.get("permissions") or {}).get("push"):
            return False, "token can read but not write (needs Contents: Read and write)"
        return True, "ok"

    def _head(self):
        try:
            ref = http("GET", self._url(f"git/ref/heads/{self.branch}"), self.token)
            return ref["object"]["sha"]
        except HttpError as e:
            if e.status in (404, 409):  # empty repository: create the branch with a README
                readme = base64.b64encode(b"# basivo-memory data (PRIVATE)\n\nKeep this repository private.\n").decode()
                http("PUT", self._url("contents/README.md"), self.token,
                     {"message": "init", "content": readme, "branch": self.branch})
                ref = http("GET", self._url(f"git/ref/heads/{self.branch}"), self.token)
                return ref["object"]["sha"]
            raise

    def listing(self):
        head = self._head()
        commit = http("GET", self._url(f"git/commits/{head}"), self.token)
        tree = http("GET", self._url(f"git/trees/{commit['tree']['sha']}?recursive=1"), self.token)
        return {e["path"]: e["sha"] for e in tree.get("tree", [])
                if e.get("type") == "blob" and e["path"].split("/")[0] in KINDS and e["path"].endswith(TEXT_EXT)}

    def get(self, rel, sha):
        blob = http("GET", self._url(f"git/blobs/{sha}"), self.token)
        return base64.b64decode(blob["content"])

    def get_many(self, rels):
        """All files in one download (repo tarball) instead of one request per file."""
        req = urllib.request.Request(self._url(f"tarball/{self.branch}"), headers={
            "Authorization": f"Bearer {self.token}", "User-Agent": "basivo-memory"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                raw = r.read()
        except (urllib.error.URLError, OSError):
            if not shutil.which("curl"):
                raise
            raw = subprocess.run(["curl", "-sSL", "--max-time", "300", "-H", f"Authorization: Bearer {self.token}",
                                  self._url(f"tarball/{self.branch}")], capture_output=True, check=True).stdout
        want, out = set(rels), {}
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for m in tar.getmembers():
                rel = m.name.split("/", 1)[-1]  # strip "<owner>-<repo>-<sha>/"
                if m.isfile() and rel in want:
                    out[rel] = tar.extractfile(m).read()
        return out

    def put(self, files, message):
        """One commit for many files (batched so each request stays small)."""
        items = sorted(files.items())
        batch, size = [], 0
        for rel, data in items + [(None, None)]:
            if rel is not None:
                batch.append((rel, data))
                size += len(data)
            if batch and (rel is None or len(batch) >= 80 or size > 4_000_000):
                self._commit(batch, message)
                batch, size = [], 0

    def _commit(self, batch, message, retry=True):
        head = self._head()
        base_tree = http("GET", self._url(f"git/commits/{head}"), self.token)["tree"]["sha"]
        entries = [{"path": rel, "mode": "100644", "type": "blob", "content": data.decode("utf-8")} for rel, data in batch]
        tree = http("POST", self._url("git/trees"), self.token, {"base_tree": base_tree, "tree": entries}, timeout=120)
        commit = http("POST", self._url("git/commits"), self.token, {"message": message, "tree": tree["sha"], "parents": [head]})
        try:
            http("PATCH", self._url(f"git/refs/heads/{self.branch}"), self.token, {"sha": commit["sha"]})
        except HttpError as e:
            if e.status == 422 and retry:  # another laptop pushed meanwhile: rebuild on the new head
                return self._commit(batch, message, retry=False)
            raise


# ---------- sync ----------

def sync(root, remote, message="journal sync"):
    """Two-way sync of sessions/ and chats/ with one remote. Returns a small report."""
    local = local_files(root)
    local_sha = {rel: blob_sha(d) for rel, d in local.items()}
    remote_sha = remote.listing()
    push, pulled = {}, 0
    missing = [rel for rel in remote_sha if rel not in local_sha]
    prefetched = {}
    if len(missing) > 20 and hasattr(remote, "get_many"):  # e.g. a new laptop: one download
        try:
            prefetched = remote.get_many(missing)
        except Exception:
            prefetched = {}
    for rel in set(local_sha) | set(remote_sha):
        l, r = local_sha.get(rel), remote_sha.get(rel)
        if l == r:
            continue
        if r is None:
            push[rel] = local[rel]
            continue
        theirs = prefetched.get(rel) if rel in prefetched and blob_sha(prefetched[rel]) == r else remote.get(rel, r)
        if l is None or freshness(theirs) > freshness(local[rel]):
            write_local(root, rel, theirs)
            pulled += 1
        else:
            push[rel] = local[rel]
    if push:
        remote.put(push, message)
    return {"remote": remote.label, "pushed": len(push), "pulled": pulled}


def find_google_drive():
    """Default Google Drive for desktop location on this machine, or None."""
    home = os.path.expanduser("~")
    cands = glob.glob(os.path.join(home, "Library", "CloudStorage", "GoogleDrive-*", "My Drive"))  # macOS
    cands += glob.glob(os.path.join(home, "Library", "CloudStorage", "GoogleDrive-*", "MyDrive"))
    cands += [os.path.join(home, "Google Drive", "My Drive"), os.path.join(home, "Google Drive")]
    if os.name == "nt":  # Windows mounts Drive as a letter, G: by default
        cands += [f"{d}:\\My Drive" for d in "GHIJKLMNOPQRSTUVWXYZ"]
    return next((c for c in cands if os.path.isdir(c)), None)
