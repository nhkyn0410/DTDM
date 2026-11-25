"""Daily database backups: consistent copies, checked, and only the newest few kept."""

import sqlite3
from datetime import UTC, datetime

import pytest

from minicloud import backup


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "minicloud.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE t (x INTEGER)")
        con.execute("INSERT INTO t VALUES (42)")
    return path


def rows(path) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT x FROM t ORDER BY x").fetchall()
    finally:
        con.close()


def test_a_backup_is_a_checked_copy_named_by_its_time(database, tmp_path):
    when = datetime(2026, 11, 25, 3, 0, tzinfo=UTC)
    path = backup.backup(f"sqlite:///{database}", tmp_path / "backups", now=when)
    assert path.name == "minicloud-20261125-030000.db"
    assert rows(path) == [(42,)]
    assert list((tmp_path / "backups").glob("*.partial")) == []


def test_only_the_newest_backups_are_kept(database, tmp_path):
    folder = tmp_path / "backups"
    for day in range(1, 6):
        when = datetime(2026, 11, day, tzinfo=UTC)
        backup.backup(f"sqlite:///{database}", folder, keep=3, now=when)
    assert [p.name[10:18] for p in backup.list_backups(folder)] == [
        "20261103",
        "20261104",
        "20261105",
    ]


def test_a_backup_while_the_api_is_writing_holds_only_committed_data(database, tmp_path):
    writer = sqlite3.connect(database, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")  # the API in the middle of a transaction
        writer.execute("INSERT INTO t VALUES (7)")
        path = backup.backup(f"sqlite:///{database}", tmp_path / "backups")
    finally:
        writer.execute("ROLLBACK")
        writer.close()
    assert rows(path) == [(42,)]


@pytest.mark.parametrize(
    "url, message",
    [
        ("postgresql+psycopg://minicloud@db/minicloud", "pg_dump"),
        ("sqlite://", "bộ nhớ"),
    ],
)
def test_only_sqlite_files_can_be_backed_up(url, message, tmp_path):
    with pytest.raises(backup.BackupError, match=message):
        backup.backup(url, tmp_path / "backups")


def test_a_wrong_path_fails_without_creating_an_empty_database(tmp_path):
    missing = tmp_path / "typo.db"
    with pytest.raises(backup.BackupError, match="không thấy"):
        backup.backup(f"sqlite:///{missing}", tmp_path / "backups")
    assert not missing.exists()
