"""Prometheus exporter for laptop power. Runs as root on every node (port 9101).

sudo python3 -m minicloud.green.exporter --node laptop-1
"""

import argparse
import socket
import time
from pathlib import Path

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, start_http_server

from .power import POWER_SUPPLY, POWERCAP, RaplMeter, on_ac_power, read_batteries, system_watts


class NodeCollector:
    def __init__(
        self,
        node: str,
        registry: CollectorRegistry = REGISTRY,
        meter: RaplMeter | None = None,
        power_supply: Path = POWER_SUPPLY,
    ) -> None:
        self.node = node
        self.power_supply = power_supply
        self.meter = meter if meter is not None else RaplMeter.discover(POWERCAP)
        labels = ["node"]
        self.cpu_watts = Gauge(
            "minicloud_node_cpu_package_watts",
            "CPU package power from RAPL",
            labels,
            registry=registry,
        )
        self.cpu_joules = Counter(
            "minicloud_node_cpu_package_joules",
            "CPU package energy from RAPL",
            labels,
            registry=registry,
        )
        self.system_watts = Gauge(
            "minicloud_node_system_watts",
            "Whole-laptop power read from the battery while unplugged",
            labels,
            registry=registry,
        )
        self.on_battery = Gauge(
            "minicloud_node_on_battery",
            "1 when the laptop runs on battery",
            labels,
            registry=registry,
        )
        self.battery_percent = Gauge(
            "minicloud_node_battery_capacity_percent",
            "Battery charge level",
            labels,
            registry=registry,
        )

    def collect(self) -> None:
        sample = self.meter.sample()
        if sample is not None:
            self.cpu_watts.labels(self.node).set(sample.watts)
            self.cpu_joules.labels(self.node).inc(sample.joules)
        ac = on_ac_power(self.power_supply)
        if ac is not None:
            self.on_battery.labels(self.node).set(0 if ac else 1)
        watts = system_watts(self.power_supply)
        if watts is not None:
            self.system_watts.labels(self.node).set(watts)
        batteries = read_batteries(self.power_supply)
        if batteries and batteries[0].capacity_percent is not None:
            self.battery_percent.labels(self.node).set(batteries[0].capacity_percent)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Exporter đo điện năng laptop (RAPL + pin)")
    parser.add_argument("--port", type=int, default=9101)
    parser.add_argument("--interval", type=float, default=5.0, help="giây giữa hai lần đo")
    parser.add_argument("--node", default=socket.gethostname())
    args = parser.parse_args(argv)

    collector = NodeCollector(args.node)
    if not collector.meter.available():
        print("Cảnh báo: không thấy RAPL; chỉ đo được qua pin khi rút sạc.")
    start_http_server(args.port)
    print(f"Exporter cho {args.node} đang chạy ở cổng {args.port}")
    while True:
        collector.collect()
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
