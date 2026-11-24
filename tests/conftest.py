import pytest
from fastapi.testclient import TestClient

from minicloud.config import Settings
from minicloud.drivers.fake import FakeDriver
from minicloud.main import create_app
from minicloud.worker import process_pending_operations


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        driver="fake",
        run_worker=False,  # tests drive the worker by hand, with a controlled clock
        trial_credits=100,
    )


@pytest.fixture
def driver() -> FakeDriver:
    return FakeDriver()


@pytest.fixture
def app(settings, driver):
    return create_app(settings, driver)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session(app):
    return app.state.session_factory


class Tenant:
    """A signed-up customer; every request carries its API key."""

    def __init__(self, client: TestClient, data: dict) -> None:
        self.client = client
        self.user_id = data["user_id"]
        self.org_id = data["org_id"]
        self.project_id = data["project_id"]
        self.headers = {"Authorization": f"Bearer {data['api_key']}"}

    def get(self, url: str, **kwargs):
        return self.client.get(url, headers=self.headers, **kwargs)

    def post(self, url: str, **kwargs):
        return self.client.post(url, headers=self.headers, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.client.delete(url, headers=self.headers, **kwargs)

    def create_instance(self, name: str = "web-1", **extra):
        return self.post(f"/v1/projects/{self.project_id}/instances", json={"name": name, **extra})


@pytest.fixture
def signup(client):
    def _signup(email: str = "an@example.com", org: str = "Nhom An") -> Tenant:
        response = client.post(
            "/v1/signup", json={"email": email, "name": "Sinh vien", "org_name": org}
        )
        assert response.status_code == 201, response.text
        return Tenant(client, response.json())

    return _signup


@pytest.fixture
def run_worker(app):
    def _run(now=None) -> int:
        with app.state.session_factory() as db:
            return process_pending_operations(db, app.state.driver, app.state.settings, now=now)

    return _run
