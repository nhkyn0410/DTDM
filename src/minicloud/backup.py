"""Backups of the SQLite database: `minicloud backup`, run daily by a systemd timer.

SQLite's online backup API copies a consistent snapshot while the API keeps writing, which a
plain `cp` of the file does not guarantee. Each backup is checked before it replaces anything,
and only the newest few are kept.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

PREFIX = "minicloud-"
SUFFIX = ".db"


class BackupError(RuntimeError):
    pass


def sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise BackupError("chỉ sao lưu được SQLite; với PostgreSQL hãy dùng pg_dump")
    if not url.database or url.database == ":memory:":
        raise BackupError("database nằm trong bộ nhớ, không có file để sao lưu")
    return Path(url.database)


def backup(database_url: str, directory: Path, keep: int = 14, now: datetime | None = None) -> Path:
    """Copy the database to directory/minicloud-<UTC time>.db and keep the newest `keep`."""
    source = sqlite_path(database_url)
    if not source.is_file():
        raise BackupError(f"không thấy database {source}")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    target = directory / f"{PREFIX}{stamp}{SUFFIX}"
    partial = target.with_name(target.name + ".partial")
    partial.unlink(missing_ok=True)
    # mode=ro: a wrong path fails instead of creating an empty database.
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(partial)
        try:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    if result != "ok":
        partial.unlink(missing_ok=True)
        raise BackupError(f"bản sao lưu bị lỗi: {result}")
    partial.chmod(0o600)  # it holds e-mail addresses and API key hashes
    partial.replace(target)
    prune(directory, keep)
    return target


def list_backups(directory: Path) -> list[Path]:
    """Oldest first: the UTC time in the name sorts the same way as the dates."""
    return sorted(directory.glob(f"{PREFIX}*{SUFFIX}"))


def prune(directory: Path, keep: int) -> list[Path]:
    backups = list_backups(directory)
    removed = backups[:-keep] if keep > 0 else []
    for path in removed:
        path.unlink()
    return removed
