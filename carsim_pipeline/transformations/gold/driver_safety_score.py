from pyspark import pipelines as dp
from pyspark.sql import functions as F

# Safe driver score thresholds. Defaults match the car-telemetry-live app (score_query);
# override any of them with pipeline parameters (configuration keys below).
HARSH_BRAKE_KN = float(spark.conf.get("carsim.harsh_brake_kn", "8"))
HARSH_ACCEL_KN = float(spark.conf.get("carsim.harsh_accel_kn", "3"))
SPEED_LIMIT_MPH = float(spark.conf.get("carsim.speed_limit_mph", "30"))
SAFE_GAP_SECONDS = float(spark.conf.get("carsim.safe_gap_seconds", "2"))
NOMINAL_TYRE_BAR = float(spark.conf.get("carsim.nominal_tyre_bar", "2.5"))
TYRE_TOLERANCE_BAR = float(spark.conf.get("carsim.tyre_tolerance_bar", "0.15"))
LOW_SOC_PCT = float(spark.conf.get("carsim.low_soc_pct", "20"))
LOW_RANGE_KM = float(spark.conf.get("carsim.low_range_km", "30"))
# The sim's forward sensor floors at 0.1 m, so a gap <= 0.1 m means contact (a crash).
CRASH_GAP_M = float(spark.conf.get("carsim.crash_gap_m", "0.1"))
CRASH_MAX_SCORE = float(spark.conf.get("carsim.crash_max_score", "10"))


def _rate(condition: str):
    """Share of records meeting a condition (NULLs count as not meeting it)."""
    return F.avg(F.expr(f"CASE WHEN {condition} THEN 1 ELSE 0 END"))


@dp.materialized_view(
    name="pmc_sandbox.carsim.driver_safety_score",
    comment="Safe driver score (0-100) per registration, dri and sessionver and session, same logic as the car-telemetry-live app.",
    spark_conf={"pipelines.trigger.interval": "5 seconds"},
)
def driver_safety_score():
    tyre_dev = F.greatest(
        *[F.abs(F.col(c) - F.lit(NOMINAL_TYRE_BAR)) for c in
          ["tyre_pressure_fl_bar", "tyre_pressure_fr_bar", "tyre_pressure_rl_bar", "tyre_pressure_rr_bar"]]
    )
    agg = (
        spark.read.table("pmc_sandbox.carsim.silver_car_telemetry")
        .withColumn("tyre_dev", tyre_dev)
        .groupBy("reg_key", "driver_name", "session_id")
        .agg(
            F.max("registration").alias("registration"),  # one value per reg_key
            F.count(F.lit(1)).alias("records"),
            F.max("event_ts").alias("last_seen"),
            _rate(f"braking_force_kn > {HARSH_BRAKE_KN}").alias("harsh_brake_rate"),
            _rate(f"acceleration_force_kn > {HARSH_ACCEL_KN}").alias("harsh_accel_rate"),
            _rate(f"speed_mph > {SPEED_LIMIT_MPH}").alias("speeding_rate"),
            _rate(f"speed_mph > 2 AND distance_to_vehicle_ahead_m < speed_mph * 0.44704 * {SAFE_GAP_SECONDS}").alias("tailgating_rate"),
            F.avg("tyre_dev").alias("avg_tyre_dev"),
            _rate(f"battery_soc_pct < {LOW_SOC_PCT} OR range_km < {LOW_RANGE_KM}").alias("low_battery_rate"),
            F.min("distance_to_vehicle_ahead_m").alias("min_gap_m"),
            F.max(F.expr(f"CASE WHEN distance_to_vehicle_ahead_m <= {CRASH_GAP_M} THEN 1 ELSE 0 END")).alias("crashed"),
        )
    )
    pen = (
        agg.withColumn("p_brake", F.expr("least(25, harsh_brake_rate * 150)"))
        .withColumn("p_accel", F.expr("least(25, harsh_accel_rate * 100)"))
        .withColumn("p_speed", F.expr("least(15, speeding_rate * 50)"))
        .withColumn("p_gap", F.expr("least(15, tailgating_rate * 100)"))
        .withColumn("p_tyre", F.expr(f"least(10, greatest(0, avg_tyre_dev - {TYRE_TOLERANCE_BAR}) * 25)"))
        .withColumn("p_battery", F.expr("least(10, low_battery_rate * 10)"))
    )
    raw = "greatest(0, 100 - p_brake - p_accel - p_speed - p_gap - p_tyre - p_battery)"
    return pen.withColumn(
        "score",
        F.expr(f"round(CASE WHEN crashed = 1 THEN least({CRASH_MAX_SCORE}, {raw}) ELSE {raw} END, 1)"),
    ).select(
        "registration", "reg_key", "driver_name", "session_id", "records", "last_seen",
        "harsh_brake_rate", "harsh_accel_rate", "speeding_rate", "tailgating_rate",
        "avg_tyre_dev", "low_battery_rate",
        "p_brake", "p_accel", "p_speed", "p_gap", "p_tyre", "p_battery",
        "min_gap_m", "crashed", "score",
    )
