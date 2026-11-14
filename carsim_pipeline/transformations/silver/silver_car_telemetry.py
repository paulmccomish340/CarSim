from pyspark import pipelines as dp
from pyspark.sql import functions as F

# Source Delta table written continuously by Zerobus (override with pipeline parameter carsim.source_table).
SOURCE_TABLE = spark.conf.get("carsim.source_table", "pmc_sandbox.carsim.car_telemetry")
AUTONOMOUS_KEY = "__AUTONOMOUS__"  # stands for a NULL vehicle_reg (the autonomous car)


@dp.table(
    name="pmc_sandbox.carsim.silver_car_telemetry",
    comment="Cleaned car telemetry: smoke-test rows removed, event_time as timestamp, speed back-filled to mph, autonomous car labelled.",
    cluster_by=["reg_key"],
)
@dp.expect("valid_event_ts", "event_ts IS NOT NULL")
def silver_car_telemetry():
    return (
        spark.readStream.table(SOURCE_TABLE)
        .filter("vehicle_id <> 'smoke-test'")
        .select(
            F.to_timestamp("event_time").alias("event_ts"),
            "vehicle_id",
            "session_id",  # NULL for rows ingested before session_id was added
            F.coalesce(F.col("vehicle_reg"), F.lit(AUTONOMOUS_KEY)).alias("reg_key"),
            F.coalesce(F.col("vehicle_reg"), F.lit("Autonomous")).alias("registration"),
            "vehicle_reg",
            "driver_name",
            "driver_mode",
            # Rows ingested before the payload change only carry speed_kph.
            F.expr("coalesce(speed_mph, speed_kph / 1.609344)").alias("speed_mph"),
            "braking_force_kn",
            "acceleration_force_kn",
            "distance_to_vehicle_ahead_m",
            "battery_soc_pct",
            "range_km",
            "tyre_pressure_fl_bar",
            "tyre_pressure_fr_bar",
            "tyre_pressure_rl_bar",
            "tyre_pressure_rr_bar",
            "sim_time_s",
            "heading_deg",
            "x_m",
            "z_m",
            "latitude",
            "longitude",
        )
    )
