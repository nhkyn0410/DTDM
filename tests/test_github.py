import io
import zipfile

import pytest

from minicloud.errors import BadGateway, InvalidUpload, PayloadTooLarge
from minicloud.ratelimit import RateLimiter
from minicloud.services import deploys, github


def zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


# -- reading links -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("link", "owner", "repo", "ref", "subdir"),
    [
        ("https://github.com/octocat/Hello-World", "octocat", "Hello-World", "", ""),
        ("github.com/octocat/Hello-World.git", "octocat", "Hello-World", "", ""),
        ("https://www.github.com/an/shop/", "an", "shop", "", ""),
        ("https://github.com/an/shop/tree/dev", "an", "shop", "dev", ""),
        ("https://github.com/an/shop/tree/main/web/frontend", "an", "shop", "main", "web/frontend"),
    ],
)
def test_github_links_are_read(link, owner, repo, ref, subdir):
    source = github.parse_url(link)
    assert (source.owner, source.repo, source.ref, source.subdir) == (owner, repo, ref, subdir)


def test_the_stored_link_and_label_are_canonical():
    source = github.parse_url("github.com/an/shop/tree/main/web")
    assert source.url == "https://github.com/an/shop/tree/main/web"
    assert source.label == "github.com/an/shop@main/web"
    assert github.parse_url(source.url) == source


@pytest.mark.parametrize(
    "link",
    [
        "",
        "https://gitlab.com/an/shop",
        "http://169.254.169.254/latest/meta-data",
        "https://github.com.evil.example/an/shop",
        "https://github.com:8443/an/shop",
        "https://user:pass@github.com/an/shop",
        "https://github.com/an",
        "https://github.com/an/shop/blob/main/app.py",
        "https://github.com/an/shop/tree/../etc",
        "https://github.com/-an/shop",
        "ftp://github.com/an/shop",
    ],
)
def test_anything_but_a_public_github_link_is_refused(link):
    with pytest.raises(InvalidUpload):
        github.parse_url(link)


# -- downloading -----------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
        self.status = status
        self._body = io.BytesIO(body)
        self._headers = headers or {}

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._headers.get(name, default)

    def read(self, amount: int = -1) -> bytes:
        return self._body.read(amount)


def fake_github(monkeypatch, response: FakeResponse | None = None, error: Exception | None = None):
    seen: dict = {}

    class Connection:
        def __init__(self, host: str, timeout: float) -> None:
            seen["host"] = host

        def request(self, method: str, path: str, headers: dict | None = None) -> None:
            if error is not None:
                raise error
            seen["path"] = path

        def getresponse(self) -> FakeResponse:
            return response

        def close(self) -> None:
            seen["closed"] = True

    monkeypatch.setattr(github, "_IPv4HTTPSConnection", Connection)
    return seen


def test_the_download_address_is_built_by_minicloud(monkeypatch):
    seen = fake_github(monkeypatch, FakeResponse(200, b"PK zip"))
    data = github.download(github.parse_url("github.com/an/shop/tree/dev/web"), limit=1024)
    assert data == b"PK zip"
    assert (seen["host"], seen["path"], seen["closed"]) == (
        "codeload.github.com",
        "/an/shop/zip/dev",
        True,
    )
    fake_github(monkeypatch, FakeResponse(200, b"PK"))
    github.download(github.parse_url("github.com/an/shop"), limit=1024)  # the default branch


def test_download_failures_become_clear_errors(monkeypatch):
    source = github.parse_url("github.com/an/shop")
    fake_github(monkeypatch, FakeResponse(404))
    with pytest.raises(InvalidUpload, match="không công khai"):
        github.download(source, limit=1024)
    fake_github(monkeypatch, FakeResponse(503))
    with pytest.raises(BadGateway):
        github.download(source, limit=1024)
    fake_github(monkeypatch, error=OSError("network unreachable"))
    with pytest.raises(BadGateway):
        github.download(source, limit=1024)


