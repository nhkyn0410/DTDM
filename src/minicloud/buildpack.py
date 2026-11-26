#!/usr/bin/env python3
"""minicloud buildpack: unpack an uploaded app, recognise its stack, install it, serve port 80.

One file, two callers:

- The control plane imports inspect_archive() and detect() to check an upload before it
  creates anything, so a customer learns at once that a zip cannot work.
- The worker copies this file into the customer's own container and runs it there as root
  (python3 minicloud-buildpack.py minicloud-app.zip). The customer's code is unpacked, built
  and run only inside that container, never on a laptop's host.

Only the standard library: a fresh Ubuntu container has python3 and little else. Progress goes
to stdout as a few short lines (the worker keeps the first 64 KB); the full output of every
command goes to /var/log/minicloud-deploy.log in the container. The last stdout line is
"MINICLOUD-RESULT {json}", which the worker reads.
"""

import http.client
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RESULT_MARKER = "MINICLOUD-RESULT "
MAX_FILES = 5000
MAX_UNPACKED_BYTES = 300 * 1024 * 1024
MAX_READ_BYTES = 256 * 1024  # biggest file read to recognise a stack (package.json, ...)
# Left out when unpacking: rebuilt in the container, or useless on Linux (a Windows venv).
SKIPPED_DIRS = frozenset({"__MACOSX", ".git", "node_modules", ".venv", "venv", "__pycache__"})
SKIPPED_FILES = frozenset({".DS_Store", "Thumbs.db"})

APP_DIR = Path("/srv/app")
NEW_DIR = Path("/srv/app.new")  # the next version is built here while the current one serves
OLD_DIR = Path("/srv/app.old")
DATA_DIR = Path("/srv/data")  # kept across deployments, unlike the app folder
VENV_DIR = Path("/srv/venv")
START_SCRIPT = Path("/srv/minicloud-start.sh")
UNIT_FILE = Path("/etc/systemd/system/minicloud-app.service")
LOG_FILE = Path("/var/log/minicloud-deploy.log")
NODE_VERSION = "22.14.0"  # 22.12+ honours --report-exclude-network (see Deployer.env)
FALLBACK_PORTS = (3000, 8080, 8000, 5000, 4000, 5173)  # where apps listen when they ignore PORT
COMMAND_TIMEOUT = 600  # seconds for one install or build command
SERVE_TIMEOUT = 120  # seconds for the app to answer on port 80 once started

SUPPORTED = (
    "web tĩnh (index.html), Node.js (package.json), Python (requirements.txt với Flask, "
    "FastAPI hoặc Django), PHP (index.php), hoặc Procfile có dòng 'web: <lệnh chạy>'"
)
NODE_FRAMEWORKS = (
    ("next", "Next.js"),
    ("nuxt", "Nuxt"),
    ("@sveltejs/kit", "SvelteKit"),
    ("@angular/core", "Angular"),
    ("react-scripts", "Create React App"),
    ("vite", "Vite"),
    ("@nestjs/core", "NestJS"),
    ("express", "Express"),
    ("fastify", "Fastify"),
    ("koa", "Koa"),
)
NODE_ENTRIES = ("server.js", "index.js", "app.js", "main.js", "server.mjs", "index.mjs")
PY_ENTRIES = ("app.py", "main.py", "server.py", "wsgi.py")
STATIC_ROOTS = ("", "public", "dist", "build", "site", "www", "docs", "html")
BUILD_OUTPUTS = ("dist", "build", "out", "public")
SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")  # file names we put into a start command


class UploadError(Exception):
    """The upload cannot be deployed. The message is for the customer, in Vietnamese."""


# -- the archive ---------------------------------------------------------------------------


@dataclass
class Archive:
    """A checked zip: app paths (a shared top folder removed) mapped to their zip entries."""

    zip: zipfile.ZipFile
    members: dict[str, zipfile.ZipInfo]

    @property
    def names(self) -> list[str]:
        return sorted(self.members)

    def read_text(self, name: str) -> str | None:
        """A small file of the app as text; None when it is missing or too big to matter."""
        info = self.members.get(name)
        if info is None or info.file_size > MAX_READ_BYTES:
            return None
        try:
            return self.zip.read(info).decode("utf-8", errors="replace")
        except (RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error, EOFError):
            raise UploadError(
                f"Không đọc được {name} trong zip. Hãy nén lại bằng kiểu nén thường "
                "(Deflate), không đặt mật khẩu."
            ) from None


