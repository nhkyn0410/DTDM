"""Several named API keys per user, each one revocable (threat model #1)."""

from sqlalchemy import select

from minicloud.models import AuditLog
from minicloud.services.keys import MAX_ACTIVE_KEYS


def with_key(tenant, raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def test_the_signup_key_is_listed_and_marked_current(signup):
    tenant = signup()
    [key] = tenant.get("/v1/me/api-keys").json()
    assert key["name"] == "signup"
    assert key["current"] is True and key["revoked"] is False
    assert key["prefix"].startswith("mc_")
    assert "key_hash" not in key and "api_key" not in key  # never handed out again


def test_a_new_key_works_and_is_shown_only_once(signup, client):
    tenant = signup()
    created = tenant.post("/v1/me/api-keys", json={"name": "ci"})
    assert created.status_code == 201
    raw = created.json()["api_key"]
    assert created.json()["key"]["name"] == "ci"
    assert client.get("/v1/me", headers=with_key(tenant, raw)).status_code == 200
    listed = client.get("/v1/me/api-keys", headers=with_key(tenant, raw)).json()
    assert [(k["name"], k["current"]) for k in listed] == [("signup", False), ("ci", True)]
    assert all("api_key" not in k for k in listed)


def test_a_revoked_key_is_refused(signup, client):
    tenant = signup()
    created = tenant.post("/v1/me/api-keys", json={"name": "script"}).json()
    revoked = tenant.delete(f"/v1/me/api-keys/{created['key']['id']}")
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True
    assert client.get("/v1/me", headers=with_key(tenant, created["api_key"])).status_code == 401
    # Revoking twice is fine.
    assert tenant.delete(f"/v1/me/api-keys/{created['key']['id']}").status_code == 200


def test_the_last_active_key_cannot_be_revoked(signup):
    tenant = signup()
    [key] = tenant.get("/v1/me/api-keys").json()
    response = tenant.delete(f"/v1/me/api-keys/{key['id']}")
    assert response.status_code == 409
    assert tenant.get("/v1/me").status_code == 200  # still works


def test_someone_elses_key_looks_missing_and_stays_active(signup, client, session):
    an = signup("an@example.com", "Nhom An")
    binh = signup("binh@example.com", "Nhom Binh")
    created = binh.post("/v1/me/api-keys", json={"name": "spare"}).json()

    assert an.delete(f"/v1/me/api-keys/{created['key']['id']}").status_code == 404
    assert client.get("/v1/me", headers=with_key(binh, created["api_key"])).status_code == 200
    with session() as db:
        denied = db.scalars(
            select(AuditLog).where(
                AuditLog.action == "api_key.revoke", AuditLog.outcome == "denied"
            )
        ).all()
        assert [d.target_id for d in denied] == [created["key"]["id"]]


def test_active_keys_are_capped(signup):
    tenant = signup()
    for i in range(MAX_ACTIVE_KEYS - 1):  # the signup key is the first one
        assert tenant.post("/v1/me/api-keys", json={"name": f"k{i}"}).status_code == 201
    assert tenant.post("/v1/me/api-keys", json={"name": "one-too-many"}).status_code == 409


def test_creating_and_revoking_are_audited(signup, session):
    tenant = signup()
    created = tenant.post("/v1/me/api-keys", json={"name": "laptop"}).json()
    tenant.delete(f"/v1/me/api-keys/{created['key']['id']}")
    with session() as db:
        actions = [
            a.action
            for a in db.scalars(select(AuditLog).where(AuditLog.target_type == "api_key")).all()
        ]
    assert actions == ["api_key.create", "api_key.revoke"]
