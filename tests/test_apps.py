import io
import zipfile

from sqlalchemy import select

from minicloud.models import Deployment
from minicloud.services import deploys


def zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


STATIC = zip_of({"index.html": "<h1>Xin chào</h1>"})
FLASK = zip_of(
    {"requirements.txt": "flask\n", "app.py": "from flask import Flask\napp = Flask(__name__)\n"}
)


def upload(tenant, data: bytes, name: str = "web", **params):
    return tenant.client.post(
        f"/v1/projects/{tenant.project_id}/apps",
        params={"name": name, **params},
        content=data,
        headers={**tenant.headers, "Content-Type": "application/zip"},
    )


def send_version(tenant, instance_id: str, data: bytes):
    return tenant.client.post(
        f"/v1/instances/{instance_id}/deployments",
        content=data,
        headers={**tenant.headers, "Content-Type": "application/zip"},
    )


def advance(session, driver, settings, times: int = 2) -> None:
    for _ in range(times):  # first the push and the start, then the result
        with session() as db:
            deploys.advance(db, driver, settings)


def test_an_uploaded_zip_becomes_a_running_app(signup, run_worker, session, settings, driver):
    tenant = signup()
    created = upload(tenant, FLASK, filename="C:\\Users\\an\\shop.zip")
    assert created.status_code == 202, created.text
    body = created.json()
    assert (body["deployment"]["stack"], body["deployment"]["status"]) == ("python", "pending")
    assert body["deployment"]["source"].startswith("shop.zip, ")
    inst_id = body["instance"]["id"]

    run_worker()  # the container is created
    advance(session, driver, settings, times=1)
    pushed = {path for (_, _, path) in driver.files}
    assert pushed == {deploys.CONTAINER_BUILDPACK, deploys.CONTAINER_ARCHIVE}
    advance(session, driver, settings, times=1)

    [dep] = tenant.get(f"/v1/instances/{inst_id}/deployments").json()
    assert (dep["status"], dep["stack_label"]) == ("succeeded", "Python (Flask, gunicorn)")
    assert "Nhận ra" in dep["log"]
    with session() as db:
        assert db.get(Deployment, dep["id"]).archive is None  # not kept once in the container
    audit = tenant.get(f"/v1/orgs/{tenant.org_id}/audit").json()
    assert {"app.deploy", "app.deployed"} <= {e["action"] for e in audit}


def test_a_new_version_goes_to_the_same_instance(signup, run_worker, session, settings, driver):
    tenant = signup()
    inst_id = upload(tenant, STATIC).json()["instance"]["id"]
    run_worker()
    advance(session, driver, settings)

    again = send_version(tenant, inst_id, FLASK)
    assert again.status_code == 202 and again.json()["stack"] == "python"
    assert send_version(tenant, inst_id, STATIC).status_code == 409  # one at a time
    advance(session, driver, settings)

    latest = tenant.get(f"/v1/projects/{tenant.project_id}/deployments").json()
    assert [(d["instance_id"], d["stack"], d["status"]) for d in latest] == [
        (inst_id, "python", "succeeded")
    ]
    assert len(tenant.get(f"/v1/instances/{inst_id}/deployments").json()) == 2


def test_a_zip_that_cannot_work_is_refused_before_anything_is_made(signup):
    tenant = signup()
    refused = upload(tenant, zip_of({"notes.txt": "x"}))
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "invalid_upload"
    assert upload(tenant, b"not a zip").status_code == 422
    assert upload(tenant, b"").status_code == 422
    assert tenant.get(f"/v1/projects/{tenant.project_id}/instances").json() == []


def test_a_zip_over_the_limit_is_refused(signup, settings):
    settings.app_max_zip_mib = 1
    resp = upload(signup(), b"x" * (1024 * 1024 + 1))
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "too_large"


def test_the_buildpack_reports_failures(signup, run_worker, session, settings, driver):
    tenant = signup()
    inst_id = upload(tenant, STATIC).json()["instance"]["id"]
    run_worker()
    driver.buildpack_failure = "Build thất bại (mã thoát 1)"
    advance(session, driver, settings)
    [dep] = tenant.get(f"/v1/instances/{inst_id}/deployments").json()
    assert (dep["status"], dep["message"]) == ("failed", "Build thất bại (mã thoát 1)")


def test_a_deployment_whose_instance_is_gone_fails(signup, run_worker, session, settings, driver):
    tenant = signup()
    inst_id = upload(tenant, STATIC).json()["instance"]["id"]
    run_worker()
    assert tenant.delete(f"/v1/instances/{inst_id}").status_code == 202
    run_worker()
    advance(session, driver, settings, times=1)
    with session() as db:
        dep = db.scalars(select(Deployment)).one()
        assert dep.status == "failed" and "không chạy" in dep.message
        assert dep.archive is None


def test_a_slow_buildpack_is_collected_later(signup, run_worker, session, settings, driver):
    tenant = signup()
    inst_id = upload(tenant, STATIC).json()["instance"]["id"]
    run_worker()
    driver.exec_waits = 2  # the buildpack answers "still running" twice
    advance(session, driver, settings, times=3)
    [dep] = tenant.get(f"/v1/instances/{inst_id}/deployments").json()
    assert dep["status"] == "building"
    assert dep["log"] == "==> Đang chạy (giả lập)"  # the steps so far, while it builds
    advance(session, driver, settings, times=1)
    [dep] = tenant.get(f"/v1/instances/{inst_id}/deployments").json()
    assert dep["status"] == "succeeded"


def test_other_tenants_cannot_deploy_into_an_instance(signup, run_worker):
    an = signup("an@example.com", "Nhom An")
    binh = signup("binh@example.com", "Nhom Binh")
    inst_id = upload(an, STATIC).json()["instance"]["id"]
    run_worker()
    assert send_version(binh, inst_id, STATIC).status_code == 404
    assert binh.get(f"/v1/instances/{inst_id}/deployments").status_code == 404
    assert binh.get(f"/v1/projects/{an.project_id}/deployments").status_code == 404


def test_virtual_machines_and_a_switched_off_feature_take_no_apps(signup, run_worker, settings):
    tenant = signup()
    vm_id = tenant.create_instance("vm-1", type="virtual-machine").json()["instance"]["id"]
    run_worker()
    assert send_version(tenant, vm_id, STATIC).status_code == 409
    settings.apps_enabled = False
    assert upload(tenant, STATIC, name="web-2").status_code == 409
