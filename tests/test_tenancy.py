def test_signup_gives_trial_credits_and_a_default_project(signup):
    tenant = signup()
    me = tenant.get("/v1/me")
    assert me.status_code == 200
    assert me.json()["email"] == "an@example.com"

    [org] = tenant.get("/v1/orgs").json()
    assert org["credits"] == 100
    assert org["role"] == "owner"

    projects = tenant.get(f"/v1/orgs/{tenant.org_id}/projects").json()
    assert [p["name"] for p in projects] == ["default"]
    assert projects[0]["quota"]["max_instances"] == 5


def test_an_email_registers_only_once(client, signup):
    signup("an@example.com")
    again = client.post(
        "/v1/signup", json={"email": "AN@example.com", "name": "X", "org_name": "Nhom khac"}
    )
    assert again.status_code == 409


def test_requests_without_a_valid_key_are_rejected(client):
    assert client.get("/v1/me").status_code == 401
    bad = client.get("/v1/me", headers={"Authorization": "Bearer mc_khong_hop_le"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "unauthorized"


def test_owner_creates_projects_with_unique_names(signup):
    tenant = signup()
    url = f"/v1/orgs/{tenant.org_id}/projects"
    assert tenant.post(url, json={"name": "web"}).status_code == 201
    assert tenant.post(url, json={"name": "web"}).status_code == 409
    assert tenant.post(url, json={"name": "Ten Sai"}).status_code == 422


def test_price_list_is_public(client):
    flavors = client.get("/v1/catalog/flavors").json()
    small = next(f for f in flavors if f["name"] == "s1.small")
    assert small["credits_per_hour_container"] == 20
    assert small["credits_per_hour_vm"] == 30
    assert small["credits_per_hour_when_stopped"] == 1
    assert client.get("/v1/catalog/images").status_code == 200
