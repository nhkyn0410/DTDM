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
    # The CSP forbids inline scripts, so any here would silently break the page.
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in html
    assert " on" + "click=" not in html