def inspect_archive(data: bytes) -> Archive:
    """Check a zip without unpacking it and drop what a deployment never needs."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError):
        raise UploadError("File tải lên không phải file zip hợp lệ.") from None
    members: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in infos:
        name = info.filename.replace("\\", "/")  # zips made by Windows PowerShell 5.1
        parts = [p for p in name.split("/") if p not in ("", ".")]
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in parts:
            raise UploadError(f"Zip có đường dẫn không hợp lệ: {name[:100]}")
        if name.endswith("/") or not parts:
            continue  # a folder entry
        if any(p in SKIPPED_DIRS for p in parts[:-1]) or parts[-1] in SKIPPED_FILES:
            continue
        if info.flag_bits & 0x1:
            raise UploadError("Zip có đặt mật khẩu; hãy nén lại không mật khẩu.")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise UploadError(f"Zip có liên kết tượng trưng (symlink), chưa hỗ trợ: {name[:100]}")
        members["/".join(parts)] = info
        total += info.file_size
        if len(members) > MAX_FILES:
            raise UploadError(
                f"Zip có hơn {MAX_FILES} file. Bỏ các thư mục như node_modules, .git, venv "
                "rồi nén lại."
            )
    if not members:
        raise UploadError("File zip không có file nào.")
    if total > MAX_UNPACKED_BYTES:
        raise UploadError(f"Giải nén ra hơn {MAX_UNPACKED_BYTES // 2**20} MB, quá giới hạn.")
    for _ in range(3):  # "Download ZIP" on GitHub wraps everything in "<repo>-main/"
        tops = {name.split("/", 1)[0] for name in members}
        if len(tops) != 1 or any("/" not in name for name in members):
            break
        cut = len(tops.pop()) + 1
        members = {name[cut:]: info for name, info in members.items()}
    return Archive(archive, members)


# -- recognising the stack ------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    stack: str  # static, node, node-static, python, php, procfile
    label: str  # shown to the customer
    start: str = ""  # shell command that serves the app on $PORT (node, python, procfile)
    root: str = ""  # static and php: the folder served, relative to the app
    build: bool = False  # run `npm run build` before serving
    spa: bool = False  # static output of a JS build: unknown paths get index.html
    node: bool = False
    python: bool = False
    pip_extra: tuple[str, ...] = ()
    django: bool = False
    composer: bool = False


def detect(names: list[str], read: Callable[[str], str | None]) -> Plan:
    """Decide how to build and serve an app from its file list (and a few small files)."""
    files = set(names)
    python_markers = bool({"requirements.txt", "pyproject.toml", "manage.py"} & files)
    package = _package_json(read) if "package.json" in files else None
    scripts = _scripts(package) if package is not None else {}
    procfile = _procfile_command(read("Procfile") or "") if "Procfile" in files else ""
    if procfile:
        return Plan(
            "procfile",
            f"Procfile ({_short(procfile)})",
            start=procfile,
            node=package is not None,
            build="build" in scripts,
            python=python_markers,
        )
    framework = _framework(package) if package is not None else None
    if "start" in scripts:
        label = f"Node.js ({framework}, npm start)" if framework else "Node.js (npm start)"
        return Plan("node", label, start="npm start", node=True, build="build" in scripts)
    if python_markers:
        return _python_plan(files, read)
    if package is not None:
        if "build" in scripts:
            label = f"Web tĩnh dựng bằng {framework or 'Node.js'} (npm run build)"
            return Plan("node-static", label, node=True, build=True, spa=True)
        main = package.get("main")
        entry = main if isinstance(main, str) and main in files else None
        entry = entry or next((e for e in NODE_ENTRIES if e in files), None)
        if entry and SAFE_PATH.match(entry):
            return Plan("node", f"Node.js (node {entry})", start=f"node {entry}", node=True)
    if any(e in files for e in PY_ENTRIES[:3]):
        return _python_plan(files, read)
    if any(n.endswith(".php") for n in files):
        root = next((d for d in STATIC_ROOTS if _join(d, "index.php") in files), "")
        return Plan("php", "PHP (Apache)", root=root, composer="composer.json" in files)
    for folder in STATIC_ROOTS:
        if _join(folder, "index.html") in files or _join(folder, "index.htm") in files:
            where = f" trong {folder}/" if folder else ""
            return Plan("static", f"Web tĩnh{where} (nginx)", root=folder)
    raise UploadError(f"Không nhận ra công nghệ của ứng dụng. minicloud hiểu: {SUPPORTED}.")


def _join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def _short(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _package_json(read: Callable[[str], str | None]) -> dict:
    text = read("package.json")
    if text is None:
        raise UploadError("package.json quá lớn hoặc không đọc được.")
    try:
        package = json.loads(text)
    except ValueError as exc:
        raise UploadError(f"package.json không phải JSON hợp lệ: {exc}") from None
    if not isinstance(package, dict):
        raise UploadError("package.json phải là một object JSON.")
    return package


def _scripts(package: dict) -> dict[str, str]:
    scripts = package.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {k: v for k, v in scripts.items() if isinstance(k, str) and isinstance(v, str)}


def _framework(package: dict) -> str | None:
    deps: dict = {}
    for key in ("dependencies", "devDependencies"):
        if isinstance(package.get(key), dict):
            deps.update(package[key])
    return next((label for name, label in NODE_FRAMEWORKS if name in deps), None)


def _procfile_command(text: str) -> str:
    for line in text.splitlines():
        kind, sep, command = line.partition(":")
        if sep and kind.strip() == "web" and command.strip():
            return command.strip()
    return ""


def _python_plan(files: set[str], read: Callable[[str], str | None]) -> Plan:
    deps = "\n".join(t for t in (read("requirements.txt"), read("pyproject.toml")) if t).lower()

    def needs(package: str) -> bool:
        return (
            re.search(rf"(^|[^a-z0-9_-]){re.escape(package)}([^a-z0-9_-]|$)", deps, re.M)
            is not None
        )

    if "manage.py" in files:
        wsgi = sorted(n for n in files if n.endswith("/wsgi.py") and n.count("/") == 1)
        if wsgi and SAFE_PATH.match(wsgi[0]):
            module = wsgi[0][:-3].replace("/", ".")
            return Plan(
                "python",
                "Python (Django, gunicorn)",
                start=f"gunicorn {module}:application --bind 0.0.0.0:$PORT",
                python=True,
                pip_extra=("gunicorn",),
                django=True,
            )
    if needs("fastapi"):
        module, var = _find_app(files, read, "FastAPI") or ("main", "app")
        return Plan(
            "python",
            "Python (FastAPI, uvicorn)",
            start=f"uvicorn {module}:{var} --host 0.0.0.0 --port $PORT",
            python=True,
            pip_extra=("uvicorn",),
        )
    if needs("flask"):
        module, var = _find_app(files, read, "Flask") or ("app", "app")
        return Plan(
            "python",
            "Python (Flask, gunicorn)",
            start=f"gunicorn {module}:{var} --bind 0.0.0.0:$PORT",
            python=True,
            pip_extra=("gunicorn",),
        )
    entry = next((e for e in PY_ENTRIES if e in files), None)
    if entry:
        return Plan("python", f"Python (python {entry})", start=f"python {entry}", python=True)
    raise UploadError(
        "Có requirements.txt nhưng không biết chạy file nào: không phải Flask, FastAPI hay "
        "Django, và không có app.py hay main.py. Thêm Procfile với dòng 'web: <lệnh chạy>'."
    )


def _find_app(
    files: set[str], read: Callable[[str], str | None], cls: str
) -> tuple[str, str] | None:
    """Module and variable of `app = Flask(...)` or `app = FastAPI(...)` in the app's code."""
    pattern = re.compile(rf"^(\w+)\s*(?::[^=\n]*)?=\s*{cls}\(", re.M)
    first = [e for e in PY_ENTRIES if e in files]
    others = sorted(n for n in files if n.endswith(".py") and n.count("/") <= 1 and n not in first)
    for name in (first + others)[:50]:
        if not SAFE_PATH.match(name):
            continue
        match = pattern.search(read(name) or "")
        if match:
            return name[:-3].replace("/", "."), match.group(1)
    return None


