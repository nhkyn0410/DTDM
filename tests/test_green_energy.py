"""Green cloud: each instance's share of its laptop's power, and Wh on the bill."""

from datetime import timedelta

import pytest

from minicloud import reconciler
from minicloud.config import Settings
from minicloud.green.energy import EnergyMeter, RunningInstance
from minicloud.models import utcnow
from minicloud.services import green
from minicloud.services.metering import run_metering_tick
from minicloud.services.statements import month_bounds, month_of


def watts_of(node):
    return {"laptop-2": (19.1, 38.3)}.get(node, (10.0, 50.0))


def test_idle_power_is_shared_by_vcpus_until_cpu_time_is_known():
    meter = EnergyMeter()
    first = [
        RunningInstance("a", "laptop-2", 1, cpu_seconds=100.0),
        RunningInstance("b", "laptop-2", 2, cpu_seconds=50.0),
    ]
    shares = meter.estimate(first, {"laptop-2": 16}, watts_of, now=1000.0)
    assert shares == pytest.approx({"a": 19.1 / 3, "b": 19.1 * 2 / 3}, abs=1e-3)  # W, 3 decimals

    # A minute later, a kept half a thread busy and b nothing: the extra power is all a's.
    later = [
        RunningInstance("a", "laptop-2", 1, cpu_seconds=130.0),
        RunningInstance("b", "laptop-2", 2, cpu_seconds=50.0),
    ]
    shares = meter.estimate(later, {"laptop-2": 16}, watts_of, now=1060.0)
    extra = (38.3 - 19.1) * 0.5 / 16
    assert shares["a"] == pytest.approx(19.1 / 3 + extra, abs=1e-3)
    assert shares["b"] == pytest.approx(19.1 * 2 / 3, abs=1e-3)
    assert sum(shares.values()) == pytest.approx(19.1 + extra, abs=2e-3)  # the whole laptop


def test_a_restarted_instance_or_an_unknown_laptop_counts_as_idle():
    meter = EnergyMeter()
    meter.estimate([RunningInstance("a", "laptop-1", 1, 500.0)], {"laptop-1": 8}, watts_of, 0.0)
    restarted = [RunningInstance("a", "laptop-1", 1, 3.0)]  # the counter went back to zero
    assert meter.estimate(restarted, {"laptop-1": 8}, watts_of, 60.0) == {"a": 10.0}
    # Incus did not list the laptop (asleep, offline): no utilisation, idle power only.
    busy = [RunningInstance("a", "laptop-1", 1, 63.0)]
    assert meter.estimate(busy, {}, watts_of, 120.0) == {"a": 10.0}


def test_instances_that_stop_are_forgotten():
    meter = EnergyMeter()
    meter.estimate([RunningInstance("a", "laptop-1", 1, 5.0)], {"laptop-1": 8}, watts_of, 0.0)
    meter.estimate([], {"laptop-1": 8}, watts_of, 60.0)
    assert meter._last == {}


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        driver="fake",
        run_worker=False,
        trial_credits=100,
        green_node_watts="laptop-1=10/50,laptop-2=10/50,laptop-3=10/50",
    )


def worker_tick(app, at, meter, clock):
    """What the background worker does once a minute: reconcile, estimate power, bill."""
    with app.state.session_factory() as db:
        report = reconciler.reconcile(db, app.state.driver, app.state.settings, now=at)
        power = green.estimate_power(
            db, app.state.driver, app.state.settings, report.observed, meter, now=clock
        )
        run_metering_tick(db, at, power=power)


def test_the_bill_shows_the_energy_of_each_minute(app, signup, run_worker, driver):
    tenant = signup()
    start, _ = month_bounds(month_of(utcnow()))
    t0 = start + timedelta(days=1)
    inst = tenant.create_instance(flavor="s1.small").json()["instance"]
    run_worker(now=t0)
    [(project, name)] = driver.instances
    meter = EnergyMeter()

    driver.cpu_seconds[(project, name)] = 0.0
    worker_tick(app, t0 + timedelta(minutes=1), meter, clock=1000.0)  # idle share: 10 W
    driver.cpu_seconds[(project, name)] = 48.0  # 0.8 of a thread for a minute: u = 0.1 of 8
    worker_tick(app, t0 + timedelta(minutes=2), meter, clock=1060.0)  # 10 + 40 × 0.1 = 14 W

    statement = tenant.get(f"/v1/orgs/{tenant.org_id}/statements/{month_of(t0)}").json()
    assert statement["energy_wh"] == pytest.approx((10 + 14) / 60, abs=0.01)
    billing = tenant.get(f"/v1/orgs/{tenant.org_id}/billing").json()
    assert billing["watts"] == pytest.approx(14)
    assert billing["month_energy_wh"] == pytest.approx(0.4, abs=0.01)

    cluster = tenant.get("/v1/green").json()
    node = tenant.get(f"/v1/instances/{inst['id']}").json()["node"]
    assert {n["name"]: n["watts"] for n in cluster["nodes"]}[node] == pytest.approx(14)
    assert cluster["cluster_watts"] == pytest.approx(14 + 10 + 10)  # two idle laptops
    assert (cluster["awake"], cluster["total"], cluster["saved_watts"]) == (3, 3, 0)


def test_a_sleeping_laptop_draws_nothing_and_counts_as_saved(signup, driver):
    tenant = signup()
    driver.set_asleep("laptop-3", True)
    cluster = tenant.get("/v1/green").json()
    assert (cluster["awake"], cluster["saved_watts"], cluster["cluster_watts"]) == (2, 10, 20)
    assert {n["name"]: n["awake"] for n in cluster["nodes"]}["laptop-3"] is False


def test_the_cluster_view_needs_an_account(client):
    assert client.get("/v1/green").status_code == 401
