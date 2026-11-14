# Self-Driving Car Sim

A browser-based 3D simulation of a self-driving car on a 3 x 3 block city. Each
junction has traffic signals, houses line every block, pedestrians walk the
sidewalks, and ambient traffic shares the roads. The ego vehicle drives itself
and streams telemetry to Databricks Zerobus.

## Layout

```
app.py              FastAPI app: UI, state API, background simulation and forwarder
simulation.py       Simulation core (road graph, signals, IDM car-following, sensors)
zerobus.py          Zerobus batching, OAuth token and ingest call
config.py           Environment-driven configuration
static/index.html   Browser shell and HUD
static/main.js      Three.js scene, camera and telemetry display
app.yaml            Databricks Apps run command and environment
requirements.txt    Python dependencies
```

## How it works

- The Python backend runs the simulation at 20 Hz. It is the source of truth for
  the vehicle, traffic, pedestrians and signals.
- The browser polls `/api/state` and renders with Three.js (r128, from CDN).
- Driving uses the Intelligent Driver Model for following distance. The vehicle
  stops at red signals and yellow signals it cannot clear, and it turns at random
  junctions without U-turns.
- The "vehicle ahead" distance comes from a forward sensor with a 200 m range.
- Braking and acceleration forces are derived from mass times deceleration or
  acceleration.
- Tyre pressures are reported for all four tyres. The rear-left tyre develops a
  slow leak after about 150 s so the data includes a realistic anomaly.
- Range is estimated from the battery state of charge and a rated consumption of
  0.165 kWh/km.

## Telemetry fields (sent to Zerobus, one record per 1/TELEMETRY_HZ)

| Field | Unit |
|---|---|
| vehicle_id, event_time, sim_time_s | id, ISO-8601 UTC, s |
| speed_kph, heading_deg | km/h, degrees (0 = north) |
| x_m, z_m | local metres |
| latitude, longitude | degrees (from GPS_ORIGIN_LAT/LON) |
| distance_to_vehicle_ahead_m | m (200 = nothing detected in range) |
| braking_force_kn, acceleration_force_kn | kN |
| tyre_pressure_fl_bar, _fr_, _rl_, _rr_ | bar |
| range_km, battery_soc_pct | km, % |

## Configuration

All configuration is through environment variables. No Zerobus values are stored
in the code.

| Variable | Purpose |
|---|---|
| `ZEROBUS_WORKSPACE_URL` | Workspace URL, used to obtain an OAuth token |
| `ZEROBUS_INGEST_URL` | Zerobus ingest endpoint for the target table |
| `ZEROBUS_TABLE` | Fully qualified target table (catalog.schema.table) |
| `ZEROBUS_CLIENT_ID` | Service principal client ID |
| `ZEROBUS_CLIENT_SECRET` | Service principal secret (use a Databricks secret) |
| `ZEROBUS_BATCH_SIZE` | Records per request (default 200) |
| `ZEROBUS_FLUSH_SECONDS` | Flush interval (default 2) |
| `SIM_TICK_HZ` | Simulation rate (default 20) |
| `TELEMETRY_HZ` | Telemetry emit rate (default 10) |
| `NUM_AMBIENT_CARS` / `NUM_PEDESTRIANS` | Population sizes (defaults 6 / 28) |
| `GPS_ORIGIN_LAT` / `GPS_ORIGIN_LON` | Map origin for lat/lon conversion |

If the Zerobus variables are missing, the app still runs and the HUD reports that
telemetry is local only.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
# open http://localhost:8000
```

## Deploy to Databricks Apps

1. Upload this folder to a workspace path, or connect it as a Git source.
2. Create the app and deploy it with `app.yaml`. Set the Zerobus values in the app
   configuration. Add the client secret as a secret resource, not a literal.
3. Open the app URL. The HUD shows the Zerobus status.

## Notes

- Zerobus wire format: `zerobus.py` posts `{table_name, records[]}` with a bearer
  token from the OAuth client-credentials flow. Check this against the endpoint
  configured for your table and adjust `_send` if the request body differs, or
  switch to the official Databricks Zerobus SDK.
- The browser loads Three.js from cdnjs. If outbound CDN access is restricted,
  vendor `three.min.js` into `static/` and update the script tag in `index.html`.