# -- configuration written into the container ----------------------------------------------


def nginx_static(root: Path, spa: bool) -> str:
    fallback = "/index.html" if spa else "=404"
    return (
        "server {\n"
        "    listen 80 default_server;\n"
        "    listen [::]:80 default_server;\n"
        f"    root {root};\n"
        "    index index.html index.htm;\n"
        "    location / {\n"
        f"        try_files $uri $uri/ {fallback};\n"
        "    }\n"
        "}\n"
    )


def nginx_proxy(port: int) -> str:
    return (
        "server {\n"
        "    listen 80 default_server;\n"
        "    listen [::]:80 default_server;\n"
        "    location / {\n"
        f"        proxy_pass http://127.0.0.1:{port};\n"
        "        proxy_http_version 1.1;\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        "        proxy_set_header X-Forwarded-Proto https;\n"
        "    }\n"
        "}\n"
    )


def apache_site(root: Path) -> str:
    return (
        "<VirtualHost *:80>\n"
        f"    DocumentRoot {root}\n"
        f"    <Directory {root}>\n"
        "        Options -Indexes +FollowSymLinks\n"
        "        AllowOverride All\n"
        "        Require all granted\n"
        "        DirectoryIndex index.php index.html\n"
        "    </Directory>\n"
        "    ErrorLog ${APACHE_LOG_DIR}/error.log\n"
        "    CustomLog ${APACHE_LOG_DIR}/access.log combined\n"
        "</VirtualHost>\n"
    )


