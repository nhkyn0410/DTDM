from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_node_watts(value: str) -> dict[str, tuple[float, float]]:
    """Parse "laptop-1=44.5/45.8,laptop-2=19.1/38.3" into {"laptop-1": (44.5, 45.8), ...}."""
    result = {}
    for item in value.split(","):
        if not item.strip():
            continue
        name, sep, watts = item.partition("=")
        idle, slash, peak = watts.partition("/")
        try:
            pair = (float(idle), float(peak))
        except ValueError:
            pair = None
        if not sep or not slash or not name.strip() or pair is None or not 0 <= pair[0] <= pair[1]:
            raise ValueError(f"expected <laptop>=<idle W>/<max W>, got {item.strip()!r}")
        result[name.strip()] = pair
    return result


class Settings(BaseSettings):
    """Runtime configuration from MINICLOUD_* environment variables (or a .env file)."""

    model_config = SettingsConfigDict(env_prefix="MINICLOUD_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./minicloud.db"

    # "fake" keeps a pretend 3-laptop cluster in memory (dev on Windows); "incus" uses the real one.
    driver: str = "fake"
    incus_url: str = "https://127.0.0.1:8443"
    incus_client_cert: str = ""
    incus_client_key: str = ""
    incus_ca_cert: str = ""  # /var/lib/incus/cluster.crt copied from a node: pins the server
    incus_verify: bool = True
    incus_image_server: str = "https://images.linuxcontainers.org"
    incus_storage_pool: str = "local"
    incus_network: str = "incusbr0"
    incus_nic_limit: str = "20Mbit"  # bandwidth cap per instance, each way; "" for none
    # Instances cannot reach one another over the shared bridge (no VPC until OVN).
    incus_port_isolation: bool = True
    # False where the nodes have no KVM (WSL without /dev/kvm): the API refuses VMs up front.
    allow_vms: bool = True
    # A new autoscaling member joins its group's URL once its app answers, waiting up to this.
    group_ready_timeout_seconds: float = 45.0

    # Placement of new instances
    placement_policy: str = "spread"  # "spread" or "binpack"
    cpu_overcommit: float = 4.0
    memory_overcommit: float = 1.0
    reserved_memory_mib: int = 1024  # kept free for the laptop's own OS

    # Billing
    trial_credits: int = 500
    # Reference rate shown next to prices, to compare with real providers (docs/bang-gia.md).
    # Credits stay a simulated currency: nobody pays real money.
    vnd_per_credit: float = 10.0
    # When set, sign-up needs this code, so strangers who find the public API cannot use the
    # laptops. Leave empty only on a dev machine that is not reachable from the Internet.
    signup_invite_code: str = ""

    # Green cloud planner
    green_min_awake: int = 1
    green_pinned_nodes: str = "laptop-1"  # comma separated; runs the control plane, never sleeps
    green_idle_watts: float = 8.0  # replace with values measured on each laptop
    green_max_watts: float = 35.0
    # Per laptop, overriding the two above: "laptop-1=44.5/45.8,laptop-2=19.1/38.3" (idle/max W)
    green_node_watts: str = ""
    # kg CO2 per kWh of Vietnam's grid: 0.681 in 2024 (estimate by HUST with the Ministry of
    # Agriculture and Environment); update when the ministry publishes a newer factor.
    green_grid_kg_co2_per_kwh: float = 0.681
    # MAC of each laptop's wired card for Wake-on-LAN: "laptop-3=E4:A8:DF:D4:1E:64,..."
    green_node_macs: str = ""
    # Where the magic packet goes: the LAN's broadcast address (laptop-1 must be on that LAN).
    green_wol_broadcast: str = "255.255.255.255"

    # Background worker
    run_worker: bool = True
    worker_interval_seconds: float = 2.0
    metering_interval_seconds: float = 60.0
    # A silence longer than this means the control plane was down (laptop off or asleep): the
    # running time it did not see is not billed (services/metering.skip_unobserved).
    metering_max_gap_seconds: float = 300.0

    # Public URLs for customer apps (ADR-0005). Empty public_domain turns the feature off.
    public_domain: str = ""  # e.g. vexenhanh.id.vn -> https://<instance>.vexenhanh.id.vn
    ingress_routes_file: str = ""  # Traefik dynamic config the control plane rewrites
    public_port_min: int = 20000
    public_port_max: int = 29999

    # Web console origins allowed to call the API from a browser (comma separated)
    cors_origins: str = "http://localhost:3000"

    # Rate limits per client address (threat model #2); 0 turns a limit off
    rate_limit_signups_per_hour: int = 5
    rate_limit_auth_failures: int = 50  # wrong API keys per 10 minutes before the address waits
    rate_limit_execs: int = 120  # commands run in instances per user per 10 minutes

    @property
    def pinned_nodes(self) -> frozenset[str]:
        return frozenset(n.strip() for n in self.green_pinned_nodes.split(",") if n.strip())

    @field_validator("green_node_watts")
    @classmethod
    def _check_node_watts(cls, value: str) -> str:
        parse_node_watts(value)  # a typo stops the app at startup instead of skewing every bill
        return value

    def node_mac(self, node: str) -> str | None:
        for item in self.green_node_macs.split(","):
            name, _, mac = item.partition("=")
            if name.strip() == node and mac.strip():
                return mac.strip()
        return None

    def node_watts(self, node: str) -> tuple[float, float]:
        """Idle and full-load power of a laptop, in W."""
        return parse_node_watts(self.green_node_watts).get(
            node, (self.green_idle_watts, self.green_max_watts)
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
