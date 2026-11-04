"""
Runtime configuration.

Everything is read from environment variables so no values are baked into the
code. On Databricks Apps these are set in the app configuration (app.yaml or the
Apps UI). Secrets should be supplied through a Databricks secret resource.
"""

import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Config:
    # --- simulation ---
    sim_tick_hz: float = _float("SIM_TICK_HZ", 60)
    telemetry_hz: float = _float("TELEMETRY_HZ", 10)
    # Which side of the road traffic drives on: "left" (UK-style) or "right".
    drive_side: str = os.getenv("DRIVE_SIDE", "left").strip().lower()
    # Length of a human driver session (seconds).
    human_driver_seconds: float = _float("HUMAN_DRIVER_SECONDS", 120)
    num_ambient_cars: int = _int("NUM_AMBIENT_CARS", 6)
    num_pedestrians: int = _int("NUM_PEDESTRIANS", 28)
    # Local origin used to convert simulated metres into latitude / longitude.
    gps_origin_lat: float = _float("GPS_ORIGIN_LAT", 51.5074)
    gps_origin_lon: float = _float("GPS_ORIGIN_LON", -0.1278)

    # --- Zerobus ingestion (values supplied by the environment) ---
    zerobus_workspace_url: str = os.getenv("ZEROBUS_WORKSPACE_URL", "")
    zerobus_ingest_url: str = os.getenv("ZEROBUS_INGEST_URL", "")
    zerobus_table: str = os.getenv("ZEROBUS_TABLE", "")
    zerobus_client_id: str = os.getenv("ZEROBUS_CLIENT_ID", "")
    zerobus_client_secret: str = os.getenv("ZEROBUS_CLIENT_SECRET", "")
    # OAuth token request parameters. The token must be issued for the audience
    # the Zerobus endpoint expects, so these are configurable.
    zerobus_token_scope: str = os.getenv("ZEROBUS_TOKEN_SCOPE", "all-apis")
    zerobus_token_audience: str = os.getenv("ZEROBUS_TOKEN_AUDIENCE", "")
    zerobus_batch_size: int = _int("ZEROBUS_BATCH_SIZE", 200)
    zerobus_flush_seconds: float = _float("ZEROBUS_FLUSH_SECONDS", 2.0)

    @property
    def zerobus_enabled(self) -> bool:
        return all(
            (
                self.zerobus_workspace_url,
                self.zerobus_ingest_url,
                self.zerobus_table,
                self.zerobus_client_id,
                self.zerobus_client_secret,
            )
        )


config = Config()