def start_script(command: str) -> str:
    return (
        "#!/bin/bash\n"
        "# Written by the minicloud buildpack; the next deployment replaces it.\n"
        f"export PORT=80 HOST=0.0.0.0 DATA_DIR={DATA_DIR} NODE_ENV=production PYTHONUNBUFFERED=1\n"
        'export NODE_OPTIONS="--report-exclude-network ${NODE_OPTIONS:-}"\n'
        f"export PATH={VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        "export HOME=/root\n"
        f"cd {APP_DIR}\n"
        f"exec {command}\n"
    )


UNIT = """[Unit]
Description=Customer app deployed by minicloud
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/srv/minicloud-start.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
"""


# -- running inside the container ----------------------------------------------------------


class StepFailed(Exception):
    def __init__(self, message: str, tail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.tail = tail


def say(text: str) -> None:
    print(f"==> {text}", flush=True)
    _log(f"\n==> {text}\n")


def _log(text: str) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _tail(text: str, lines: int = 40, chars: int = 6000) -> str:
    kept = "\n".join(text.rstrip().splitlines()[-lines:])
    return kept[-chars:]


def run(
    command: list[str],
    *,
    what: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = COMMAND_TIMEOUT,
    check: bool = True,
) -> str:
    """Run a command with its output in the log; on failure raise StepFailed with the tail."""
    _log(f"$ {' '.join(command)}\n")
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.output or b"").decode("utf-8", errors="replace")
        _log(out)
        raise StepFailed(f"{what}: chạy quá {timeout // 60} phút", _tail(out)) from None
    except FileNotFoundError:
        raise StepFailed(f"{what}: không có lệnh {command[0]}") from None
    out = proc.stdout.decode("utf-8", errors="replace")
    _log(out)
    if check and proc.returncode != 0:
        raise StepFailed(f"{what} thất bại (mã thoát {proc.returncode})", _tail(out))
    return out


def download(url: str, attempts: int = 3) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (https, fixed host)
                return resp.read()
        except (OSError, http.client.HTTPException) as exc:
            if attempt == attempts:
                raise StepFailed(f"Không tải được {url}: {exc}") from None
            time.sleep(3 * attempt)
    raise AssertionError("unreachable")


def answers(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3):  # noqa: S310
            return True
    except urllib.error.HTTPError:
        return True  # it answered, even if with an error page
    except (OSError, http.client.HTTPException, ValueError):
        return False


def listening_ports() -> set[int]:
    ports: set[int] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 3 and fields[3] == "0A":  # LISTEN
                ports.add(int(fields[1].rsplit(":", 1)[1], 16))
    return ports


