from alembic import context

from kaisen import config, db, models  # noqa: F401  (models register on Base)

cfg = config.load()
engine = db.build_engine(cfg.database_url, cfg.database_ca_cert)

with engine.connect() as conn:
    context.configure(connection=conn, target_metadata=db.Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
