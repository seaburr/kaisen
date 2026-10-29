"""Background worker: DB lease with fencing token, then run queued builds."""
import logging
import threading
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from . import builder, db, outputs, settings_store
from .github import GitHubError
from .llm import ModelError
from .models import InboxItem, Lease as LeaseRow, Project, Run, utcnow
from .redact import redact

log = logging.getLogger("kaisen.worker")
LEASE_TTL = 40
RENEW_EVERY = 10


class Lease:
    """CAS lease. token only changes hands on takeover, so a stale holder fails check()."""

    def __init__(self, holder: str | None = None, ttl: int = LEASE_TTL):
        self.holder = holder or uuid.uuid4().hex
        self.ttl = ttl
        self.token: int | None = None

    def _exp(self):
        return utcnow() + timedelta(seconds=self.ttl)

    def acquire(self) -> bool:
        with db.session_scope() as s:
            row = s.get(LeaseRow, "worker")
            mine = bool(row and row.holder == self.holder and row.token == self.token and row.expires > utcnow())
        if mine:
            return self.renew()
        with db.session_scope() as s:
            row = s.get(LeaseRow, "worker")
            if row is None:
                try:
                    s.add(LeaseRow(name="worker", holder=self.holder, token=1, expires=self._exp()))
                    s.flush()
                except IntegrityError:
                    return False
                self.token = 1
                return True
            res = s.execute(update(LeaseRow).execution_options(synchronize_session=False).where(
                LeaseRow.name == "worker", LeaseRow.token == row.token, LeaseRow.expires < utcnow(),
            ).values(holder=self.holder, token=row.token + 1, expires=self._exp()))
            if res.rowcount == 1:
                self.token = row.token + 1
                return True
            return False

    def renew(self) -> bool:
        with db.session_scope() as s:
            res = s.execute(update(LeaseRow).execution_options(synchronize_session=False).where(
                LeaseRow.name == "worker", LeaseRow.holder == self.holder, LeaseRow.token == self.token,
            ).values(expires=self._exp()))
            return res.rowcount == 1

    def check(self) -> bool:
        with db.session_scope() as s:
            row = s.get(LeaseRow, "worker")
            return bool(row and row.holder == self.holder and row.token == self.token and row.expires > utcnow())


def tick(client_factory, lease: Lease, github_factory=None) -> bool:
    """Run one queued step if we hold the lease. Returns True if work was attempted."""
    if not lease.acquire():
        return False
    with db.session_scope() as s:
        run_id = s.scalar(select(Run.id).where(Run.status.in_(("queued", "running"))).order_by(Run.id).limit(1))
        rs = settings_store.load(s)
    if run_id is None:
        return False
    try:
        client = client_factory(rs)
    except ModelError as e:
        builder.stop_run(run_id, "error", "error", str(e))
        return True
    if builder.run_step(run_id, client, lease) == "done" and github_factory is not None:
        _push_on_finish(run_id, github_factory)
    return True


def _push_on_finish(run_id: int, github_factory) -> None:
    """If the customer turned pushes on, push the finished project; report either way in the inbox."""
    with db.session_scope() as s:
        rs = settings_store.load(s)
        if not (rs.push_enabled and rs.push_on_finish):
            return
        run = s.get(Run, run_id)
        p = s.get(Project, run.project_id)
        try:
            text = outputs.push_project(s, p, rs, github_factory)
        except GitHubError as e:
            text = redact(f"Automatic push failed: {e}. Fix Settings > Output, then press Push on the project page.",
                          rs.secret_values())
        s.add(InboxItem(project_id=p.id, run_id=run_id, kind="push", text=text))


class Worker:
    def __init__(self, client_factory, github_factory=None):
        self.client_factory = client_factory
        self.github_factory = github_factory
        self.lease = Lease()
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self):
        for target in (self._loop, self._renew):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self.threads.append(t)

    def _loop(self):
        while not self.stop.is_set():
            try:
                busy = tick(self.client_factory, self.lease, self.github_factory)
            except Exception:
                log.exception("worker tick failed")
                busy = False
            self.stop.wait(0.5 if busy else 2.0)

    def _renew(self):
        while not self.stop.wait(RENEW_EVERY):
            try:
                if self.lease.token is not None:
                    self.lease.renew()
            except Exception:
                log.exception("lease renew failed")
