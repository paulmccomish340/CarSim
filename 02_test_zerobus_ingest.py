# Databricks notebook source
# DBTITLE 1,Overview
# MAGIC %md
# MAGIC # Car sim: Zerobus ingest smoke test
# MAGIC
# MAGIC Sends a few sample telemetry records to the target table through the Zerobus REST API, using the same service principal and request shape as `aa-car-sim/zerobus.py`. Use this to confirm credentials, grants and the endpoint before deploying the app.
# MAGIC
# MAGIC Prerequisites:
# MAGIC * Notebook 01 has been run, including the migration cell (adds `session_id`, `driver_mode`, `vehicle_reg`, `driver_name`, `speed_mph`).
# MAGIC * The service principal client ID and secret are stored in a secret scope (default scope `carsim`, keys `zerobus-client-id` and `zerobus-client-secret`).

# COMMAND ----------

# DBTITLE 1,Parameters
dbutils.widgets.text("catalog", "pmc_sandbox", "Catalog")
dbutils.widgets.text("schema", "carsim", "Schema")
dbutils.widgets.text("table", "car_telemetry", "Table")
dbutils.widgets.text("region", "westus2", "Azure region of the workspace")
dbutils.widgets.text("secret_scope", "carsim", "Secret scope")

catalog = dbutils.widgets.get("catalog").strip()
schema = dbutils.widgets.get("schema").strip()
table = dbutils.widgets.get("table").strip()
region = dbutils.widgets.get("region").strip()
scope = dbutils.widgets.get("secret_scope").strip()
full_table = f"{catalog}.{schema}.{table}"

dbutils.secrets.get(scope, "zerobus-client-id")
dbutils.secrets.get(scope, "zerobus-client-secret")

# COMMAND ----------

# DBTITLE 1,Get Zerobus OAuth token
import json
import requests
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
workspace_url = f"https://{spark.conf.get('spark.databricks.workspaceUrl')}"
workspace_id = w.get_workspace_id()
zerobus_endpoint = f"https://{workspace_id}.zerobus.{region}.azuredatabricks.net"

# Token scoped to the target table for Zerobus writes.
authorization_details = [
    {"type": "unity_catalog_privileges", "privileges": ["USE CATALOG"],
     "object_type": "CATALOG", "object_full_path": catalog},
    {"type": "unity_catalog_privileges", "privileges": ["USE SCHEMA"],
     "object_type": "SCHEMA", "object_full_path": f"{catalog}.{schema}"},
    {"type": "unity_catalog_privileges", "privileges": ["SELECT", "MODIFY"],
     "object_type": "TABLE", "object_full_path": full_table,
     "operations": ["zerobuswrite"]},
]

resp = requests.post(
    f"{workspace_url}/oidc/v1/token",
    auth=(client_id, client_secret),
    data={
        "grant_type": "client_credentials",
        "scope": "all-apis",
        "resource": f"api://databricks/workspaces/{workspace_id}/zerobusDirectWriteApi",
        "authorization_details": json.dumps(authorization_details),
    },
    timeout=30,
)
resp.raise_for_status()
token = resp.json()["access_token"]
print("Token acquired. Endpoint:", zerobus_endpoint)

# COMMAND ----------

# DBTITLE 1,Send sample telemetry records
import uuid
from datetime import datetime, timezone

# Same field names and types as simulation.py::_telemetry.
# Record 0 is autonomous (one session), records 1-2 are a human session (shared UUID).
now = datetime.now(timezone.utc).isoformat()
auto_session = str(uuid.uuid4())
human_session = str(uuid.uuid4())
samples = [
    {
        "vehicle_id": "smoke-test",
        "session_id": auto_session if i == 0 else human_session,
        "driver_mode": "autonomous" if i == 0 else "human",
        "vehicle_reg": None if i == 0 else "SM0K3 TST",
        "driver_name": None if i == 0 else "Smoke Test",
        "event_time": now,
        "sim_time_s": float(i),
        "speed_mph": 20.0 + i,
        "heading_deg": 90.0,
        "x_m": 10.0 * i,
        "z_m": 0.0,
        "latitude": 51.5074,
        "longitude": -0.1278,
        "distance_to_vehicle_ahead_m": 200.0,
        "braking_force_kn": 0.0,
        "acceleration_force_kn": 1.2,
        "range_km": 170.0,
        "battery_soc_pct": 70.0,
        "tyre_pressure_fl_bar": 2.5,
        "tyre_pressure_fr_bar": 2.5,
        "tyre_pressure_rl_bar": 2.5,
        "tyre_pressure_rr_bar": 2.5,
    }
    for i in range(3)
]

r = requests.post(
    f"{zerobus_endpoint}/zerobus/v1/tables/{full_table}/insert",
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    data=json.dumps(samples),
    timeout=30,
)
print(r.status_code, r.text[:500])
r.raise_for_status()

# COMMAND ----------

# DBTITLE 1,Verify records landed
import time

# Records typically materialise within a few seconds.
for _ in range(12):
    n = spark.sql(f"SELECT count(*) AS n FROM {full_table} WHERE vehicle_id = 'smoke-test'").first().n
    if n:
        break
    time.sleep(5)
print("smoke-test rows:", n)
display(spark.sql(f"SELECT * FROM {full_table} WHERE vehicle_id = 'smoke-test' ORDER BY sim_time_s"))

# COMMAND ----------

# DBTITLE 1,Optional: remove smoke-test rows
# Uncomment to clean up the test rows once ingestion is confirmed.
# spark.sql(f"DELETE FROM {full_table} WHERE vehicle_id = 'smoke-test'")