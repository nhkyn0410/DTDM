"""The reconciler: the database follows what Incus really runs, without doing harm."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from minicloud import reconciler
from minicloud.drivers.base import InstanceRuntime
from minicloud.models import (
    Instance,
    InstanceStatus,
    Operation,
    OperationStatus,
    Organization,
    Project,
    User,
    utcnow,
)
from minicloud.services.metering import run_metering_tick


@pytest.fixture
def reconcile(app):
    def _reconcile(now=None) -> reconciler.ReconcileReport:
        with app.state.session_factory() as db:
            return reconciler.reconcile(db, app.state.driver, app.state.settings, now=now)

    return _reconcile


def incus_key(session, inst_id: str) -> tuple[str, str]:
    with session() as db:
        inst = db.get(Instance, inst_id)
        return db.get(Project, inst.project_id).incus_project, inst.incus_name


def set_in_incus(driver, key, **changes) -> None:
    driver.instances[key] = replace(driver.instances[key], **changes)


def status(tenant, inst_id: str) -> str:
    return tenant.get(f"/v1/instances/{inst_id}").json()["status"]


def credits(tenant) -> float:
    return tenant.get(f"/v1/orgs/{tenant.org_id}/billing").json()["credits"]


def running_instance(tenant, run_worker, name: str = "web", now=None) -> str:
    inst_id = tenant.create_instance(name).json()["instance"]["id"]
    run_worker(now=now)
    return inst_id


def interrupt(session, inst_id: str, status: InstanceStatus) -> None:
    """Make the last operation look as if the control plane died while it ran."""
    with session() as db:
        op = db.scalars(
            select(Operation)
            .where(Operation.instance_id == inst_id)
            .order_by(Operation.created_at.desc())
        ).first()
        op.status = OperationStatus.running
        op.finished_at = None
        inst = db.get(Instance, inst_id)
        inst.status = status
        if status == InstanceStatus.provisioning:  # what a create saves only when it succeeds
            inst.metered_until = inst.node = inst.ip = None
        db.commit()
        assert reconciler.recover_interrupted_operations(db) == 1


def test_a_stop_behind_our_back_is_seen_and_billed_right(
    signup, run_worker, session, driver, reconcile
):
    tenant = signup()
    t0 = utcnow()
    inst_id = running_instance(tenant, run_worker, now=t0)
    set_in_incus(driver, incus_key(session, inst_id), status="Stopped")  # `incus stop` on a laptop

    report = reconcile(now=t0 + timedelta(hours=1))
    assert status(tenant, inst_id) == "stopped"
    [change] = report.changes
    assert (change.field, change.before, change.after) == ("status", "running", "stopped")
    assert change.reason == "Incus reports Stopped"

    with session() as db:  # from now on only the disk is billed: 1 credit an hour
        run_metering_tick(db, t0 + timedelta(hours=2))
    assert credits(tenant) == pytest.approx(100 - 20 - 1, abs=0.05)
    actions = [e["action"] for e in tenant.get(f"/v1/orgs/{tenant.org_id}/audit").json()]
    assert "instance.reconcile" in actions  # the customer can see why it changed


def test_a_start_behind_our_back_is_seen(signup, run_worker, session, driver, reconcile):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    tenant.post(f"/v1/instances/{inst_id}/stop")
    run_worker()
    set_in_incus(driver, incus_key(session, inst_id), status="Running")

    reconcile()
    assert status(tenant, inst_id) == "running"


def test_a_vanished_instance_becomes_an_error_and_stops_costing(
    signup, run_worker, session, driver, reconcile
):
    tenant = signup()
    t0 = utcnow()
    inst_id = running_instance(tenant, run_worker, now=t0)
    del driver.instances[incus_key(session, inst_id)]

    [change] = reconcile(now=t0 + timedelta(hours=1)).changes
    assert change.after == "error" and change.reason == "not found in Incus"
    after_reconcile = credits(tenant)
    with session() as db:
        run_metering_tick(db, t0 + timedelta(hours=5))
    assert credits(tenant) == after_reconcile
    assert reconcile().changes == []  # said once, not every minute


def test_instances_with_an_operation_in_flight_are_left_alone(
    signup, run_worker, session, driver, reconcile
):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    tenant.post(f"/v1/instances/{inst_id}/stop")  # queued, the worker has not run yet
    set_in_incus(driver, incus_key(session, inst_id), status="Stopped")

    report = reconcile()
    assert report.checked == 0 and report.changes == []
    assert status(tenant, inst_id) == "stopping"


def test_nothing_changes_when_incus_does_not_answer(signup, run_worker, session, driver, reconcile):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    driver.instances.clear()
    driver.fail_next = "cannot reach Incus"

    report = reconcile()
    assert report.skipped == "cannot reach Incus"
    assert status(tenant, inst_id) == "running"


def test_many_instances_vanishing_at_once_are_held_back(
    signup, run_worker, session, driver, reconcile
):
    tenant = signup()
    ids = [running_instance(tenant, run_worker, f"web-{i}") for i in range(3)]
    driver.instances.clear()  # more likely the wrong Incus server than three deletions

    report = reconcile()
    assert len(report.held_back) == 3 and report.changes == []
    assert {status(tenant, i) for i in ids} == {"running"}


def test_an_interrupted_create_is_finished_from_what_incus_shows(
    signup, run_worker, session, reconcile
):
    tenant = signup()
    body = tenant.create_instance("web").json()
    inst_id = body["instance"]["id"]
    run_worker()  # Incus created it...
    interrupt(session, inst_id, InstanceStatus.provisioning)  # ...but the result was never saved

    op = tenant.get(f"/v1/operations/{body['operation']['id']}").json()
    assert op["status"] == "failed" and "interrupted" in op["error"]
    changes = reconcile().changes
    assert [(c.field, c.reason) for c in changes] == [
        ("node", "placed"),  # without a node it gets no route and the scheduler misses it
        ("status", "Incus reports Running"),
    ]
    inst = tenant.get(f"/v1/instances/{inst_id}").json()
    assert inst["status"] == "running" and inst["node"] and inst["ip"]
    with session() as db:
        assert db.get(Instance, inst_id).metered_until is not None  # billing starts now


def test_an_interrupted_create_that_never_reached_incus_becomes_an_error(
    signup, run_worker, session, driver, reconcile
):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    interrupt(session, inst_id, InstanceStatus.provisioning)
    del driver.instances[incus_key(session, inst_id)]

    reconcile()
    assert status(tenant, inst_id) == "error"


def test_an_interrupted_delete_is_carried_out_again(signup, run_worker, session, driver, reconcile):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    tenant.delete(f"/v1/instances/{inst_id}")
    interrupt(session, inst_id, InstanceStatus.deleting)

    [change] = reconcile().changes
    assert change.reason == "delete was interrupted: retrying"
    run_worker()
    assert status(tenant, inst_id) == "deleted"
    assert driver.instances == {}


def test_a_delete_that_finished_in_incus_is_recorded(
    signup, run_worker, session, driver, reconcile
):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    tenant.delete(f"/v1/instances/{inst_id}")
    interrupt(session, inst_id, InstanceStatus.deleting)
    del driver.instances[incus_key(session, inst_id)]

    reconcile()
    assert status(tenant, inst_id) == "deleted"


def test_a_suspended_organization_does_not_keep_running(signup, run_worker, session, reconcile):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    with session() as db:  # suspended, but the stop never happened (say it was interrupted)
        db.get(Organization, tenant.org_id).suspended = True
        db.commit()

    [change] = reconcile().changes
    assert (change.after, change.reason) == ("stopping", "organization is suspended")
    run_worker()
    assert status(tenant, inst_id) == "stopped"


def test_a_move_in_incus_updates_the_node(signup, run_worker, session, driver, reconcile):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    key = incus_key(session, inst_id)
    old_node = driver.instances[key].node
    set_in_incus(driver, key, node="laptop-3", ip="10.10.9.9")  # e.g. `incus cluster evacuate`

    [change] = reconcile().changes
    assert (change.field, change.before, change.after) == ("node", old_node, "laptop-3")
    body = tenant.get(f"/v1/instances/{inst_id}").json()
    assert body["node"] == "laptop-3" and body["ip"] == "10.10.9.9"


def test_a_new_address_alone_is_picked_up_quietly(signup, run_worker, session, driver, reconcile):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    set_in_incus(driver, incus_key(session, inst_id), ip="10.10.7.7")

    assert reconcile().changes == []
    assert tenant.get(f"/v1/instances/{inst_id}").json()["ip"] == "10.10.7.7"


def test_states_still_settling_are_left_alone(signup, run_worker, session, driver, reconcile):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    set_in_incus(driver, incus_key(session, inst_id), status="Frozen")
    assert reconcile().changes == []
    assert status(tenant, inst_id) == "running"


def test_a_row_changed_while_incus_was_read_waits_for_the_next_pass(
    signup, run_worker, session, driver, reconcile, monkeypatch
):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    set_in_incus(driver, incus_key(session, inst_id), status="Stopped")
    read_incus = driver.list_instances

    def slow_answer():
        answer = read_incus()
        with session() as db:  # meanwhile the worker saves a newer state of this instance
            db.get(Instance, inst_id).updated_at = utcnow() + timedelta(seconds=1)
            db.commit()
        return answer

    monkeypatch.setattr(driver, "list_instances", slow_answer)
    assert reconcile().changes == []
    assert status(tenant, inst_id) == "running"


def test_unknown_instances_in_tenant_projects_are_reported_not_touched(
    signup, run_worker, driver, reconcile
):
    tenant = signup()
    running_instance(tenant, run_worker)
    driver.instances[("mc-ghost", "ghost-1")] = InstanceRuntime("ghost-1", "laptop-1", "Running")
    driver.instances[("default", "ops-box")] = InstanceRuntime("ops-box", "laptop-1", "Running")

    report = reconcile()
    assert report.orphans == ["mc-ghost/ghost-1"]  # the default project is the operators'
    assert ("mc-ghost", "ghost-1") in driver.instances


def test_admins_can_run_a_pass_on_demand(signup, run_worker, session, driver):
    tenant = signup()
    inst_id = running_instance(tenant, run_worker)
    set_in_incus(driver, incus_key(session, inst_id), status="Stopped")
    assert tenant.post("/v1/admin/reconcile").status_code == 403

    with session() as db:
        db.scalar(select(User).where(User.id == tenant.user_id)).is_platform_admin = True
        db.commit()
    body = tenant.post("/v1/admin/reconcile").json()
    assert body["checked"] == 1 and body["skipped"] is None
    assert [(c["before"], c["after"]) for c in body["changes"]] == [("running", "stopped")]
    assert status(tenant, inst_id) == "stopped"
