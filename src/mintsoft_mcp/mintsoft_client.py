"""Shared Mintsoft HTTP client.

Reused by the MCP server and the smoke tester. READ + WRITE capable, but callers
decide which. Key comes from the MINTSOFT_API_KEY env var; optionally falls back to
GCP Secret Manager via gcloud when MINTSOFT_SECRET_PROJECT is set. Never logged.

Cloudflare quirk: default user-agents get a 1010 block, so we send a browser UA.
"""
from __future__ import annotations

import os
import re
import time
import json as _jsonlib
import subprocess
from datetime import datetime
from dataclasses import dataclass

import requests

BASE = "https://api.mintsoft.co.uk/api"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

SECRET_PROJECT = os.environ.get("MINTSOFT_SECRET_PROJECT", "")
SECRET_NAME = os.environ.get("MINTSOFT_SECRET_NAME", "mintsoft-api-key")


def write_allowlist() -> set:
    """ClientIds writes are permitted against (env MINTSOFT_WRITE_CLIENT_ALLOWLIST,
    comma-separated). Default EMPTY: every write is refused until you deliberately
    allowlist the client(s) you intend to change — Mintsoft has no sandbox."""
    return {int(x) for x in os.environ.get("MINTSOFT_WRITE_CLIENT_ALLOWLIST", "").split(",") if x.strip()}

# --- Audit log: append-only history of EVERY API call (for fallback/recovery) ---
# Logs every call (reads + writes) by default. The API key lives in session headers
# and is NEVER written to the log.
#   MINTSOFT_AUDIT=0            -> disable logging entirely
#   MINTSOFT_AUDIT_WRITES_ONLY=1 -> log only PUT/POST/DELETE (skip GET reads)
#   MINTSOFT_AUDIT_LOG=<path>   -> override the log file location
AUDIT_PATH = os.environ.get("MINTSOFT_AUDIT_LOG",
                            os.path.join(os.path.expanduser("~"), ".mintsoft-mcp",
                                         "mintsoft_api_audit.jsonl"))
AUDIT_ENABLED = os.environ.get("MINTSOFT_AUDIT", "1").lower() not in ("0", "false", "off", "no")
AUDIT_WRITES_ONLY = os.environ.get("MINTSOFT_AUDIT_WRITES_ONLY", "0").lower() in ("1", "true", "yes", "on")
_WRITE_METHODS = {"PUT", "POST", "DELETE"}


def _normalize_product_body(path: str, body):
    """Fix Mintsoft field-shape quirks on Product create/update bodies.

    `Product.CommodityCode` is a nested object {"Code": "470710"}, NOT a string —
    a plain string is silently dropped on PUT/POST /Product. Callers naturally pass
    a string, so coerce it here (the single chokepoint) so every script + the MCP
    tools get it right. Idempotent: leaves an already-correct dict untouched.
    """
    if not isinstance(body, dict):
        return body
    if path.strip("/").lower() != "product":
        return body
    cc = body.get("CommodityCode")
    if isinstance(cc, str) and cc.strip():
        body = dict(body)
        body["CommodityCode"] = {"Code": cc.strip()}
    return body


