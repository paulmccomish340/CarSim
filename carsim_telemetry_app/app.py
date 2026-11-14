import os
import logging
import threading
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from databricks import sql
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from datetime import timedelta

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("car-telemetry-live")

TABLE = os.getenv("TELEMETRY_TABLE", "pmc_sandbox.carsim.car_telemetry")
WAREHOUSE_ID = os.getenv("DATABRICKS_WAREHOUSE_ID")
WINDOW_MINUTES = int(os.getenv("WINDOW_MINUTES", "5"))

# Driver scores are computed by the carsim_driver_safety_score pipeline (one row per
# reg_key + driver_name); the app only reads them.
SCORE_TABLE = os.getenv("SCORE_TABLE", "pmc_sandbox.carsim.driver_safety_score")
# Single-row table holding the latest session overall; the telemetry charts only show
# rows for that session_id.
LATEST_SESSION_TABLE = os.getenv("LATEST_SESSION_TABLE", "pmc_sandbox.carsim.driver_latest_session_score")
# Genie agent "Autonomous Driving Agent" writes driving guidance for the latest session.
GENIE_SPACE_ID = os.getenv("GENIE_SPACE_ID", "")
GUIDANCE_PROMPT = os.getenv("GUIDANCE_PROMPT", (
    "Give guidance on how to improve driving for the latest session. "
    "The output should be a single paragraph with max 4 sentences. "
    "There is no need to mention the details about the run such as the driver."
))
# Used only for the UI's "capped after crash" note; the cap itself is applied in the pipeline.
CRASH_MAX_SCORE = float(os.getenv("CRASH_MAX_SCORE", "10"))

AUTONOMOUS = "__AUTONOMOUS__"   # dropdown key for rows with a NULL registration

cfg = Config()
app = FastAPI(title="Car Telemetry Live")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

REG_KEY = f"coalesce(vehicle_reg, '{AUTONOMOUS}')"
BASE_FILTER = "vehicle_id <> 'smoke-test'"
# Rows ingested before the payload change only carry speed_kph.
SPEED_MPH = "coalesce(speed_mph, speed_kph / 1.609344)"

REGISTRATIONS_QUERY = f"""
SELECT {REG_KEY} AS reg_key,
       max_by(driver_name, to_timestamp(event_time)) AS driver_name,
       max(to_timestamp(event_time)) AS last_seen
FROM {TABLE}
WHERE {BASE_FILTER}
GROUP BY 1
ORDER BY last_seen DESC
"""

# The latest session, as pre-filtered by the single row in LATEST_SESSION_TABLE.
LATEST_SESSION_QUERY = f"""
SELECT session_id, reg_key, driver_name, last_seen FROM {LATEST_SESSION_TABLE}
ORDER BY last_seen DESC
LIMIT 1
"""

# Charts: telemetry for the latest session only, aggregated per second.
TELEMETRY_QUERY = f"""
WITH s AS (
  SELECT session_id FROM {LATEST_SESSION_TABLE} ORDER BY last_seen DESC LIMIT 1
),
f AS (
  SELECT t.* FROM {TABLE} t JOIN s ON t.session_id = s.session_id
  WHERE {BASE_FILTER}
)
SELECT
  date_trunc('SECOND', to_timestamp(event_time)) AS t,
  avg({SPEED_MPH}) AS speed_mph,
  avg(distance_to_vehicle_ahead_m) AS distance_to_vehicle_ahead_m,
  avg(braking_force_kn) AS braking_force_kn,
  avg(acceleration_force_kn) AS acceleration_force_kn,
  avg(range_km) AS range_km,
  avg(battery_soc_pct) AS battery_soc_pct,
  avg(tyre_pressure_fl_bar) AS tyre_pressure_fl_bar,
  avg(tyre_pressure_fr_bar) AS tyre_pressure_fr_bar,
  avg(tyre_pressure_rl_bar) AS tyre_pressure_rl_bar,
  avg(tyre_pressure_rr_bar) AS tyre_pressure_rr_bar
FROM f
GROUP BY 1
ORDER BY 1
"""


# The score table is keyed by reg_key + driver_name. For the selected registration,
# show the most recently active driver's row.
SCORE_QUERY = f"""
SELECT * FROM {SCORE_TABLE}
WHERE reg_key = :reg
ORDER BY last_seen DESC
LIMIT 1
"""

LEADERBOARD_QUERY = f"""
SELECT * FROM {SCORE_TABLE}
ORDER BY score DESC, records DESC
"""

_conn = None
_lock = threading.Lock()   # the SQL connection is shared across request threads


