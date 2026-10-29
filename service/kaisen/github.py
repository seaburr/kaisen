"""GitHub push via the Git Data API. The token is only ever sent as an Authorization header."""
import base64
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubError(Exception):
    """Plain-language message, safe to show."""


@dataclass
class PushResult:
    branch: str
    commit_sha: str
    url: str
    pr_url: str | None = None
    seeded: bool = False


class GitHubClient(Protocol):
    def check_repo(self, repo: str) -> str: ...
    def push(self, repo: str, files: list[tuple[str, str]], slug: str, branch: str | None,
             message: str, open_pr: bool) -> PushResult: ...


def _plain(status: int, body: str) -> str:
    if status == 401:
        return "GitHub rejected the token (401)"
    if status == 403:
        return "GitHub refused the request (403): the token lacks permission, or rate limit reached"
    if status == 404:
        return "Not found (404): check the repo name and that the token can access it"
    return f"GitHub error {status}: {body[:200]}"


class RealGitHub:
    def __init__(self, token: str, base_url: str = "https://api.github.com", transport=None):
        self.http = httpx.Client(
            base_url=base_url, timeout=30, transport=transport,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "kaisen"},
        )

    def _req(self, method, path, ok_missing=False, **kw):
        try:
            r = self.http.request(method, path, **kw)
        except httpx.HTTPError:
            raise GitHubError("Could not reach GitHub") from None
        if ok_missing and r.status_code in (404, 409):
            return None
        if r.status_code >= 400:
            raise GitHubError(_plain(r.status_code, r.text))
        return r.json()

    def _repo(self, repo: str) -> dict:
        if not REPO_RE.match(repo):
            raise GitHubError("Repo must look like owner/name")
        return self._req("GET", f"/repos/{repo}")

    def check_repo(self, repo: str) -> str:
        info = self._repo(repo)
        if not (info.get("permissions") or {}).get("push"):
            raise GitHubError(f"Token can read {repo} but has no push permission")
        return f"OK: can push to {repo}"

    def _ref(self, repo, branch):
        r = self._req("GET", f"/repos/{repo}/git/ref/heads/{branch}", ok_missing=True)
        return r["object"]["sha"] if r else None

    def push(self, repo, files, slug, branch, message, open_pr) -> PushResult:
        info = self._repo(repo)
        base = info.get("default_branch") or "main"
        base_sha = self._ref(repo, base)
        seeded = False
        if base_sha is None:  # empty repository: the Git Data API needs a first commit
            self._req("PUT", f"/repos/{repo}/contents/README.md", json={
                "message": "Initial commit", "content": base64.b64encode(b"# Kaisen\n").decode()})
            base_sha = self._ref(repo, base)
            seeded = True
            if base_sha is None:
                raise GitHubError("Could not initialise the empty repository")
        target = branch or f"kaisen/{slug}"
        target_sha = base_sha if target == base else self._ref(repo, target)
        parent = target_sha or base_sha
        tree_sha = self._req("GET", f"/repos/{repo}/git/commits/{parent}")["tree"]["sha"]
        entries = []
        for path, content in files:
            blob = self._req("POST", f"/repos/{repo}/git/blobs", json={
                "content": base64.b64encode(content.encode()).decode(), "encoding": "base64"})
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        # On Kaisen's own branch the tree mirrors the project, so deleted files go away;
        # on any other branch, layer onto the existing tree so the user's files are kept.
        tree_req = {"tree": entries}
        if not target.startswith("kaisen/"):
            tree_req["base_tree"] = tree_sha
        tree = self._req("POST", f"/repos/{repo}/git/trees", json=tree_req)
        commit = self._req("POST", f"/repos/{repo}/git/commits",
                           json={"message": message, "tree": tree["sha"], "parents": [parent]})
        if target_sha:
            self._req("PATCH", f"/repos/{repo}/git/refs/heads/{target}", json={"sha": commit["sha"], "force": False})
        else:
            self._req("POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{target}", "sha": commit["sha"]})
        pr_url = None
        if open_pr and target != base:
            try:
                pr = self._req("POST", f"/repos/{repo}/pulls", json={
                    "title": message, "head": target, "base": base, "body": "Opened by Kaisen."})
                pr_url = pr.get("html_url")
            except GitHubError as e:
                if "422" not in str(e):  # 422 = a PR for this branch already exists
                    raise
        return PushResult(target, commit["sha"], f"https://github.com/{repo}/tree/{target}", pr_url, seeded)


@dataclass
class FakeGitHub:
    empty: bool = False
    can_push: bool = True
    pushes: list = field(default_factory=list)

    def check_repo(self, repo: str) -> str:
        if not REPO_RE.match(repo):
            raise GitHubError("Repo must look like owner/name")
        if not self.can_push:
            raise GitHubError(f"Token can read {repo} but has no push permission")
        return f"OK: can push to {repo}"

    def push(self, repo, files, slug, branch, message, open_pr) -> PushResult:
        if not REPO_RE.match(repo):
            raise GitHubError("Repo must look like owner/name")
        target = branch or f"kaisen/{slug}"
        self.pushes.append({"repo": repo, "branch": target, "files": dict(files), "pr": open_pr, "seeded": self.empty})
        seeded, self.empty = self.empty, False
        return PushResult(target, "f" * 40, f"https://github.com/{repo}/tree/{target}",
                          f"https://github.com/{repo}/pull/1" if open_pr else None, seeded)
