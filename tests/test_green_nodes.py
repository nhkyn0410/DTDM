"""Demo scene 7: empty a laptop before it sleeps, wake it, take it back, without losing quorum."""

from sqlalchemy import select

from minicloud.green import wol
from minicloud.models import AuditLog, User


def make_admin(session, email: str = "an@example.com") -> None:
    with session() as db:
        db.scalar(select(User).where(User.email == email)).is_platform_admin = True
        db.commit()


def test_evacuating_a_laptop_moves_its_instances_and_their_routes(signup, run_worker, session):
    tenant = signup()
    make_admin(session)
    ids = [tenant.create_instance(f"web-{i}").json()["instance"]["id"] for i in range(3)]
    run_worker()  # spread: one per laptop
    on_three = [i for i in ids if tenant.get(f"/v1/instances/{i}").json()["node"] == "laptop-3"]
    assert len(on_three) == 1

    response = tenant.post("/v1/admin/nodes/laptop-3/evacuate")
    assert response.status_code == 200, response.text
    moved = tenant.get(f"/v1/instances/{on_three[0]}").json()
    assert moved["node"] != "laptop-3" and moved["status"] == "running"
    green = tenant.get("/v1/green").json()
    assert {n["name"]: n["awake"] for n in green["nodes"]}["laptop-3"] is False

    assert tenant.post("/v1/admin/nodes/laptop-3/restore").status_code == 200
    green = tenant.get("/v1/green").json()
    assert all(n["awake"] for n in green["nodes"])


def test_the_pinned_laptop_and_quorum_are_protected(signup, session):
    tenant = signup()
    make_admin(session)
    assert tenant.post("/v1/admin/nodes/laptop-1/evacuate").status_code == 409  # pinned
    assert tenant.post("/v1/admin/nodes/laptop-3/evacuate").status_code == 200
    # A second sleeper would leave 1 of 3 awake: Incus would stop answering.
    assert tenant.post("/v1/admin/nodes/laptop-2/evacuate").status_code == 409
    assert tenant.post("/v1/admin/nodes/laptop-9/evacuate").status_code == 404


def test_waking_sends_a_magic_packet_to_the_configured_mac(app, signup, session, monkeypatch):
    tenant = signup()
    make_admin(session)
    sent = []
    monkeypatch.setattr(wol, "wake", lambda mac, broadcast: sent.append((mac, broadcast)))
    assert tenant.post("/v1/admin/nodes/laptop-3/wake").status_code == 404  # no MAC yet

    app.state.settings.green_node_macs = "laptop-3=E4:A8:DF:D4:1E:64"
    assert tenant.post("/v1/admin/nodes/laptop-3/wake").status_code == 200
    assert sent == [("E4:A8:DF:D4:1E:64", "255.255.255.255")]
    with session() as db:
        actions = db.scalars(select(AuditLog.action).where(AuditLog.target_type == "node")).all()
    assert actions == ["node.wake"]


def test_only_platform_admins_move_laptops(signup):
    tenant = signup()
    for action in ("evacuate", "restore", "wake"):
        assert tenant.post(f"/v1/admin/nodes/laptop-3/{action}").status_code == 403
