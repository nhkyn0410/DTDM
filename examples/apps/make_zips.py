"""Pack each example app into src/minicloud/console/samples/<name>.zip for the console's guide.

Run after changing an example:  uv run python examples/apps/make_zips.py
Each zip wraps its files in a folder named after the app, like GitHub's "Download ZIP".
"""

import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGET = ROOT / "src" / "minicloud" / "console" / "samples"
FIXED_TIME = (2026, 11, 26, 0, 0, 0)  # the same bytes on every run


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for app in sorted(p for p in HERE.iterdir() if p.is_dir()):
        out = TARGET / f"{app.name}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(p for p in app.rglob("*") if p.is_file()):
                info = zipfile.ZipInfo(f"{app.name}/{path.relative_to(app).as_posix()}", FIXED_TIME)
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
        print(f"{out.relative_to(ROOT).as_posix()}: {out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
