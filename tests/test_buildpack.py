import io
import zipfile
from pathlib import Path

import pytest

from minicloud import buildpack

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "apps"


def make_zip(files: dict[str, str | bytes], prefix: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(prefix + name)
            info.filename = prefix + name  # keep backslashes: ZipInfo turns os.sep into "/"
            archive.writestr(info, content)
    return buffer.getvalue()


def plan_of(files: dict[str, str | bytes], prefix: str = "") -> buildpack.Plan:
    archive = buildpack.inspect_archive(make_zip(files, prefix))
    return buildpack.detect(archive.names, archive.read_text)


@pytest.mark.parametrize(
    ("example", "stack"),
    [
        ("static-site", "static"),
        ("node-express", "node"),
        ("vite-site", "node-static"),
        ("python-flask", "python"),
        ("python-fastapi", "python"),
        ("php-site", "php"),
    ],
)
def test_every_example_app_is_recognised(example, stack):
    folder = EXAMPLES / example
    files = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*")}
    archive = buildpack.inspect_archive(make_zip({k: v for k, v in files.items() if v}))
    assert buildpack.detect(archive.names, archive.read_text).stack == stack


def test_the_example_servers_read_the_port_they_are_given():
    express = plan_of({"package.json": (EXAMPLES / "node-express" / "package.json").read_text()})
    assert (express.start, express.label) == ("npm start", "Node.js (Express, npm start)")
    flask = buildpack.inspect_archive(
        make_zip({p.name: p.read_bytes() for p in (EXAMPLES / "python-flask").iterdir()})
    )
    plan = buildpack.detect(flask.names, flask.read_text)
    assert plan.start == "gunicorn app:app --bind 0.0.0.0:$PORT"


def test_a_github_download_folder_is_unwrapped():
    plan = plan_of({"index.html": "<h1>hi</h1>", "css/site.css": ""}, prefix="my-site-main/")
    assert (plan.stack, plan.root) == ("static", "")


def test_windows_paths_and_junk_are_cleaned_up():
    archive = buildpack.inspect_archive(
        make_zip(
            {
                "site\\index.html": "<h1>hi</h1>",
                "__MACOSX/site/._index.html": "",
                "site/.DS_Store": "",
                "site/node_modules/left-pad/index.js": "",
            }
        )
    )
    assert archive.names == ["index.html"]


def test_a_procfile_wins():
    plan = plan_of(
        {
            "Procfile": "# comment\nweb: python serve.py --port $PORT\n",
            "requirements.txt": "flask\n",
            "app.py": "app = Flask(__name__)\n",
        }
    )
    assert (plan.stack, plan.start, plan.python) == (
        "procfile",
        "python serve.py --port $PORT",
        True,
    )


def test_flask_and_fastapi_apps_are_found_by_their_variable():
    flask = plan_of(
        {
            "requirements.txt": "Flask==3.0.3\n",
            "web/server.py": "from flask import Flask\napplication = Flask(__name__)\n",
        }
    )
    assert flask.start == "gunicorn web.server:application --bind 0.0.0.0:$PORT"
    fastapi = plan_of(
        {
            "requirements.txt": "fastapi\n",
            "main.py": "from fastapi import FastAPI\napi: FastAPI = FastAPI()\n",
        }
    )
    assert fastapi.start == "uvicorn main:api --host 0.0.0.0 --port $PORT"
    assert fastapi.pip_extra == ("uvicorn",)


def test_django_runs_its_wsgi_module():
    plan = plan_of({"manage.py": "", "requirements.txt": "Django>=5\n", "mysite/wsgi.py": "x = 1"})
    assert plan.start == "gunicorn mysite.wsgi:application --bind 0.0.0.0:$PORT"
    assert plan.django


def test_a_node_project_that_only_builds_is_served_as_files():
    plan = plan_of(
        {
            "package.json": '{"scripts": {"build": "vite build"}, "devDependencies": {"vite": ""}}',
            "index.html": "<div id=app></div>",
        }
    )
    assert (plan.stack, plan.build, plan.spa) == ("node-static", True, True)
    assert "Vite" in plan.label


def test_a_node_server_without_scripts_runs_its_entry_file():
    plan = plan_of({"package.json": '{"main": "src/server.js"}', "src/server.js": "x"})
    assert plan.start == "node src/server.js"


def test_python_wins_over_a_package_json_kept_for_assets():
    plan = plan_of(
        {
            "package.json": '{"scripts": {"build": "tailwindcss -o static/site.css"}}',
            "requirements.txt": "flask\n",
            "app.py": "app = Flask(__name__)\n",
        }
    )
    assert plan.stack == "python"


def test_static_files_in_a_public_folder_and_php():
    assert plan_of({"public/index.html": "x", "README.md": "x"}).root == "public"
    assert plan_of({"index.php": "<?php echo 1;", "inc/db.php": "x"}).stack == "php"


def test_unknown_projects_are_refused_with_what_is_supported():
    with pytest.raises(buildpack.UploadError, match="Procfile"):
        plan_of({"README.md": "hello"})
    with pytest.raises(buildpack.UploadError, match="JSON"):
        plan_of({"package.json": "{not json"})


@pytest.mark.parametrize("name", ["../evil.sh", "/etc/passwd", "C:/Windows/x.txt", "a/../../b"])
def test_paths_that_leave_the_app_folder_are_refused(name):
    with pytest.raises(buildpack.UploadError, match="đường dẫn"):
        buildpack.inspect_archive(make_zip({name: "x"}))


def test_symlinks_and_non_zips_are_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        link = zipfile.ZipInfo("link")
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "/etc/passwd")
    with pytest.raises(buildpack.UploadError, match="symlink"):
        buildpack.inspect_archive(buffer.getvalue())
    with pytest.raises(buildpack.UploadError, match="zip"):
        buildpack.inspect_archive(b"PK but not really")


def test_too_many_files_are_refused(monkeypatch):
    monkeypatch.setattr(buildpack, "MAX_FILES", 3)
    with pytest.raises(buildpack.UploadError, match="node_modules"):
        buildpack.inspect_archive(make_zip({f"f{i}.txt": "x" for i in range(4)}))


def test_unpacking_writes_the_app_files(tmp_path):
    archive = buildpack.inspect_archive(make_zip({"index.html": "hi", "css/a.css": "b"}, "x/"))
    buildpack.unpack(archive, tmp_path / "app")
    assert (tmp_path / "app" / "index.html").read_text() == "hi"
    assert (tmp_path / "app" / "css" / "a.css").read_text() == "b"


def test_the_written_configuration():
    assert "try_files $uri $uri/ /index.html;" in buildpack.nginx_static(
        Path("/srv/app/dist"), True
    )
    assert "try_files $uri $uri/ =404;" in buildpack.nginx_static(Path("/srv/app"), False)
    assert "proxy_pass http://127.0.0.1:3000;" in buildpack.nginx_proxy(3000)
    script = buildpack.start_script("npm start")
    assert "export PORT=80" in script and script.rstrip().endswith("exec npm start")
