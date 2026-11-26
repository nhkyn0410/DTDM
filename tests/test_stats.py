"""`minicloud stats`: the figures the trial report needs."""

import json
import time
from datetime import timedelta

import pytest

from minicloud import cli
from minicloud.config import get_settings
from minicloud.models import utcnow
from minicloud.services.metering import run_metering_tick
from minicloud.services.stats import collect, organizations, percentile


def test_percentiles_use_the_nearest_rank():
    values = [float(v) for v in range(1, 11)]
    assert (percentile(values, 50), percentile(values, 95)) == (5.0, 10.0)
    assert percentile([], 50) is None


def test_the_report_counts_users_provisioning_and_usage(signup, run_worker, session):
    a = signup("a@example.com", "Nhom A")
    b = signup("b@example.com", "Nhom B")
    a.create_instance("web-1")
    a.create_instance("db-1", type="virtual-machine")
    b.create_instance("web-2")
    done = utcnow() + timedelta(seconds=30)
    run_worker(now=done)
    a.post(
        f"/v1/instances/{a.get(f'/v1/projects/{a.project_id}/instances').json()[0]['id']}/exec",
        json={"command": "uptime"},
    )
    with session() as db:
        run_metering_tick(db, done + timedelta(hours=1))
        figures = collect(db, utcnow(), 0.681)

    assert (figures["users"], figures["organizations"]) == (2, 2)
    assert figures["organizations_running_now"] == 2
    assert figures["instances"] == {"running": 3}
    containers = figures["create_seconds"]["container"]
    assert containers["count"] == 2
    assert containers["p50"] == pytest.approx(30, abs=5)
    assert figures["create_seconds"]["virtual-machine"]["count"] == 1
    assert figures["compute_hours"] == pytest.approx(3, abs=0.01)
    assert figures["credits_charged"] == pytest.approx(20 + 30 + 20, abs=0.01)
    assert figures["commands_run"] == 1


def test_the_stats_command_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINICLOUD_DATABASE_URL", f"sqlite:///{tmp_path / 'stats.db'}")
    get_settings.cache_clear()
    try:
        cli.main(["stats"])
    finally:
        get_settings.cache_clear()
    figures = json.loads(capsys.readouterr().out)
    assert figures["users"] == 0 and figures["create_seconds"] == {}


def test_organizations_show_who_signed_up_and_what_runs(signup, run_worker, session):
    an = signup("an@example.com", "Nhom An")
    binh = signup("binh@example.com", "Nhom Binh")
    binh.create_instance()
    run_worker()
    with session() as db:
        rows = {row["id"]: row for row in organizations(db, utcnow())}
    assert rows[an.org_id]["owner_email"] == "an@example.com"
    assert rows[an.org_id]["instances_total"] == 0
    assert rows[an.org_id]["credits"] == 100  # trial credits in the test settings
    assert rows[binh.org_id]["owner_email"] == "binh@example.com"
    assert (rows[binh.org_id]["instances_running"], rows[binh.org_id]["instances_total"]) == (1, 1)


def test_the_orgs_command_runs_on_an_empty_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINICLOUD_DATABASE_URL", f"sqlite:///{tmp_path / 'orgs.db'}")
    get_settings.cache_clear()
    try:
        cli.main(["orgs"])
    finally:
        get_settings.cache_clear()
    assert capsys.readouterr().out.strip() == "chưa có tổ chức nào"


def test_an_operation_records_how_long_the_work_took(signup, run_worker, session, driver):
    from minicloud.models import Operation

    tenant = signup()
    tenant.create_instance()
    slow_create = driver.create_instance

    def create_slowly(spec):
        time.sleep(0.3)  # Incus takes a while
        return slow_create(spec)

    driver.create_instance = create_slowly
    started = utcnow()
    run_worker(now=started)
    with session() as db:
        [op] = db.query(Operation).all()
    assert (op.finished_at - op.started_at).total_seconds() >= 0.25  # Windows clock ticks


def test_grant_credits_takes_a_negative_amount_for_the_demo(tmp_path, monkeypatch):
    # Demo scene 5 drains an organization with a negative grant; argparse must not read "-5"
    # as an option.
    monkeypatch.setenv("MINICLOUD_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as stop:
            cli.main(["grant-credits", "org_000000000000", "-5"])
    finally:
        get_settings.cache_clear()
    assert "org_000000000000" in str(stop.value)  # found no such org: the number parsed fine
