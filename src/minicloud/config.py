from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Placement of new instances
    placement_policy: str = "spread"  # "spread" or "binpack"
    cpu_overcommit: float = 4.0
    memory_overcommit: float = 1.0
    reserved_memory_mib: int = 1024  # kept free for the laptop's own OS

    # Billing
    trial_credits: int = 500
    # When set, sign-up needs this code, so strangers who find the public API cannot use the
    # laptops. Leave empty only on a dev machine that is not reachable from the Internet.
    signup_invite_code: str = ""

    # Green cloud planner
    green_min_awake: int = 1
    green_pinned_nodes: str = "laptop-1"  # comma separated; runs the control plane, never sleeps
    green_idle_watts: float = 8.0  # replace with values measured on each laptop
    green_max_watts: float = 35.0

    # Background worker
    run_worker: bool = True
    worker_interval_seconds: float = 2.0
    metering_interval_seconds: float = 60.0

    # Public URLs for customer apps (ADR-0005). Empty public_domain turns the feature off.
    public_domain: str = ""  # e.g. vexenhanh.id.vn -> https://<instance>.vexenhanh.id.vn
    ingress_routes_file: str = ""  # Traefik dynamic config the control plane rewrites
    public_port_min: int = 20000
    public_port_max: int = 29999

    # Web console origins allowed to call the API from a browser (comma separated)
    cors_origins: str = "http://localhost:3000"

    @property
    def pinned_nodes(self) -> frozenset[str]:
        return frozenset(n.strip() for n in self.green_pinned_nodes.split(",") if n.strip())

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
