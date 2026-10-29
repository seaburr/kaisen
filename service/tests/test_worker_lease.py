import time
from datetime import timedelta

from kaisen import db, worker
from kaisen.llm import FakeModelClient
from kaisen.models import Lease as LeaseRow, utcnow
from kaisen.worker import Lease, Worker, tick


def test_lease_takeover_fences_stale_holder(ctx):
    a, b = Lease(holder="a", ttl=40), Lease(holder="b", ttl=40)
    assert a.acquire() and a.token == 1
    assert not b.acquire()  # a still holds it
    assert a.acquire()  # re-acquire while mine renews
    with db.session_scope() as s:
        s.get(LeaseRow, "worker").expires = utcnow() - timedelta(seconds=1)
    assert not a.check()
    assert b.acquire() and b.token == 2
    assert not a.renew() and not a.check() and b.check()
    assert not a.acquire()


def test_tick_idle_and_not_leader(ctx):
    assert tick(lambda rs: FakeModelClient(), Lease()) is False  # nothing queued
    other = Lease(holder="other")
    other.acquire()
    assert tick(lambda rs: FakeModelClient(), Lease()) is False  # lease held elsewhere


def test_worker_threads_loop_renew_and_stop(ctx, monkeypatch):
    calls = []

    def fake_tick(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")  # must be swallowed
        return False
    monkeypatch.setattr(worker, "tick", fake_tick)
    monkeypatch.setattr(worker, "RENEW_EVERY", 0.01)
    w = Worker(lambda rs: None)
    w.lease.token = 1
    renewed = []
    monkeypatch.setattr(w.lease, "renew", lambda: renewed.append(1) or (_ for _ in ()).throw(RuntimeError("x")) if len(renewed) == 0 else renewed.append(1))
    w.stop.wait = lambda t=None: w.stop.is_set() or time.sleep(0.01) or w.stop.is_set()
    w.start()
    deadline = time.time() + 3
    while (len(calls) < 2 or len(renewed) < 2) and time.time() < deadline:
        time.sleep(0.02)
    w.stop.set()
    for t in w.threads:
        t.join(2)
    assert len(calls) >= 2 and len(renewed) >= 2