def get_connection():
    global _conn
    if _conn is None:
        if not WAREHOUSE_ID:
            raise RuntimeError("DATABRICKS_WAREHOUSE_ID is not set; add a SQL warehouse resource to the app.")
        _conn = sql.connect(
            server_hostname=cfg.host,
            http_path=f"/sql/1.0/warehouses/{WAREHOUSE_ID}",
            credentials_provider=lambda: cfg.authenticate,
        )
    return _conn


def _clean(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def run_query(query: str, params: dict | None = None):
    global _conn
    for attempt in range(2):
        try:
            with _lock, get_connection().cursor() as cur:
                cur.execute(query, params or {})
                cols = [c[0] for c in cur.description]
                return [{k: _clean(v) for k, v in zip(cols, r)} for r in cur.fetchall()]
        except Exception:
            log.exception("Query failed (attempt %s)", attempt + 1)
            try:
                if _conn is not None:
                    _conn.close()
            except Exception:
                pass
            _conn = None
            if attempt == 1:
                raise


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


def _label(reg_key):
    return "Autonomous" if reg_key == AUTONOMOUS else reg_key


@app.get("/api/registrations")
def registrations():
    try:
        rows = run_query(REGISTRATIONS_QUERY)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    regs = [{"value": r["reg_key"], "label": _label(r["reg_key"]),
             "driver_name": r["driver_name"], "last_seen": r["last_seen"]} for r in rows]
    if not any(r["value"] == AUTONOMOUS for r in regs):
        regs.append({"value": AUTONOMOUS, "label": "Autonomous", "driver_name": None, "last_seen": None})
    return {"registrations": regs}


@app.get("/api/telemetry")
def telemetry(reg: str = AUTONOMOUS):
    # reg is accepted for compatibility but ignored: charts follow the latest session.
    try:
        session = run_query(LATEST_SESSION_QUERY)
        rows = run_query(TELEMETRY_QUERY)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    s = session[0] if session else None
    if s:
        s["label"] = _label(s["reg_key"])
    return {"session": s, "rows": rows, "latest": rows[-1] if rows else None}


@app.get("/api/score")
def score(reg: str = AUTONOMOUS):
    try:
        rows = run_query(SCORE_QUERY, {"reg": reg})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not rows:
        return {"reg": reg, "label": _label(reg), "score": None}
    r = rows[0]
    r["label"] = _label(r["reg_key"])
    r["crash_max_score"] = CRASH_MAX_SCORE
    return r


@app.get("/api/leaderboard")
def leaderboard():
    try:
        rows = run_query(LEADERBOARD_QUERY)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"rows": [{"rank": i + 1, "reg_key": r["reg_key"], "label": _label(r["reg_key"]),
                      "driver_name": r["driver_name"], "score": r["score"], "crashed": bool(r["crashed"]),
                      "records": r["records"], "last_seen": r["last_seen"]}
                     for i, r in enumerate(rows)]}


# Guidance is generated once per session_id in a background thread (Genie takes ~20 s),
# then served from this cache until a new session becomes the latest.
_guidance = {}            # session_id -> {"status": pending|ready|error, "text": str}
_guidance_lock = threading.Lock()
_ws = None


def _genie_text(message):
    texts = [a.text.content for a in (message.attachments or []) if a.text and a.text.content]
    return texts[-1] if texts else None


def _generate_guidance(session_id):
    global _ws
    try:
        if _ws is None:
            _ws = WorkspaceClient()
        msg = _ws.genie.start_conversation_and_wait(GENIE_SPACE_ID, GUIDANCE_PROMPT,
                                                    timeout=timedelta(minutes=5))
        text = _genie_text(msg)
        result = {"status": "ready", "text": text} if text else \
                 {"status": "error", "text": f"Genie returned no text (status {msg.status})."}
    except Exception as e:
        log.exception("Genie guidance failed for session %s", session_id)
        result = {"status": "error", "text": str(e)}
    with _guidance_lock:
        _guidance[session_id] = result


@app.get("/api/guidance")
def guidance(retry: bool = False):
    try:
        session = run_query(LATEST_SESSION_QUERY)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not session:
        return {"session_id": None, "status": "empty", "text": None}
    sid = session[0]["session_id"]
    with _guidance_lock:
        entry = _guidance.get(sid)
        if entry is None or (retry and entry["status"] == "error"):
            entry = _guidance[sid] = {"status": "pending", "text": None}
            threading.Thread(target=_generate_guidance, args=(sid,), daemon=True).start()
    return {"session_id": sid, **entry}


@app.get("/health")
def health():
    return {"status": "ok"}
