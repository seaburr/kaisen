"""First-run claim, password check, lockout, signed session cookie with CSRF token."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, crypto, db
from .models import LoginAttempt, Setting, User, utcnow

log = logging.getLogger("kaisen.auth")
COOKIE = "kaisen_session"
SESSION_AGE = 12 * 3600
MAX_FAILURES, WINDOW = 5, timedelta(minutes=15)


def _ser():
    return URLSafeTimedSerializer(config.get().secret_key, salt="kaisen-session")


class Sess:
    def __init__(self, data: dict):
        self.data = data

    @property
    def csrf(self) -> str:
        return self.data["csrf"]

    @property
    def uid(self):
        return self.data.get("uid")

    def login(self, uid: int):
        self.data = {"csrf": secrets.token_urlsafe(24), "uid": uid}

    def logout(self):
        self.data = {"csrf": secrets.token_urlsafe(24)}

    def flash(self, msg: str):
        self.data["flash"] = msg

    def pop_flash(self):
        return self.data.pop("flash", None)

    def dumps(self) -> str:
        return _ser().dumps(self.data)


def load_session(request: Request) -> Sess:
    raw = request.cookies.get(COOKIE)
    if raw:
        try:
            return Sess(_ser().loads(raw, max_age=SESSION_AGE))
        except BadSignature:
            pass
    return Sess({"csrf": secrets.token_urlsafe(24)})


def get_sess(request: Request) -> Sess:
    return request.state.sess


# ---- password ---------------------------------------------------------------

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(calc.hex(), h)
    except ValueError:
        return False


# ---- lockout ------------------------------------------------------------------

def locked_out(s: Session, scope: str) -> bool:
    since = utcnow() - WINDOW
    n = s.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.scope == scope, LoginAttempt.at >= since))
    return n >= MAX_FAILURES


def record_failure(s: Session, scope: str) -> None:
    s.add(LoginAttempt(scope=scope))
    s.commit()


def clear_failures(s: Session, scope: str) -> None:
    s.query(LoginAttempt).filter(LoginAttempt.scope == scope).delete()


# ---- first-run claim ------------------------------------------------------------

def has_admin(s: Session) -> bool:
    return s.scalar(select(func.count()).select_from(User)) > 0


def ensure_setup_code() -> str | None:
    """If no admin exists, make (or reuse) a one-time setup code and log it. Returns it (tests read this)."""
    with db.session_scope() as s:
        if has_admin(s):
            return None
        row = s.get(Setting, "setup_code")
        if row is None:
            code = secrets.token_urlsafe(12)
            s.add(Setting(key="setup_code", value=crypto.encrypt(code), secret=True))
        else:
            code = crypto.decrypt(row.value)
    log.warning("KAISEN SETUP CODE: %s  (open /setup, enter this code and choose a password)", code)
    print(f"KAISEN SETUP CODE: {code}", flush=True)
    return code


def claim(s: Session, code: str, password: str) -> None:
    """Raises ValueError with a user-facing message. Code is single use."""
    if locked_out(s, "setup"):
        raise ValueError("Too many attempts. Try again in 15 minutes.")
    if has_admin(s):
        raise ValueError("Already set up.")
    row = s.get(Setting, "setup_code")
    if row is None or not hmac.compare_digest(crypto.decrypt(row.value), code.strip()):
        record_failure(s, "setup")
        raise ValueError("Wrong setup code.")
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")
    s.add(User(password_hash=hash_password(password)))
    s.delete(row)
    s.commit()


# ---- dependencies -----------------------------------------------------------------

def current_user(request: Request, s: Session = Depends(db.get_db)) -> User:
    if not has_admin(s):
        raise HTTPException(303, headers={"Location": "/setup"})
    uid = request.state.sess.uid
    user = s.get(User, uid) if uid else None
    if not user:
        raise HTTPException(303, headers={"Location": "/login"})
    return user


async def csrf_form(request: Request):
    form = await request.form()
    if not hmac.compare_digest(str(form.get("csrf", "")), request.state.sess.csrf):
        raise HTTPException(403, "Bad CSRF token")
    return form
