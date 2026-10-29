from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth, builder, config, db, export, settings_store
from .github import GitHubError
from .llm import ModelError
from .models import Event, InboxItem, Project, ProjectFile, Run, User
from .redact import redact, transcript_lines

router = APIRouter()
env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html"]))
env.filters["usd"] = lambda v: f"${(v or 0):.4f}"


def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
    sess = request.state.sess
    ctx.update(csrf=sess.csrf, flash=sess.pop_flash(), logged_in=bool(sess.uid))
    return HTMLResponse(env.get_template(name).render(**ctx), status_code=status)


def back(request: Request, url: str, msg: str | None = None) -> RedirectResponse:
    if msg:
        request.state.sess.flash(msg)
    return RedirectResponse(url, status_code=303)


def _secrets(s: Session) -> list[str]:
    return settings_store.load(s).secret_values()


# ---- setup / login -------------------------------------------------------------

@router.get("/setup")
def setup_page(request: Request, s: Session = Depends(db.get_db)):
    if auth.has_admin(s):
        return back(request, "/login")
    return render(request, "setup.html")


@router.post("/setup")
def setup_submit(request: Request, form=Depends(auth.csrf_form), s: Session = Depends(db.get_db)):
    try:
        auth.claim(s, form.get("code", ""), form.get("password", ""))
    except ValueError as e:
        return render(request, "setup.html", status=400, error=str(e))
    return back(request, "/login", "Admin password set. Log in.")


@router.get("/login")
def login_page(request: Request, s: Session = Depends(db.get_db)):
    if not auth.has_admin(s):
        return back(request, "/setup")
    return render(request, "login.html")


@router.post("/login")
def login_submit(request: Request, form=Depends(auth.csrf_form), s: Session = Depends(db.get_db)):
    if auth.locked_out(s, "login"):
        return render(request, "login.html", status=429, error="Too many failed attempts. Try again in 15 minutes.")
    user = s.scalars(select(User).limit(1)).first()
    if not user or not auth.verify_password(form.get("password", ""), user.password_hash):
        auth.record_failure(s, "login")
        return render(request, "login.html", status=401, error="Wrong password.")
    auth.clear_failures(s, "login")
    s.commit()
    request.state.sess.login(user.id)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request, form=Depends(auth.csrf_form)):
    request.state.sess.logout()
    return RedirectResponse("/login", status_code=303)


# ---- projects ---------------------------------------------------------------------

@router.get("/")
def projects(request: Request, user=Depends(auth.current_user), s: Session = Depends(db.get_db)):
    rows = []
    for p in s.scalars(select(Project).order_by(Project.id.desc())):
        run = s.scalars(select(Run).where(Run.project_id == p.id).order_by(Run.id.desc())).first()
        rows.append((p, run.status if run else "no runs", builder.spend_project(s, p.id)))
    return render(request, "projects.html", rows=rows)


@router.get("/projects/new")
def project_new(request: Request, user=Depends(auth.current_user)):
    return render(request, "project_new.html")


@router.post("/projects/new")
def project_create(request: Request, user=Depends(auth.current_user), form=Depends(auth.csrf_form),
                   s: Session = Depends(db.get_db)):
    name, brief = form.get("name", "").strip(), form.get("brief", "").strip()
    if not name or not brief or len(name) > 120 or len(brief) > 20000:
        return render(request, "project_new.html", status=400, error="Name and brief are required (brief up to 20000 chars).")
    p = builder.create_project(s, name, brief)
    s.commit()
    return back(request, f"/projects/{p.id}", "Project created.")


def _project(s: Session, pid: int) -> Project:
    p = s.get(Project, pid)
    if not p:
        raise HTTPException(404, "No such project")
    return p


@router.get("/projects/{pid}")
def project_page(pid: int, request: Request, user=Depends(auth.current_user), s: Session = Depends(db.get_db)):
    p = _project(s, pid)
    run = s.scalars(select(Run).where(Run.project_id == pid).order_by(Run.id.desc())).first()
    rs = settings_store.load(s)
    lines = transcript_lines(run.messages, rs.secret_values()) if run else []
    files = builder.Workspace(s, pid).list()
    return render(request, "project.html", p=p, run=run, lines=lines, files=files,
                  total=builder.spend_project(s, pid), repo=p.github_repo or rs.github_repo,
                  summary=redact(run.summary or "", rs.secret_values()) if run else "",
                  error=redact(run.error or "", rs.secret_values()) if run else "")


@router.post("/projects/{pid}/build")
def project_build(pid: int, request: Request, user=Depends(auth.current_user), form=Depends(auth.csrf_form),
                  s: Session = Depends(db.get_db)):
    _, msg = builder.start_build(s, _project(s, pid))
    s.commit()
    return back(request, f"/projects/{pid}", msg)


