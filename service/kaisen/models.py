from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGBLOB, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

# MySQL TEXT holds 64 KB; files may be 200 KB and briefs can be long.
LongText = Text().with_variant(MEDIUMTEXT(), "mysql")
LongBlob = LargeBinary().with_variant(LONGBLOB(), "mysql")

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    secret: Mapped[bool] = mapped_column(Boolean, default=False)  # value is Fernet-encrypted


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    created: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    brief: Mapped[str] = mapped_column(LongText)
    github_repo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # live preview on PicoPaaS
    app_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    app_hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deploy_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_deploy: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Bundle(Base):
    """A deployable tar.gz, fetched by the runtime image via an unguessable token (stored only as a hash)."""
    __tablename__ = "bundles"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LongBlob)
    created: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProjectFile(Base):
    __tablename__ = "project_files"
    __table_args__ = (UniqueConstraint("project_id", "path"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    path: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(LongText)
    size: Mapped[int] = mapped_column(Integer)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    # queued | running | waiting | done | stopped | error
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    pending: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # tool results held while waiting on ask_human
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="usage")
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class InboxItem(Base):
    __tablename__ = "inbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    kind: Mapped[str] = mapped_column(String(16))  # question | budget | refusal | limit | error | push | deploy
    text: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="open", index=True)  # open | closed
    created: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Lease(Base):
    __tablename__ = "lease"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    holder: Mapped[str] = mapped_column(String(64))
    token: Mapped[int] = mapped_column(Integer, default=1)
    expires: Mapped[datetime] = mapped_column(DateTime)
