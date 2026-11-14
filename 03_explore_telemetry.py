# Databricks notebook source
# DBTITLE 1,Overview
# MAGIC %md
# MAGIC # Car sim: explore ingested telemetry
# MAGIC
# MAGIC Creates a typed view over the Zerobus target table (parses `event_time` to `TIMESTAMP`) and runs a few checks: ingest freshness, driving summary, human driver sessions (by registration and driver name), and the rear-left tyre slow-leak anomaly built into the simulation.
# MAGIC
# MAGIC Speed is reported in mph. Rows ingested before the payload change only have `speed_kph`; the view converts these to mph so history stays comparable.

# COMMAND ----------

# DBTITLE 1,Parameters
dbutils.widgets.text("catalog", "pmc_sandbox", "Catalog")
dbutils.widgets.text("schema", "carsim", "Schema")
dbutils.widgets.text("table", "car_telemetry", "Table")

catalog = dbutils.widgets.get("catalog").strip()
schema = dbutils.widgets.get("schema").strip()
table = dbutils.widgets.get("table").strip()
full_table = f"{catalog}.{schema}.{table}"
view = f"{catalog}.{schema}.{table}_v"

# COMMAND ----------

# DBTITLE 1,Create typed telemetry view
cols = set(spark.table(full_table).columns)

# Back-fill mph from the legacy km/h column for rows ingested before the payload change.
if "speed_kph" in cols:
    speed_expr = "coalesce(speed_mph, round(speed_kph / 1.609344, 2))"
    except_cols = "vehicle_id, driver_mode, vehicle_reg, driver_name, event_time, speed_mph, speed_kph"
else:
    speed_expr = "speed_mph"
    except_cols = "vehicle_id, driver_mode, vehicle_reg, driver_name, event_time, speed_mph"

spark.sql(f"""
CREATE OR REPLACE VIEW {view}
COMMENT 'Typed view over Zerobus car telemetry'
AS SELECT
  vehicle_id,
  coalesce(driver_mode, 'autonomous')  AS driver_mode,
  upper(trim(vehicle_reg))             AS vehicle_reg,
  trim(driver_name)                    AS driver_name,
  CAST(event_time AS TIMESTAMP)        AS event_ts,
  {speed_expr}                         AS speed_mph,
  * EXCEPT ({except_cols})
FROM {full_table}
WHERE vehicle_id <> 'smoke-test'
""")
print("Created view", view)

# COMMAND ----------

# DBTITLE 1,Ingest freshness
display(spark.sql(f"""
SELECT vehicle_id,
       count(*)                                   AS records,
       min(event_ts)                              AS first_event,
       max(event_ts)                              AS last_event,
       timestampdiff(SECOND, max(event_ts), current_timestamp()) AS seconds_since_last
FROM {view}
GROUP BY vehicle_id
"""))

# COMMAND ----------

# DBTITLE 1,Driving summary per minute
display(spark.sql(f"""
SELECT date_trunc('MINUTE', event_ts)          AS minute,
       count_if(driver_mode = 'human')        AS human_records,
       round(avg(speed_mph), 1)               AS avg_speed_mph,
       round(max(speed_mph), 1)               AS max_speed_mph,
       round(max(braking_force_kn), 2)        AS max_braking_kn,
       round(min(distance_to_vehicle_ahead_m), 1) AS min_gap_m,
       round(min(battery_soc_pct), 2)         AS min_soc_pct,
       round(min(range_km), 1)                AS min_range_km
FROM {view}
GROUP BY 1
ORDER BY 1 DESC
"""))

# COMMAND ----------

# DBTITLE 1,Human driver sessions (registration and name)
# One row per human driving session. A new session starts when the registration or
# driver changes, or after a gap of more than 30 s between human records.
display(spark.sql(f"""
WITH h AS (
  SELECT *,
         CASE WHEN lag(vehicle_reg) OVER w IS DISTINCT FROM vehicle_reg
                OR lag(driver_name) OVER w IS DISTINCT FROM driver_name
                OR timestampdiff(SECOND, lag(event_ts) OVER w, event_ts) > 30
              THEN 1 ELSE 0 END AS new_session
  FROM {view}
  WHERE driver_mode = 'human'
  WINDOW w AS (ORDER BY event_ts)
),
s AS (
  SELECT *, sum(new_session) OVER (ORDER BY event_ts) AS session_id FROM h
)
SELECT session_id,
       vehicle_reg,
       driver_name,
       min(event_ts)                                         AS session_start,
       max(event_ts)                                         AS session_end,
       timestampdiff(SECOND, min(event_ts), max(event_ts))   AS duration_s,
       count(*)                                              AS records,
       round(avg(speed_mph), 1)                              AS avg_speed_mph,
       round(max(speed_mph), 1)                              AS max_speed_mph,
       round(max(braking_force_kn), 2)                       AS max_braking_kn,
       round(min(distance_to_vehicle_ahead_m), 1)            AS min_gap_m
FROM s
GROUP BY session_id, vehicle_reg, driver_name
ORDER BY session_start DESC
"""))

# COMMAND ----------

# DBTITLE 1,Tyre pressure anomaly (rear-left leak)
# Flag records where any tyre deviates more than 0.15 bar from the average of the other three.
display(spark.sql(f"""
WITH t AS (
  SELECT event_ts, sim_time_s,
         tyre_pressure_fl_bar AS fl, tyre_pressure_fr_bar AS fr,
         tyre_pressure_rl_bar AS rl, tyre_pressure_rr_bar AS rr
  FROM {view}
)
SELECT event_ts, sim_time_s, fl, fr, rl, rr,
       round(rl - (fl + fr + rr) / 3, 3) AS rl_deviation_bar
FROM t
WHERE abs(rl - (fl + fr + rr) / 3) > 0.15
   OR abs(fl - (fr + rl + rr) / 3) > 0.15
   OR abs(fr - (fl + rl + rr) / 3) > 0.15
   OR abs(rr - (fl + fr + rl) / 3) > 0.15
ORDER BY event_ts DESC
LIMIT 200
"""))

# COMMAND ----------