@router.get("/projects/{pid}/download")
def project_download(pid: int, user=Depends(auth.current_user), s: Session = Depends(db.get_db)):
    p = _project(s, pid)
    files = [(r.path, r.content) for r in s.scalars(select(ProjectFile).where(ProjectFile.project_id == pid))]
    return Response(export.build_tarball(p.slug, files), media_type="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="{p.slug}.tar.gz"'})


@router.post("/projects/{pid}/push")
def project_push(pid: int, request: Request, user=Depends(auth.current_user), form=Depends(auth.csrf_form),
                 s: Session = Depends(db.get_db)):
    p = _project(s, pid)
    rs = settings_store.load(s)
    repo = form.get("repo", "").strip() or rs.github_repo
    if not repo or not rs.github_token:
        return back(request, f"/projects/{pid}", "Set a GitHub repo and token first (Settings).")
    p.github_repo = repo
    files = [(r.path, r.content) for r in s.scalars(select(ProjectFile).where(ProjectFile.project_id == pid))]
    if not files:
        return back(request, f"/projects/{pid}", "Nothing to push yet.")
    gh = request.app.state.github_factory(rs.github_token)
    try:
        res = gh.push(repo, files, p.slug, rs.github_branch or None, f"Kaisen: {p.name}", bool(form.get("open_pr")))
    except GitHubError as e:
        return back(request, f"/projects/{pid}", redact(f"Push failed: {e}", rs.secret_values()))
    s.commit()
    msg = f"Pushed to {res.url}" + (f" (PR: {res.pr_url})" if res.pr_url else "") + (" (empty repo was seeded)" if res.seeded else "")
    return back(request, f"/projects/{pid}", msg)


# ---- settings ---------------------------------------------------------------------

def _settings_ctx(s: Session):
    rs = settings_store.load(s)
    return dict(rs=rs, secrets=settings_store.describe_secrets(rs), models=list(config.PRICES), efforts=config.EFFORTS)


@router.get("/settings")
def settings_page(request: Request, user=Depends(auth.current_user), s: Session = Depends(db.get_db)):
    return render(request, "settings.html", **_settings_ctx(s))


@router.post("/settings")
def settings_save(request: Request, user=Depends(auth.current_user), form=Depends(auth.csrf_form),
                  s: Session = Depends(db.get_db)):
    try:
        settings_store.save_form(s, form)
    except ValueError as e:
        s.rollback()
        return render(request, "settings.html", status=400, error=str(e), **_settings_ctx(s))
    s.commit()
    return back(request, "/settings", "Settings saved.")


@router.post("/settings/pause")
def settings_pause(request: Request, user=Depends(auth.current_user), form=Depends(auth.csrf_form),
                   s: Session = Depends(db.get_db)):
    rs = settings_store.load(s)
    settings_store.set_value(s, "paused", "0" if rs.paused else "1")
    s.commit()
    return back(request, "/settings", "Resumed." if rs.paused else "Paused: no further model calls.")


@router.post("/settings/test/{what}")
def settings_test(what: str, request: Request, user=Depends(auth.current_user), form=Depends(auth.csrf_form),
                  s: Session = Depends(db.get_db)):
    rs = settings_store.load(s)
    try:
        if what == "anthropic":
            if not rs.anthropic_api_key:
                raise ModelError("Anthropic API key is not set")
            msg = "Anthropic OK: model " + request.app.state.model_client_factory(rs).retrieve_model(rs.model)
        elif what == "github":
            if not (rs.github_repo and rs.github_token):
                raise GitHubError("GitHub repo and token are not set")
            msg = "GitHub " + request.app.state.github_factory(rs.github_token).check_repo(rs.github_repo)
        else:
            raise HTTPException(404)
    except (ModelError, GitHubError) as e:
        msg = f"Test failed: {e}"
    return back(request, "/settings", redact(msg, rs.secret_values()))


# ---- inbox / spend -----------------------------------------------------------------

@router.get("/inbox")
def inbox(request: Request, user=Depends(auth.current_user), s: Session = Depends(db.get_db)):
    items = s.scalars(select(InboxItem).where(InboxItem.status == "open").order_by(InboxItem.id)).all()
    sec = _secrets(s)
    return render(request, "inbox.html", items=[(i, redact(i.text, sec)) for i in items])


@router.post("/inbox/{iid}/answer")
def inbox_answer(iid: int, request: Request, user=Depends(auth.current_user), form=Depends(auth.csrf_form),
                 s: Session = Depends(db.get_db)):
    item = s.get(InboxItem, iid)
    text = form.get("answer", "").strip()
    if not item or item.status != "open" or item.kind != "question":
        return back(request, "/inbox", "Nothing to answer.")
    if not text:
        return back(request, "/inbox", "Answer is empty.")
    builder.answer_question(s, item, text[:10000])
    s.commit()
    return back(request, "/inbox", "Answered; the run will resume.")


@router.post("/inbox/{iid}/dismiss")
def inbox_dismiss(iid: int, request: Request, user=Depends(auth.current_user), form=Depends(auth.csrf_form),
                  s: Session = Depends(db.get_db)):
    item = s.get(InboxItem, iid)
    if item and item.status == "open" and item.kind != "question":
        item.status = "closed"
        s.commit()
    return back(request, "/inbox")


@router.get("/spend")
def spend(request: Request, user=Depends(auth.current_user), s: Session = Depends(db.get_db)):
    rs = settings_store.load(s)
    by_model = s.execute(select(Event.model, func.count(), func.sum(Event.usd)).group_by(Event.model)).all()
    by_project = s.execute(select(Project.name, func.coalesce(func.sum(Event.usd), 0.0)).outerjoin(
        Event, Event.project_id == Project.id).group_by(Project.id, Project.name).order_by(Project.id)).all()
    return render(request, "spend.html", rs=rs, today=builder.spend_today(s), by_model=by_model, by_project=by_project,
                  total=s.scalar(select(func.coalesce(func.sum(Event.usd), 0.0))))
