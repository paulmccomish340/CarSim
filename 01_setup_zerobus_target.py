# Databricks notebook source
# DBTITLE 1,Overview
# MAGIC %md
# MAGIC # Car sim: Zerobus target setup
# MAGIC
# MAGIC Creates the Unity Catalog objects that receive telemetry from the `aa-car-sim` app (`zerobus.py`) through **Zerobus Ingest**.
# MAGIC
# MAGIC Steps:
# MAGIC 1. Set the widgets (catalog, schema, table, region, service principal application ID).
# MAGIC 2. Create the schema and the **managed Delta** target table. Columns match the record built in `simulation.py::_telemetry` exactly. Zerobus does not create or alter tables.
# MAGIC 3. Grant the service principal the privileges Zerobus needs (`USE CATALOG`, `USE SCHEMA`, `SELECT`, `MODIFY`).
# MAGIC 4. Print the environment values to set on the Databricks App.
# MAGIC

# COMMAND ----------

# DBTITLE 1,Parameters
dbutils.widgets.text("catalog", "pmc_sandbox", "Catalog")
dbutils.widgets.text("schema", "carsim", "Schema")
dbutils.widgets.text("table", "car_telemetry", "Table (letters, digits, underscores only)")
dbutils.widgets.text("region", "westus2", "Azure region of the workspace")
dbutils.widgets.text("sp_application_id", "", "Service principal application (client) ID")

catalog = dbutils.widgets.get("catalog").strip()
schema = dbutils.widgets.get("schema").strip()
table = dbutils.widgets.get("table").strip()
region = dbutils.widgets.get("region").strip()
sp_app_id = dbutils.widgets.get("sp_application_id").strip()

full_table = f"{catalog}.{schema}.{table}"
print("Target table:", full_table)

# COMMAND ----------

