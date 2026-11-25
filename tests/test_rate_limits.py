"""Rate limits on sign-up and on wrong API keys, per client address (threat model #2)."""

from fastapi.testclient import TestClient

from minicloud.config import Settings
from minicloud.drivers.fake import FakeDriver
from minicloud.main import create_app
from minicloud.ratelimit import RateLimiter

HOME = "203.0.113.7"  # what Cloudflare reports in CF-Connecting-IP
OTHER = "198.51.100.9"


def make_client(**overrides) -> TestClient:
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        driver="fake",
        run_worker=False,
        trial_credits=100,
        **overrides,
    )
    return TestClient(create_app(settings, FakeDriver()))


def signup(client, n: int, ip: str = HOME, code: str | None = None):
    body = {"email": f"u{n}@example.com", "name": "U", "org_name": f"Org {n}"}
    if code is not None:
        body["invite_code"] = code
    return client.post("/v1/signup", json=body, headers={"CF-Connecting-IP": ip})


def test_sign_ups_from_one_address_are_limited():
    with make_client(rate_limit_signups_per_hour=3) as client:
        assert [signup(client, i).status_code for i in range(3)] == [201, 201, 201]
        blocked = signup(client, 3)
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "rate_limited"
        assert int(blocked.headers["retry-after"]) > 0
        assert signup(client, 4, ip=OTHER).status_code == 201  # another address is unaffected


def test_wrong_invite_codes_count_toward_the_limit():
    with make_client(rate_limit_signups_per_hour=3, signup_invite_code="right") as client:
        assert [signup(client, i, code="guess").status_code for i in range(3)] == [403] * 3
        assert signup(client, 3, code="right").status_code == 429  # guessing is slowed down


def test_many_wrong_keys_make_the_address_wait():
    with make_client(rate_limit_auth_failures=3) as client:
        good = {"Authorization": f"Bearer {signup(client, 0).json()['api_key']}"}
        bad = {"Authorization": "Bearer mc_not-a-real-key", "CF-Connecting-IP": HOME}
        assert [client.get("/v1/me", headers=bad).status_code for _ in range(3)] == [401] * 3
        assert client.get("/v1/me", headers=bad).status_code == 429
        assert client.get("/v1/me", headers={**good, "CF-Connecting-IP": HOME}).status_code == 429
        assert client.get("/v1/me", headers={**good, "CF-Connecting-IP": OTHER}).status_code == 200


def test_a_limit_expires_after_its_window():
    now = [0.0]
    limiter = RateLimiter(2, 60, clock=lambda: now[0])
    limiter.hit("a")
    limiter.hit("a")
    assert limiter.exceeded("a")
    assert 60 <= limiter.retry_after("a") <= 61
    assert not limiter.exceeded("b")
    now[0] = 60.5
    assert not limiter.exceeded("a")


def test_zero_turns_a_limit_off():
    limiter = RateLimiter(0, 60)
    for _ in range(100):
        limiter.hit("a")
    assert not limiter.exceeded("a")