def unpack(archive: Archive, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    written = 0
    for name, info in archive.members.items():
        dest = target.joinpath(*name.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with archive.zip.open(info) as source, dest.open("wb") as out:
            while chunk := source.read(1 << 20):
                written += len(chunk)
                if written > MAX_UNPACKED_BYTES:
                    raise UploadError("Giải nén vượt giới hạn dung lượng.")
                out.write(chunk)
        dest.chmod(0o755 if (info.external_attr >> 16) & 0o111 else 0o644)


class Deployer:
    """The steps for one plan, run as root inside the customer's container."""

    def __init__(self, plan: Plan) -> None:
        self.plan = plan
        self.apt_updated = False
        self.env = {
            **os.environ,
            "HOME": "/root",
            "PATH": f"{VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "DEBIAN_FRONTEND": "noninteractive",
            "CI": "true",
            "npm_config_update_notifier": "false",
            "npm_config_fund": "false",
            "npm_config_audit": "false",
            # npm calls process.report.getReport() to tell glibc from musl, and Node then looks
            # up the name of every open connection, synchronously. Where reverse DNS goes
            # unanswered (staging: 10 s per lookup), an Express install took 7 minutes; with
            # this flag and Node 22.14 it takes 7 seconds (22.11 ignores the flag here).
            "NODE_OPTIONS": "--report-exclude-network",
            "npm_config_fetch_timeout": "60000",
            "npm_config_fetch_retries": "5",
            "npm_config_fetch_retry_mintimeout": "2000",
            "npm_config_fetch_retry_maxtimeout": "10000",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
        }

    # preparation

    def prepare(self) -> None:
        if shutil.which("cloud-init"):  # a new container is still setting itself up
            run(
                ["cloud-init", "status", "--wait"],
                what="Chờ máy khởi động xong",
                timeout=180,
                check=False,
            )
        deadline = time.monotonic() + 60
        while True:
            try:
                socket.getaddrinfo("archive.ubuntu.com", 443)
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise StepFailed("Máy chưa có mạng ra Internet sau 60 giây") from None
                time.sleep(2)

    def apt_install(self, *packages: str) -> None:
        missing = [p for p in packages if not self._installed(p)]
        if not missing:
            return
        lock = ["-o", "DPkg::Lock::Timeout=300"]
        if not self.apt_updated:
            run(
                ["apt-get", *lock, "update", "-qq"],
                what="Cập nhật danh sách gói",
                env=self.env,
                timeout=300,
            )
            self.apt_updated = True
        say(f"Cài {', '.join(missing)}")
        run(
            ["apt-get", *lock, "install", "-y", "-qq", "--no-install-recommends", *missing],
            what=f"Cài {', '.join(missing)}",
            env=self.env,
        )

    @staticmethod
    def _installed(package: str) -> bool:
        proc = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", package],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0 and "install ok installed" in proc.stdout

    # runtimes and dependencies, in the new folder

    def install(self, app: Path) -> None:
        plan = self.plan
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if plan.node:
            self.install_node()
            self.node_dependencies(app)
        if plan.python:
            self.python_dependencies(app)
        if plan.stack in ("static", "node-static"):
            self.apt_install("nginx")
        if plan.stack == "php":
            self.apt_install("apache2", "libapache2-mod-php")
            if plan.composer:
                self.apt_install("composer")
                say("Cài thư viện PHP (composer install)")
                run(
                    ["composer", "install", "--no-dev", "--no-interaction", "--prefer-dist"],
                    what="Cài thư viện PHP",
                    cwd=app,
                    env={**self.env, "COMPOSER_ALLOW_SUPERUSER": "1"},
                )

    def _node_version(self) -> str:
        node = shutil.which("node", path=self.env["PATH"])
        if not node:
            return ""
        return subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=False
        ).stdout.strip()

    def install_node(self) -> None:
        # Exactly this version: older ones make npm slow here (see NODE_OPTIONS in env).
        if self._node_version() == f"v{NODE_VERSION}":
            return
        arch = {"x86_64": "x64", "aarch64": "arm64"}.get(os.uname().machine)
        if arch is None:
            raise StepFailed(f"Chưa hỗ trợ Node.js trên kiến trúc {os.uname().machine}")
        say(f"Tải Node.js {NODE_VERSION}")
        url = f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-linux-{arch}.tar.xz"
        with tarfile.open(fileobj=io.BytesIO(download(url)), mode="r:xz") as tar:
            members = []
            for member in tar.getmembers():
                _, _, rest = member.name.partition("/")
                if rest.split("/", 1)[0] in ("bin", "lib", "include", "share"):
                    member.name = rest
                    members.append(member)
            tar.extractall("/usr/local", members=members, filter="data")

    def node_dependencies(self, app: Path) -> None:
        if (app / "package-lock.json").exists():
            say("Cài thư viện Node.js (npm ci)")
            try:
                run(["npm", "ci", "--loglevel=error"], what="npm ci", cwd=app, env=self.env)
            except StepFailed:  # a lock file out of step with package.json is common
                say("npm ci không được, thử npm install")
                run(
                    ["npm", "install", "--loglevel=error"],
                    what="Cài thư viện Node.js",
                    cwd=app,
                    env=self.env,
                )
        else:
            say("Cài thư viện Node.js (npm install)")
            run(
                ["npm", "install", "--loglevel=error"],
                what="Cài thư viện Node.js",
                cwd=app,
                env=self.env,
            )
        if self.plan.build:
            say("Build (npm run build)")
            run(["npm", "run", "build"], what="Build", cwd=app, env=self.env)

    def python_dependencies(self, app: Path) -> None:
        self.apt_install("python3-venv")
        if not (VENV_DIR / "bin" / "python").exists():
            run(["python3", "-m", "venv", str(VENV_DIR)], what="Tạo môi trường Python")
        pip = [str(VENV_DIR / "bin" / "pip"), "install", "--no-input"]
        if (app / "requirements.txt").exists():
            say("Cài thư viện Python (pip install -r requirements.txt)")
            run([*pip, "-r", "requirements.txt"], what="Cài thư viện Python", cwd=app, env=self.env)
        elif (app / "pyproject.toml").exists():
            say("Cài thư viện Python (pip install .)")
            run([*pip, "."], what="Cài thư viện Python", cwd=app, env=self.env)
        if self.plan.pip_extra:
            run([*pip, *self.plan.pip_extra], what="Cài máy chủ web Python", env=self.env)
        if self.plan.django:
            python = str(VENV_DIR / "bin" / "python")
            for task in (["migrate", "--noinput"], ["collectstatic", "--noinput"]):
                run(
                    [python, "manage.py", *task],
                    what=f"manage.py {task[0]}",
                    cwd=app,
                    env=self.env,
                    check=False,
                )

    # switching to the new version

    def switch(self) -> None:
        if OLD_DIR.exists():
            shutil.rmtree(OLD_DIR)
        if APP_DIR.exists():
            APP_DIR.rename(OLD_DIR)
        NEW_DIR.rename(APP_DIR)
        plan = self.plan
        say("Khởi động ứng dụng")
        if plan.stack in ("static", "node-static"):
            root = APP_DIR / plan.root if plan.stack == "static" else self._build_output()
            self._stop("minicloud-app", "apache2")
            self._nginx(nginx_static(root, plan.spa))
        elif plan.stack == "php":
            self._stop("minicloud-app", "nginx")
            Path("/etc/apache2/sites-available/000-default.conf").write_text(
                apache_site(APP_DIR / plan.root)
            )
            run(["a2enmod", "rewrite"], what="Bật mod_rewrite", check=False)
            run(["systemctl", "enable", "apache2"], what="Bật Apache", check=False)
            run(["systemctl", "restart", "apache2"], what="Khởi động Apache")
        else:
            self._stop("nginx", "apache2")
            START_SCRIPT.write_text(start_script(plan.start))
            START_SCRIPT.chmod(0o755)
            UNIT_FILE.write_text(UNIT)
            run(["systemctl", "daemon-reload"], what="Nạp lại systemd")
            run(["systemctl", "enable", "minicloud-app"], what="Bật dịch vụ ứng dụng", check=False)
            run(["systemctl", "restart", "minicloud-app"], what="Khởi động ứng dụng")

    def _build_output(self) -> Path:
        for folder in BUILD_OUTPUTS:
            if (APP_DIR / folder / "index.html").exists():
                return APP_DIR / folder
        raise StepFailed("Build xong nhưng không thấy thư mục dist/, build/ hay out/ có index.html")

    def _stop(self, *units: str) -> None:
        for unit in units:
            run(["systemctl", "disable", "--now", unit], what=f"Dừng {unit}", check=False)

    def _nginx(self, config: str) -> None:
        Path("/etc/nginx/sites-available/default").write_text(config)
        enabled = Path("/etc/nginx/sites-enabled/default")
        if not enabled.exists():
            enabled.symlink_to("/etc/nginx/sites-available/default")
        run(["nginx", "-t"], what="Kiểm tra cấu hình nginx")
        run(["systemctl", "enable", "nginx"], what="Bật nginx", check=False)
        run(["systemctl", "restart", "nginx"], what="Khởi động nginx")

    def wait_until_serving(self) -> int | None:
        """Wait for port 80; an app that ignores PORT gets nginx in front of it.

        Returns the port nginx forwards to, or None when the app itself serves port 80.
        """
        began = time.monotonic()
        deadline = began + SERVE_TIMEOUT
        proxied: int | None = None
        while time.monotonic() < deadline:
            if answers(80):
                if OLD_DIR.exists():
                    shutil.rmtree(OLD_DIR, ignore_errors=True)
                return proxied
            unit_stack = self.plan.stack in ("node", "python", "procfile")
            if unit_stack and proxied is None and time.monotonic() - began > 8:
                ports = listening_ports()
                found = sorted(p for p in FALLBACK_PORTS if p in ports)
                if found and 80 not in ports:
                    proxied = found[0]
                    say(f"Ứng dụng nghe ở cổng {proxied}, không phải 80: nginx sẽ chuyển tới đó")
                    self.apt_install("nginx")
                    self._nginx(nginx_proxy(proxied))
                    deadline = max(deadline, time.monotonic() + 30)
                    continue
            time.sleep(1)
        if self.plan.stack in ("node", "python", "procfile"):
            journal = run(
                ["journalctl", "-u", "minicloud-app", "-n", "40", "--no-pager", "-o", "cat"],
                what="Đọc log ứng dụng",
                check=False,
            )
            raise StepFailed(
                f"Ứng dụng không trả lời ở cổng 80 sau {SERVE_TIMEOUT} giây. Ứng dụng cần nghe "
                "ở cổng trong biến môi trường PORT (bằng 80).",
                _tail(journal),
            )
        raise StepFailed(f"Máy chủ web không trả lời ở cổng 80 sau {SERVE_TIMEOUT} giây")


def report(ok: bool, plan: Plan | None, message: str) -> None:
    payload = {
        "ok": ok,
        "stack": plan.stack if plan else None,
        "label": plan.label if plan else None,
        "message": message,
    }
    print(RESULT_MARKER + json.dumps(payload, ensure_ascii=False), flush=True)


def main(argv: list[str]) -> int:
    archive_path = Path(argv[1]) if len(argv) > 1 else Path("/root/minicloud-app.zip")
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text(
        f"minicloud deploy {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8"
    )
    plan: Plan | None = None
    try:
        archive = inspect_archive(archive_path.read_bytes())
        plan = detect(archive.names, archive.read_text)
        say(f"Nhận ra: {plan.label}")
        deployer = Deployer(plan)
        deployer.prepare()
        say("Giải nén mã nguồn")
        unpack(archive, NEW_DIR)
        archive.zip.close()
        archive_path.unlink(missing_ok=True)
        deployer.install(NEW_DIR)
        deployer.switch()
        proxied = deployer.wait_until_serving()
        note = f" (nginx chuyển tới cổng {proxied})" if proxied else ""
        say("Ứng dụng đã trả lời ở cổng 80")
        report(True, plan, f"Đang chạy{note}. Xem log đầy đủ trong máy: {LOG_FILE}")
        return 0
    except UploadError as exc:
        report(False, plan, str(exc))
    except StepFailed as exc:
        if exc.tail:
            print("    " + exc.tail.replace("\n", "\n    "), flush=True)
        report(False, plan, f"{exc.message}. Log đầy đủ trong máy: {LOG_FILE}")
    except Exception as exc:  # anything else still reaches the customer as a message
        report(False, plan, f"Lỗi không lường trước: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