def get_api_key() -> str:
    """MINTSOFT_API_KEY env first; `gcloud secrets versions access` only if
    MINTSOFT_SECRET_PROJECT is configured."""
    key = os.environ.get("MINTSOFT_API_KEY")
    if key:
        return key.strip()
    if not SECRET_PROJECT:
        raise RuntimeError(
            "No API key: set the MINTSOFT_API_KEY env var (or MINTSOFT_SECRET_PROJECT "
            "to fetch it from GCP Secret Manager)."
        )
    try:
        out = subprocess.run(
            ["gcloud", "secrets", "versions", "access", "latest",
             f"--secret={SECRET_NAME}", f"--project={SECRET_PROJECT}"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(
            f"No MINTSOFT_API_KEY env and could not read secret {SECRET_NAME}: {e}"
        ) from e


@dataclass
class Result:
    method: str
    path: str
    status: int | None
    ms: int
    ok: bool
    # `data` on success, error string on failure. Kept small for reporting.
    summary: str
    data: object = None


class MintsoftClient:
    def __init__(self, key: str | None = None, sleep: float = 0.2):
        self.key = key or get_api_key()
        self.sleep = sleep
        self.s = requests.Session()
        self.s.headers.update({
            "ms-apikey": self.key,
            "User-Agent": _UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def request(self, method: str, path: str, params=None, json=None) -> Result:
        url = f"{BASE}/{path.lstrip('/')}"
        json = _normalize_product_body(path, json)
        t0 = time.time()
        try:
            r = self.s.request(method, url, params=params, json=json, timeout=60)
            ms = int((time.time() - t0) * 1000)
            ok = r.status_code < 400
            data = None
            summary = ""
            if ok:
                try:
                    data = r.json()
                    if isinstance(data, list):
                        summary = f"list[{len(data)}]"
                    elif isinstance(data, dict):
                        # surface a couple of useful keys without dumping PII
                        keys = list(data.keys())[:6]
                        summary = "obj{" + ",".join(keys) + "}"
                    else:
                        summary = type(data).__name__
                except ValueError:
                    summary = f"{len(r.content)}B non-json"
            else:
                summary = (r.text or "")[:160].replace("\n", " ")
            time.sleep(self.sleep)
            res = Result(method, path, r.status_code, ms, ok, summary, data)
            self._audit(method, path, params, json, res)
            return res
        except requests.RequestException as e:
            ms = int((time.time() - t0) * 1000)
            res = Result(method, path, None, ms, False, f"EXC {type(e).__name__}: {e}")
            self._audit(method, path, params, json, res)
            return res

    @staticmethod
    def _affected_id(path: str, body, res: "Result"):
        # 1) response id (new id on creates)  2) numeric path segment (detail/delete)  3) request-body id (updates)
        if isinstance(res.data, dict):
            for k in ("ProductId", "OrderId", "ID", "Id"):
                if res.data.get(k):
                    return res.data[k]
        m = re.search(r"/(\d+)(?:/|$)", "/" + path)   # e.g. DELETE Product/63675
        if m:
            return int(m.group(1))
        if isinstance(body, dict):
            for k in ("ID", "ProductId", "Id"):
                if body.get(k):
                    return body[k]
        return None

    def _audit(self, method, path, params, body, res):
        """Append one JSONL line for every call (reads + writes). Never raises."""
        if not AUDIT_ENABLED:
            return
        if AUDIT_WRITES_ONLY and method.upper() not in _WRITE_METHODS:
            return
        try:
            d = res.data if isinstance(res.data, dict) else None
            rec = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "method": method.upper(),
                "write": method.upper() in _WRITE_METHODS,
                "path": path,
                "params": params,
                "body": body,
                "status": res.status,
                "ok": res.ok,
                "success": (d.get("Success") if d else None),
                "message": (d.get("Message") if d else None),
                "affected_id": self._affected_id(path, body, res),
                "summary": res.summary,
            }
            os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
            with open(AUDIT_PATH, "a", encoding="utf-8") as f:
                f.write(_jsonlib.dumps(rec, default=str) + "\n")
        except Exception:
            pass   # auditing must never break an API call

    # convenience
    def get(self, path, params=None) -> Result:
        return self.request("GET", path, params=params)

    def paged(self, path, params=None, limit=100, max_pages=1) -> list:
        out = []
        for page in range(1, max_pages + 1):
            p = dict(params or {})
            p.update({"PageNo": page, "Limit": limit})
            res = self.request("GET", path, params=p)
            if not res.ok or not isinstance(res.data, list):
                break
            out.extend(res.data)
            if len(res.data) < limit:
                break
        return out