# DBTITLE 1,Create schema and target table
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`")

# Column names and types mirror simulation.py::_telemetry + _tyres.
spark.sql(f"""
CREATE TABLE IF NOT EXISTS `{catalog}`.`{schema}`.`{table}` (
  vehicle_id                  STRING  COMMENT 'Vehicle identifier (ego)',
  session_id                  STRING  COMMENT 'Unique session UUID',
  driver_mode                 STRING  COMMENT 'human or autonomous',
  vehicle_reg                 STRING  COMMENT 'Vehicle registration entered by the human driver (NULL in autonomous mode)',
  driver_name                 STRING  COMMENT 'Human driver name (NULL in autonomous mode)',
  event_time                  STRING  COMMENT 'ISO-8601 UTC event time from the app',
  sim_time_s                  DOUBLE  COMMENT 'Simulation clock (s)',
  speed_mph                   DOUBLE  COMMENT 'Speed (mph)',
  heading_deg                 DOUBLE  COMMENT 'Heading, 0 = north (deg)',
  x_m                         DOUBLE  COMMENT 'Local x position (m)',
  z_m                         DOUBLE  COMMENT 'Local z position (m)',
  latitude                    DOUBLE  COMMENT 'Latitude (deg)',
  longitude                   DOUBLE  COMMENT 'Longitude (deg)',
  distance_to_vehicle_ahead_m DOUBLE  COMMENT 'Forward sensor gap, 200 = nothing in range (m)',
  braking_force_kn            DOUBLE  COMMENT 'Braking force (kN)',
  acceleration_force_kn       DOUBLE  COMMENT 'Acceleration force (kN)',
  range_km                    DOUBLE  COMMENT 'Estimated range (km)',
  battery_soc_pct             DOUBLE  COMMENT 'Battery state of charge (%)',
  tyre_pressure_fl_bar        DOUBLE  COMMENT 'Front-left tyre pressure (bar)',
  tyre_pressure_fr_bar        DOUBLE  COMMENT 'Front-right tyre pressure (bar)',
  tyre_pressure_rl_bar        DOUBLE  COMMENT 'Rear-left tyre pressure (bar)',
  tyre_pressure_rr_bar        DOUBLE  COMMENT 'Rear-right tyre pressure (bar)'
)
USING DELTA
COMMENT 'Self-driving car sim telemetry, ingested by Zerobus from the aa-car-sim app'
CLUSTER BY (session_id, event_time)
""")

display(spark.sql(f"DESCRIBE TABLE `{catalog}`.`{schema}`.`{table}`"))

# COMMAND ----------

# DBTITLE 1,Migrate existing table to new payload
# Zerobus rejects records containing fields that are not table columns, so an
# existing table must be extended before the updated app sends data.
# Idempotent: only adds columns that are missing. Requires table ownership (or MANAGE).
new_columns = {
    "session_id":  ("STRING", "Unique session UUID", "vehicle_id"),
    "driver_mode": ("STRING", "human or autonomous", "session_id"),
    "vehicle_reg": ("STRING", "Vehicle registration entered by the human driver (NULL in autonomous mode)", "driver_mode"),
    "driver_name": ("STRING", "Human driver name (NULL in autonomous mode)", "vehicle_reg"),
    "speed_mph":   ("DOUBLE", "Speed (mph)", "sim_time_s"),
}

fq = f"`{catalog}`.`{schema}`.`{table}`"
existing = {f.name for f in spark.table(f"{catalog}.{schema}.{table}").schema.fields}

for col, (dtype, comment, after) in new_columns.items():
    if col in existing:
        print("Exists:", col)
        continue
    spark.sql(f"ALTER TABLE {fq} ADD COLUMN {col} {dtype} COMMENT '{comment}' AFTER {after}")
    existing.add(col)
    print("Added:", col)

if "speed_kph" in existing:
    spark.sql(f"ALTER TABLE {fq} ALTER COLUMN speed_kph COMMENT 'Legacy speed (km/h), no longer sent by the app; use speed_mph'")
    print("Kept legacy column speed_kph (historical rows only)")

# Apply liquid clustering (idempotent – replaces any previous clustering)
spark.sql(f"ALTER TABLE {fq} CLUSTER BY (session_id, event_time)")
print("Clustered by: session_id, event_time")

display(spark.sql(f"DESCRIBE TABLE {fq}"))

# COMMAND ----------

# DBTITLE 1,Grant privileges to the service principal
# The service principal used by the app (ZEROBUS_CLIENT_ID) needs these privileges.
# Create the SP and an OAuth secret first: Settings > Identity and access > Service principals.
if not sp_app_id:
    print("Set the 'sp_application_id' widget and re-run this cell to apply grants.")
else:
    for stmt in [
        f"GRANT USE CATALOG ON CATALOG `{catalog}` TO `{sp_app_id}`",
        f"GRANT USE SCHEMA ON SCHEMA `{catalog}`.`{schema}` TO `{sp_app_id}`",
        f"GRANT SELECT, MODIFY ON TABLE `{catalog}`.`{schema}`.`{table}` TO `{sp_app_id}`",
    ]:
        spark.sql(stmt)
        print("OK:", stmt)
    display(spark.sql(f"SHOW GRANTS `{sp_app_id}` ON TABLE `{catalog}`.`{schema}`.`{table}`"))

# COMMAND ----------

# DBTITLE 1,App environment values
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
workspace_url = w.config.host.rstrip("/")
workspace_id = w.get_workspace_id()
zerobus_endpoint = f"https://{workspace_id}.zerobus.{region}.azuredatabricks.net"

print("Set these on the Databricks App (app.yaml env or Apps UI):\n")
print(f"ZEROBUS_WORKSPACE_URL = {workspace_url}")
print(f"ZEROBUS_INGEST_URL    = {zerobus_endpoint}")
print(f"ZEROBUS_TABLE         = {full_table}")
print(f"ZEROBUS_CLIENT_ID     = {sp_app_id or '<service principal application id>'}")
print( "ZEROBUS_CLIENT_SECRET = <client_secret>")

# COMMAND ----------

