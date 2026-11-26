import re
from pathlib import Path

import minicloud

CONSOLE = Path(minicloud.__file__).parent / "console"


def test_the_root_opens_the_console(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/console/"


def test_the_console_is_served_with_a_strict_policy(client):
    page = client.get("/console/")
    assert page.status_code == 200
    assert "minicloud" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["cache-control"] == "no-cache"

    script = client.get("/console/app.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]


def test_api_responses_do_not_get_console_headers(client):
    assert "content-security-policy" not in client.get("/healthz").headers


def test_the_console_never_turns_data_into_markup():
    # Instance names and error messages come from users; the console must only use textContent.
    source = (CONSOLE / "app.js").read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in source
    assert "document.cookie" not in source  # the API key never sits in a cookie apps could read


def test_the_console_has_no_inline_script():
    # The CSP forbids inline scripts and styles, so any here would silently break the page.
    for page in CONSOLE.glob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert "<script>" not in html, page.name
        assert " on" + "click=" not in html, page.name
        assert " style=" not in html, page.name


def test_the_help_and_terms_pages_are_served(client):
    for page in ("huong-dan.html", "dieu-khoan.html"):
        response = client.get(f"/console/{page}")
        assert response.status_code == 200
        assert "content-security-policy" in response.headers
    index = client.get("/console/").text
    assert 'href="huong-dan.html"' in index and 'href="dieu-khoan.html"' in index


def test_the_sample_apps_linked_from_the_guide_are_served(client):
    guide = client.get("/console/huong-dan.html").text
    samples = re.findall(r'href="(samples/[\w-]+\.zip)"', guide)
    assert len(samples) == 6
    for sample in samples:
        response = client.get(f"/console/{sample}")
        assert response.status_code == 200, sample
        assert response.content.startswith(b"PK"), sample  # a zip
