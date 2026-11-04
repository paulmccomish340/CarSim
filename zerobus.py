"""
Zerobus telemetry forwarder.

Telemetry records are queued in memory and flushed in batches to the Zerobus
ingest endpoint configured through environment variables. If the configuration
is incomplete the app still runs and telemetry stays local (visible in the UI).

The wire format lives in `_send` so it can be aligned with the exact Zerobus
endpoint and schema for the target table. Replace `_send` with the official
Databricks Zerobus SDK call if you prefer the SDK.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import urlparse

import httpx

log = logging.getLogger("zerobus")

MAX_QUEUE = 50_000


class ZerobusClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = cfg.zerobus_enabled
        self._queue: list[dict] = []
        self._http: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._token_expiry = 0.0
        self.sent = 0
        self.failed = 0
        self.dropped = 0
        self.last_error: str | None = None
        self.last_flush_utc: str | None = None

    # ------------------------------------------------------------ public
    def enqueue(self, record: dict) -> None:
        if not self.enabled:
            return
        if len(self._queue) >= MAX_QUEUE:
            del self._queue[:1000]
            self.dropped += 1000
        self._queue.append(record)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "sent": self.sent,
            "failed": self.failed,
            "dropped": self.dropped,
            "queued": len(self._queue),
            "last_error": self.last_error,
            "last_flush_utc": self.last_flush_utc,
        }

    async def run(self) -> None:
        if not self.enabled:
            log.warning("Zerobus is not configured; telemetry is kept local only.")
        self._http = httpx.AsyncClient(timeout=15.0)
        try:
            while True:
                await asyncio.sleep(self.cfg.zerobus_flush_seconds)
                if self.enabled:
                    await self.flush()
        finally:
            await self._http.aclose()

    async def flush(self) -> None:
        batch_size = self.cfg.zerobus_batch_size
        while self._queue:
            batch = self._queue[:batch_size]
            try:
                await self._send(batch)
            except Exception as exc:  # keep the sim running even if ingest is down
                self.failed += len(batch)
                self.last_error = str(exc)[:300]
                log.warning("Zerobus flush failed: %s", self.last_error)
                return
            del self._queue[:len(batch)]
            self.sent += len(batch)
            self.last_flush_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.last_error = None

    # ----------------------------------------------------------- internals
    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        url = f"{self.cfg.zerobus_workspace_url.rstrip('/')}/oidc/v1/token"
        catalog, schema, _ = self.cfg.zerobus_table.split(".")
        # Zerobus needs a token scoped to the target table with the zerobuswrite operation.
        authorization_details = [
            {"type": "unity_catalog_privileges", "privileges": ["USE CATALOG"],
             "object_type": "CATALOG", "object_full_path": catalog},
            {"type": "unity_catalog_privileges", "privileges": ["USE SCHEMA"],
             "object_type": "SCHEMA", "object_full_path": f"{catalog}.{schema}"},
            {"type": "unity_catalog_privileges", "privileges": ["SELECT", "MODIFY"],
             "object_type": "TABLE", "object_full_path": self.cfg.zerobus_table,
             "operations": ["zerobuswrite"]},
        ]
        resp = await self._http.post(
            url,
            data={
                "grant_type": "client_credentials",
                "scope": "all-apis",
                "resource": f"api://databricks/workspaces/{self._workspace_id()}/zerobusDirectWriteApi",
                "authorization_details": json.dumps(authorization_details),
            },
            auth=(self.cfg.zerobus_client_id, self.cfg.zerobus_client_secret),
        )
        if resp.status_code == 401:
            self._token = None  # force a refresh on the next attempt
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + float(body.get("expires_in", 3600))
        return self._token

    def _workspace_id(self) -> str:
        # Zerobus endpoint host is <workspace-id>.zerobus.<region>.azuredatabricks.net
        return urlparse(self.cfg.zerobus_ingest_url).hostname.split(".")[0]

    def _insert_url(self) -> str:
        base = self.cfg.zerobus_ingest_url.rstrip("/")
        if "/zerobus/v1/" in base:
            return base
        return f"{base}/zerobus/v1/tables/{self.cfg.zerobus_table}/insert"

    async def _send(self, batch: list[dict]) -> None:
        token = await self._access_token()
        # REST body is a JSON list of records matching the table columns.
        resp = await self._http.post(
            self._insert_url(),
            json=batch,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            self._token = None  # force a refresh on the next attempt
        resp.raise_for_status()
