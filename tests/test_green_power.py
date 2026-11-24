import sys

import pytest
from prometheus_client import CollectorRegistry

from minicloud.green.attribution import attribute_power
from minicloud.green.exporter import NodeCollector
from minicloud.green.power import (
    RaplMeter,
    on_ac_power,
    rapl_package_domains,
    read_batteries,
    system_watts,
)
from minicloud.green.wol import magic_packet


def write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def fake_package(base, energy_uj: int, max_range: int = 1_000_000_000):
    # Real sysfs names it intel-rapl:0; ':' is not allowed in Windows file names, so tests
    # hand the domain directory to RaplMeter directly.
    domain = base / "package-0"
    write(domain / "energy_uj", str(energy_uj))
    write(domain / "max_energy_range_uj", str(max_range))
    return domain


def fake_battery(base, status: str = "Discharging", online: str = "0") -> None:
    write(base / "BAT0" / "type", "Battery")
    write(base / "BAT0" / "status", status)
    write(base / "BAT0" / "current_now", "1500000")  # 1.5 A
    write(base / "BAT0" / "voltage_now", "12000000")  # 12 V
    write(base / "BAT0" / "capacity", "76")
    write(base / "AC" / "type", "Mains")
    write(base / "AC" / "online", online)


def test_rapl_meter_handles_counter_wraparound(tmp_path):
    domain = fake_package(tmp_path, 999_000_000)
    ticks = iter([0.0, 2.0])
    meter = RaplMeter([domain], clock=lambda: next(ticks))
    assert meter.sample() is None  # the first call only sets the baseline
    write(domain / "energy_uj", "19000000")  # wrapped: 1 J before the wrap + 19 J after
    sample = meter.sample()
    assert sample.joules == pytest.approx(20.0)
    assert sample.watts == pytest.approx(10.0)


@pytest.mark.skipif(sys.platform == "win32", reason="':' is not allowed in Windows file names")
def test_discovery_keeps_packages_and_skips_subzones(tmp_path):
    write(tmp_path / "intel-rapl:0" / "energy_uj", "1")
    write(tmp_path / "intel-rapl:0:0" / "energy_uj", "1")
    assert [p.name for p in rapl_package_domains(tmp_path)] == ["intel-rapl:0"]


def test_a_laptop_without_rapl_is_not_an_error(tmp_path):
    meter = RaplMeter.discover(tmp_path / "khong-co")
    assert not meter.available()
    assert meter.sample() is None


def test_the_battery_gives_whole_laptop_power_when_unplugged(tmp_path):
    fake_battery(tmp_path)
    [battery] = read_batteries(tmp_path)
    assert battery.watts == pytest.approx(18.0)
    assert battery.capacity_percent == 76
    assert on_ac_power(tmp_path) is False
    assert system_watts(tmp_path) == pytest.approx(18.0)


def test_power_is_unknown_while_charging(tmp_path):
    fake_battery(tmp_path, status="Charging", online="1")
    assert system_watts(tmp_path) is None


def test_exporter_publishes_node_metrics(tmp_path):
    domain = fake_package(tmp_path / "powercap", 1_000_000)
    fake_battery(tmp_path / "power_supply")
    ticks = iter([0.0, 5.0])
    registry = CollectorRegistry()
    collector = NodeCollector(
        "laptop-1",
        registry,
        meter=RaplMeter([domain], clock=lambda: next(ticks)),
        power_supply=tmp_path / "power_supply",
    )
    collector.collect()
    write(domain / "energy_uj", "51000000")  # +50 J in 5 s
    collector.collect()

    labels = {"node": "laptop-1"}
    value = registry.get_sample_value
    assert value("minicloud_node_cpu_package_watts", labels) == pytest.approx(10.0)
    assert value("minicloud_node_cpu_package_joules_total", labels) == pytest.approx(50.0)
    assert value("minicloud_node_system_watts", labels) == pytest.approx(18.0)
    assert value("minicloud_node_on_battery", labels) == 1
    assert value("minicloud_node_battery_capacity_percent", labels) == 76


def test_magic_packet_layout():
    packet = magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:12] == bytes.fromhex("aabbccddeeff") == packet[-6:]
    with pytest.raises(ValueError):
        magic_packet("khong-phai-mac")


def test_power_split_follows_cpu_time_and_adds_up():
    shares = attribute_power(
        node_watts=20.0, idle_watts=8.0, cpu_seconds={"a": 30.0, "b": 10.0}, vcpus={"a": 1, "b": 1}
    )
    assert shares["a"] == pytest.approx(4.0 + 9.0)  # half the idle power + 3/4 of the rest
    assert shares["b"] == pytest.approx(4.0 + 3.0)
    assert sum(shares.values()) == pytest.approx(20.0)
    assert attribute_power(10.0, 8.0, {}, {}) == {}
