from pyspark import pipelines as dp
from pyspark.sql import functions as F


@dp.materialized_view(
    name="pmc_sandbox.carsim.driver_latest_session_score",
    comment="The single most recent session (by last_seen) across all registrations and drivers, with its safety score.",
)
def driver_latest_session_score():
    return (
        spark.read.table("pmc_sandbox.carsim.driver_safety_score")
        .filter(F.col("session_id").isNotNull())
        .orderBy(F.col("last_seen").desc(), F.col("session_id").desc())
        .limit(1)
        .select(
            "registration", "reg_key", "driver_name", "session_id",
            "last_seen", "records", "crashed", "score",
        )
    )
