"""Keeps the database schema current with Alembic.

The app calls `upgrade()` on startup and `minicloud migrate` calls it during a deploy, so a
new migration reaches every copy of the database without anyone deleting minicloud.db.
"""

import logging
from pathlib import Path
from typing import NamedTuple

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# Databases made before migrations existed (Base.metadata.create_all) hold this schema.
BASELINE = "0001"


class LegacySchemaError(RuntimeError):
    """An unversioned database whose tables do not match the baseline migration."""


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        config.attributes["connection"] = connection  # env.py runs on it
    return config


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def _columns(connection: Connection) -> dict[str, set[str]]:
    inspector = inspect(connection)
    return {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
        if table != "alembic_version"
    }


def _baseline_columns() -> dict[str, set[str]]:
    """Build the baseline schema in a throwaway database and read it back."""
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), BASELINE)
        return _columns(connection)


def _adopt_legacy(connection: Connection, existing: dict[str, set[str]]) -> None:
    missing: list[str] = []
    for table, columns in sorted(_baseline_columns().items()):
        if table not in existing:
            missing.append(table)
        else:
            missing += [f"{table}.{column}" for column in sorted(columns - existing[table])]
    if missing:
        raise LegacySchemaError(
            "Database có sẵn bảng nhưng chưa có bảng alembic_version, và thiếu: "
            + ", ".join(missing)
            + ". Nếu là dữ liệu thử thì xoá file database rồi chạy lại."
        )
    # Same tables as the first migration: record it as applied instead of creating them again.
    command.stamp(alembic_config(connection), BASELINE)
    log.info("adopted a database created before migrations as revision %s", BASELINE)


class Upgrade(NamedTuple):
    before: str | None  # None: an empty database, or one made before migrations
    after: str | None
    adopted: bool = False  # tables made by create_all were recorded as the baseline


def upgrade(engine: Engine) -> Upgrade:
    """Bring the database to the newest revision."""
    adopted = False
    with engine.begin() as connection:
        before = current_revision(connection)
        if before is None:
            existing = _columns(connection)
            if existing:
                _adopt_legacy(connection, existing)
                adopted = True
        command.upgrade(alembic_config(connection), "head")
        after = current_revision(connection)
    if before != after:
        log.info("database schema upgraded from %s to %s", before or "nothing", after)
    return Upgrade(before, after, adopted)
