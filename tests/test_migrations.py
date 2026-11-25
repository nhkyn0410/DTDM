"""Schema migrations: every database reaches the newest schema without losing data."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from minicloud import cli, migrate, models  # noqa: F401  (models registers the tables)
from minicloud.config import get_settings
from minicloud.db import Base


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'minicloud.db'}")
    yield engine
    engine.dispose()


def test_the_migrations_build_exactly_what_the_models_describe(engine):
    migrate.upgrade(engine)
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == [], (
        "models.py changed without a migration; run: "
        'uv run alembic revision --autogenerate -m "..." --rev-id <next number>'
    )


def test_the_app_starts_on_the_newest_revision(session):
    with session() as db:
        version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == migrate.head_revision()


def test_upgrading_again_changes_nothing(engine):
    head = migrate.head_revision()
    assert migrate.upgrade(engine) == (None, head, False)  # a new, empty database
    assert migrate.upgrade(engine) == (head, head, False)


def make_v01_database(engine) -> None:
    """The tables v0.1 made with create_all (the WSL staging database, too): revision 0001."""
    with engine.begin() as connection:
        command.upgrade(migrate.alembic_config(connection), migrate.BASELINE)
        connection.execute(text("DROP TABLE alembic_version"))


def test_a_database_made_before_migrations_is_adopted_with_its_data(engine):
    make_v01_database(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, name, is_platform_admin, created_at) "
                "VALUES ('usr_1', 'an@example.com', 'An', 1, '2026-11-25 00:00:00')"
            )
        )

    assert migrate.upgrade(engine) == (None, migrate.head_revision(), True)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT email FROM users")).scalar() == "an@example.com"


def test_an_unversioned_database_with_another_schema_is_refused(engine):
    make_v01_database(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE instances DROP COLUMN public_port"))
    with pytest.raises(migrate.LegacySchemaError, match="instances.public_port"):
        migrate.upgrade(engine)


def test_every_migration_can_be_undone_and_redone(engine):
    migrate.upgrade(engine)
    with engine.begin() as connection:
        command.downgrade(migrate.alembic_config(connection), "base")
        assert set(inspect(connection).get_table_names()) <= {"alembic_version"}
    assert migrate.upgrade(engine).after == migrate.head_revision()


@pytest.fixture
def cli_database(tmp_path, monkeypatch):
    path = tmp_path / "cli.db"
    monkeypatch.setenv("MINICLOUD_DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def test_the_cli_migrates_then_backs_up(cli_database, tmp_path, capsys):
    cli.main(["migrate"])
    assert "Đã tạo database mới ở bản" in capsys.readouterr().out
    cli.main(["migrate"])
    assert "đã ở bản mới nhất" in capsys.readouterr().out

    cli.main(["backup", "--dir", str(tmp_path / "backups")])
    assert "Đã sao lưu vào" in capsys.readouterr().out
    assert len(list((tmp_path / "backups").glob("minicloud-*.db"))) == 1