def test_a_repository_over_the_limit_is_cut_off(monkeypatch):
    source = github.parse_url("github.com/an/shop")
    fake_github(monkeypatch, FakeResponse(200, b"x" * 10, {"Content-Length": "4096"}))
    with pytest.raises(PayloadTooLarge):
        github.download(source, limit=1024)
    fake_github(monkeypatch, FakeResponse(200, b"x" * 2048))  # no Content-Length: counted
    with pytest.raises(PayloadTooLarge):
        github.download(source, limit=1024)


# -- the API ---------------------------------------------------------------------------------

# What codeload.github.com sends: everything inside "<repo>-<ref>/".
MONOREPO = zip_of(
    {
        "shop-main/README.md": "# shop",
        "shop-main/web/index.html": "<h1>Xin chào</h1>",
        "shop-main/api/requirements.txt": "flask\n",
        "shop-main/api/app.py": "from flask import Flask\napp = Flask(__name__)\n",
    }
)


def from_github(tenant, url: str, name: str = "shop"):
    return tenant.post(
        f"/v1/projects/{tenant.project_id}/apps/github", json={"name": name, "url": url}
    )


def test_a_folder_of_a_github_repository_becomes_an_app(
    signup, run_worker, session, settings, driver, monkeypatch
):
    asked = []
    monkeypatch.setattr(github, "download", lambda source, limit: asked.append(source) or MONOREPO)
    tenant = signup()
    created = from_github(tenant, "https://github.com/an/shop/tree/main/web")
    assert created.status_code == 202, created.text
    dep = created.json()["deployment"]
    assert (dep["stack"], dep["source_url"]) == (
        "static",
        "https://github.com/an/shop/tree/main/web",
    )
    assert dep["source"].startswith("github.com/an/shop@main/web, ")
    inst_id = created.json()["instance"]["id"]

    run_worker()
    for _ in range(2):
        with session() as db:
            deploys.advance(db, driver, settings)
    [done] = tenant.get(f"/v1/instances/{inst_id}/deployments").json()
    assert (done["status"], done["stack"]) == ("succeeded", "static")

    again = tenant.post(f"/v1/instances/{inst_id}/deployments/github", json={})
    assert again.status_code == 202, again.text
    assert again.json()["source_url"] == "https://github.com/an/shop/tree/main/web"
    other = tenant.post(
        f"/v1/instances/{inst_id}/deployments/github",
        json={"url": "https://github.com/an/shop/tree/main/api"},
    )
    assert other.status_code == 409  # the first new version is still pending
    assert [s.subdir for s in asked] == ["web", "web"]


def test_a_repository_that_cannot_work_is_refused_before_anything_is_made(signup, monkeypatch):
    monkeypatch.setattr(github, "download", lambda source, limit: MONOREPO)
    tenant = signup()
    refused = from_github(tenant, "https://github.com/an/shop")  # README.md and two folders
    assert refused.status_code == 422
    assert from_github(tenant, "https://github.com/an/shop/tree/main/docs").status_code == 422
    assert from_github(tenant, "https://gitlab.com/an/shop").status_code == 422
    assert tenant.get(f"/v1/projects/{tenant.project_id}/instances").json() == []


def test_github_fetches_are_limited_per_user(signup, app, monkeypatch):
    monkeypatch.setattr(github, "download", lambda source, limit: MONOREPO)
    app.state.git_limiter = RateLimiter(1, 600)
    tenant = signup()
    assert from_github(tenant, "https://github.com/an/shop/tree/main/web").status_code == 202
    assert (
        from_github(tenant, "https://github.com/an/shop/tree/main/web", "shop-2").status_code == 429
    )


def test_an_uploaded_app_has_no_link_to_fetch_again(signup, run_worker, monkeypatch):
    tenant = signup()
    created = tenant.client.post(
        f"/v1/projects/{tenant.project_id}/apps",
        params={"name": "web"},
        content=zip_of({"index.html": "x"}),
        headers={**tenant.headers, "Content-Type": "application/zip"},
    )
    inst_id = created.json()["instance"]["id"]
    run_worker()
    resp = tenant.post(f"/v1/instances/{inst_id}/deployments/github", json={})
    assert resp.status_code == 409
