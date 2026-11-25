"""`minicloud stats`: the figures the trial report needs."""

import json
from datetime import timedelta

import pytest

from minicloud import cli
from minicloud.config import get_settings
from minicloud.models import utcnow
from minicloud.services.metering import run_metering_tick
from minicloud.services.stats import collect, percentile


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
