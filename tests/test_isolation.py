"""Demo scene 3: tenant B must not see or touch tenant A, and A sees every attempt."""


def test_other_tenants_cannot_see_or_touch_resources(signup, run_worker):
    a = signup("a@example.com", "Nhom A")
    b = signup("b@example.com", "Nhom B")
    inst_id = a.create_instance().json()["instance"]["id"]
    run_worker()

    attempts = [
        b.get(f"/v1/instances/{inst_id}"),
        b.post(f"/v1/instances/{inst_id}/stop"),
        b.delete(f"/v1/instances/{inst_id}"),
        b.get(f"/v1/projects/{a.project_id}/instances"),
        b.post(f"/v1/projects/{a.project_id}/instances", json={"name": "trojan"}),
        b.get(f"/v1/orgs/{a.org_id}/billing"),
    ]
    # 404, not 403: B cannot even learn that these ids exist.
    assert [r.status_code for r in attempts] == [404] * 6

    assert a.get(f"/v1/instances/{inst_id}").json()["status"] == "running"
    audit = a.get(f"/v1/orgs/{a.org_id}/audit").json()
    denied = [entry for entry in audit if entry["outcome"] == "denied"]
    assert len(denied) == 6
    assert {entry["actor_id"] for entry in denied} == {b.user_id}


def test_unknown_ids_look_the_same_as_foreign_ones(signup):
    b = signup("b@example.com", "Nhom B")
    assert b.get("/v1/instances/ins_000000000000").status_code == 404


def test_only_owners_read_the_audit_log(signup):
    a = signup("a@example.com", "Nhom A")
    b = signup("b@example.com", "Nhom B")
    assert b.get(f"/v1/orgs/{a.org_id}/audit").status_code == 404
