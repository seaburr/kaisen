"""GitHub push of a project's files, shared by the Push button and push-on-finish."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Project, ProjectFile
from .settings_store import RuntimeSettings


def push_project(s: Session, p: Project, rs: RuntimeSettings, github_factory) -> str:
    """Push to the repo in Settings and return a message for the user. Raises GitHubError."""
    files = [(r.path, r.content) for r in s.scalars(select(ProjectFile).where(ProjectFile.project_id == p.id))]
    if not files:
        return "Nothing to push yet."
    res = github_factory(rs.github_token).push(
        rs.github_repo, files, p.slug, rs.github_branch or None, f"Kaisen: {p.name}", rs.open_pr)
    p.github_repo = rs.github_repo
    return (f"Pushed to {res.url}" + (f" (PR: {res.pr_url})" if res.pr_url else "")
            + (" (empty repo was seeded)" if res.seeded else ""))
