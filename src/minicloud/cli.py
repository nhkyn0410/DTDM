"""Admin commands, e.g.:

uv run minicloud make-admin an@example.com
uv run minicloud grant-credits org_1a2b3c4d5e6f 1000
"""

import argparse

from sqlalchemy import select

from .config import get_settings
from .db import Base, make_engine, make_session_factory
from .models import Organization, User
from .services import metering


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="minicloud", description="Lệnh quản trị minicloud")
    sub = parser.add_subparsers(dest="command", required=True)
    make_admin = sub.add_parser("make-admin", help="cấp quyền admin nền tảng cho một user")
    make_admin.add_argument("email")
    grant = sub.add_parser("grant-credits", help="cộng credit cho một tổ chức")
    grant.add_argument("org_id")
    grant.add_argument("credits", type=int)
    grant.add_argument("--reason", default="admin grant")
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
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


if __name__ == "__main__":
    main()
