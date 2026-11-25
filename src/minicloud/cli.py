"""Admin commands, e.g.:

uv run minicloud migrate
uv run minicloud backup --dir backups
uv run minicloud make-admin an@example.com
uv run minicloud grant-credits org_1a2b3c4d5e6f 1000
uv run minicloud stats
"""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from . import backup as backups
from . import migrate
from .config import get_settings
from .db import make_engine, make_session_factory
from .models import Organization, User, utcnow
from .services import metering, stats


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="minicloud", description="Lệnh quản trị minicloud")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="nâng database lên bản schema mới nhất")
    save = sub.add_parser("backup", help="sao lưu database SQLite (an toàn khi API đang chạy)")
    save.add_argument("--dir", type=Path, default=Path("backups"), help="thư mục chứa bản sao")
    save.add_argument("--keep", type=int, default=14, help="giữ lại bao nhiêu bản mới nhất")
    make_admin = sub.add_parser("make-admin", help="cấp quyền admin nền tảng cho một user")
    make_admin.add_argument("email")
    grant = sub.add_parser("grant-credits", help="cộng credit cho một tổ chức")
    grant.add_argument("org_id")
    grant.add_argument("credits", type=int)
    grant.add_argument("--reason", default="admin grant")
    sub.add_parser(
        "stats", help="số liệu cho báo cáo chạy thử (JSON): người dùng, thời gian tạo máy"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.command == "backup":
        try:
            path = backups.backup(settings.database_url, args.dir, keep=args.keep)
        except backups.BackupError as exc:
            raise SystemExit(f"Sao lưu thất bại: {exc}") from None
        print(f"Đã sao lưu vào {path} ({path.stat().st_size // 1024} KiB)")
        return

    engine = make_engine(settings.database_url)
    try:
        result = migrate.upgrade(engine)
    except migrate.LegacySchemaError as exc:
        raise SystemExit(str(exc)) from None
    if args.command == "migrate":
        if result.adopted:
            then = "" if result.after == migrate.BASELINE else f", rồi nâng lên {result.after}"
            print(f"Database tạo trước khi có migration: ghi nhận là bản {migrate.BASELINE}{then}")
        elif result.before == result.after:
            print(f"Database đã ở bản mới nhất ({result.after})")
        elif result.before is None:
            print(f"Đã tạo database mới ở bản {result.after}")
        else:
            print(f"Đã nâng database từ {result.before} lên {result.after}")
        return

    with make_session_factory(engine)() as db:
        if args.command == "make-admin":
            user = db.scalar(select(User).where(User.email == args.email.lower()))
            if user is None:
                raise SystemExit(f"không có user {args.email}")
            user.is_platform_admin = True
            db.commit()
            print(f"{user.email} giờ là admin nền tảng")
        elif args.command == "grant-credits":
            org = db.get(Organization, args.org_id)
            if org is None:
                raise SystemExit(f"không có tổ chức {args.org_id}")
            metering.grant_credits(db, org, args.credits, args.reason)
            balance = org.balance_millicredits / 1000
            print(f"Đã cộng {args.credits} credit cho {org.name}, số dư {balance}")
        elif args.command == "stats":
            figures = stats.collect(db, utcnow(), settings.green_grid_kg_co2_per_kwh)
            print(json.dumps(figures, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
