"""Alembic environment.

Runs in two ways: inside the app, which hands over its own connection (see minicloud.migrate),
or from the `alembic` command line when a developer writes a new migration.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

from minicloud import models  # noqa: F401  (registers every table on Base.metadata)
from minicloud.config import get_settings
from minicloud.db import Base

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_with(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER most things: batch mode copies the table instead.
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # called by the app
        run_with(connection)
        return
    if config.config_file_name:  # called from the alembic CLI: use alembic.ini logging
        fileConfig(config.config_file_name)
    engine = engine_from_config(
        {"sqlalchemy.url": database_url()}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        run_with(connection)


if context.is_offline_mode():
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    run_online()
