"""Read laptop power from Linux sysfs.

* RAPL (/sys/class/powercap/intel-rapl:N/energy_uj): energy of the CPU package, Intel and AMD.
  Readable by root only on recent kernels, so the exporter runs as root.
* Battery (/sys/class/power_supply/BAT*): while the charger is unplugged, the battery's power
  is the power of the whole laptop (screen, disk, Wi-Fi included), a handy check on RAPL.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

POWERCAP = Path("/sys/class/powercap")
POWER_SUPPLY = Path("/sys/class/power_supply")


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def rapl_package_domains(base: Path = POWERCAP) -> list[Path]:
    """Top-level package domains (intel-rapl:0, intel-rapl:1), not sub-zones like intel-rapl:0:0."""
    if not base.exists():
        return []
    return sorted(
        p
        for p in base.glob("intel-rapl:*")
        if p.name.count(":") == 1 and (p / "energy_uj").exists()
    )


@dataclass(frozen=True)
class PowerSample:
    watts: float
    joules: float
    seconds: float


class RaplMeter:
    """Turns the ever-growing RAPL energy counters into average power between two samples."""

    def __init__(self, domains: list[Path], clock: Callable[[], float] = time.monotonic) -> None:
        self.domains = list(domains)
        self.clock = clock
        self.max_range = {
            d.name: _read_int(d / "max_energy_range_uj") or 2**32 for d in self.domains
        }
        self._last: tuple[float, dict[str, int]] | None = None

    @classmethod
    def discover(
        cls, base: Path = POWERCAP, clock: Callable[[], float] = time.monotonic
    ) -> "RaplMeter":
        return cls(rapl_package_domains(base), clock)

    def available(self) -> bool:
        return bool(self.domains)

    def sample(self) -> PowerSample | None:
        """Average power since the previous call; None on the first call or if RAPL is missing."""
        if not self.domains:
            return None
        now = self.clock()
        readings = {d.name: _read_int(d / "energy_uj") for d in self.domains}
        if any(v is None for v in readings.values()):
            return None
        previous, self._last = self._last, (now, readings)  # type: ignore[assignment]
        if previous is None:
            return None
        then, before = previous
        seconds = now - then
        if seconds <= 0:
            return None
        total_uj = 0
        for name, value in readings.items():
            delta = value - before[name]
            if delta < 0:  # the counter wrapped around
                delta += self.max_range[name]
            total_uj += delta
        joules = total_uj / 1e6
        return PowerSample(watts=joules / seconds, joules=joules, seconds=seconds)


@dataclass(frozen=True)
class BatteryReading:
    name: str
    status: str
    watts: float | None
    capacity_percent: int | None


def read_batteries(base: Path = POWER_SUPPLY) -> list[BatteryReading]:
    readings = []
    if not base.exists():
        return readings
    for path in sorted(base.iterdir()):
        if _read_text(path / "type") != "Battery":
            continue
        power_uw = _read_int(path / "power_now")
        if power_uw is None:
            current_ua = _read_int(path / "current_now")
            voltage_uv = _read_int(path / "voltage_now")
            if current_ua is not None and voltage_uv is not None:
                power_uw = current_ua * voltage_uv // 1_000_000  # µA × µV = 1e-12 W
        readings.append(
            BatteryReading(
                name=path.name,
                status=_read_text(path / "status") or "Unknown",
                watts=abs(power_uw) / 1e6 if power_uw is not None else None,
                capacity_percent=_read_int(path / "capacity"),
            )
        )
    return readings


def on_ac_power(base: Path = POWER_SUPPLY) -> bool | None:
    if not base.exists():
        return None
    for path in sorted(base.iterdir()):
        if _read_text(path / "type") == "Mains":
            return _read_int(path / "online") == 1
    return None


def system_watts(base: Path = POWER_SUPPLY) -> float | None:
    """Whole-laptop power, known only while it runs on battery."""
    if on_ac_power(base):
        return None
    watts = [
        b.watts for b in read_batteries(base) if b.status == "Discharging" and b.watts is not None
    ]
    return sum(watts) if watts else None
