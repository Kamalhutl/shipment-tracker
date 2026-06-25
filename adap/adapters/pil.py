"""
adapters/pil.py
───────────────
PIL Shipping (Pacific International Lines) BL Tracker

API endpoint:
  https://www.pilship.com/wp-content/themes/hello-theme-child-master/
  pil-api/trackntrace-containertnt.php

Query params:
  module    = TrackTraceBL
  refNo     = <BL number>
  n         = <unix_sec>|<md5_hash>   ← dynamic token, see _generate_n()
  timestamp = <unix_ms>

The `n` token contains a SECRET KEY that PIL embeds in the tracking page JS.
Strategy:
  1. GET the PIL tracking page → extract secret key from JS
  2. Build n = "{unix_sec}|{md5(secret + unix_sec)}"
  3. Call the API with fresh cookies from step 1

If the secret key pattern changes, update _SECRET_PATTERNS below.
To debug: set PIL_DEBUG=1 env var to dump raw page HTML to /tmp/pil_page.html
"""

import os
import re
import time
import hashlib
import logging
from typing import Optional

import requests

from base import BaseAdapter
from schema import (
    UnifiedTracking,
    make_event,
    make_container,
    make_vessel,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# PIL Adapter
# ─────────────────────────────────────────────────────────────────────────────

class PILAdapter(BaseAdapter):

    CARRIER_NAME = "PIL"

    _API_URL = (
        "https://www.pilship.com/wp-content/themes/"
        "hello-theme-child-master/pil-api/trackntrace-containertnt.php"
    )
    _PAGE_URL = "https://www.pilship.com/digital-solutions/"

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest":  "empty",
        "Sec-Fetch-Mode":  "cors",
        "Sec-Fetch-Site":  "same-origin",
    }

    # ─── Regex patterns to extract secret key from PIL page JS ───────────────
    # PIL embeds a secret (or the full n-value) via wp_localize_script.
    # Add more patterns here if PIL changes their JS variable names.
    _SECRET_PATTERNS = [
        # Full pre-computed n string:  "1782198002|25be7f6a..."
        (r'"n"\s*:\s*"(\d{9,11}\|[a-f0-9]{32})"',            "full_n"),
        (r"'n'\s*:\s*'(\d{9,11}\|[a-f0-9]{32})'",            "full_n"),
        # Secret key used to compute n:
        (r'"secret"\s*:\s*"([a-f0-9A-F]{16,64})"',           "secret"),
        (r"'secret'\s*:\s*'([a-f0-9A-F]{16,64})'",           "secret"),
        (r'"key"\s*:\s*"([a-f0-9A-F]{16,64})"',              "secret"),
        # WordPress nonce / PIL-specific variable names:
        (r'pilapi_nonce["\'\s]*:\s*["\']([^"\']{8,64})["\']', "nonce"),
        (r'pil_nonce["\'\s]*:\s*["\']([^"\']{8,64})["\']',   "nonce"),
        (r'trackNonce["\'\s]*:\s*["\']([^"\']{8,64})["\']',  "nonce"),
        (r'"nonce"\s*:\s*"([^"]{8,64})"',                    "nonce"),
        (r"'nonce'\s*:\s*'([^']{8,64})'",                    "nonce"),
        # PIL sometimes uses a generic hash param
        (r'"hash"\s*:\s*"([a-f0-9]{32,64})"',                "secret"),
        (r'"token"\s*:\s*"([a-f0-9]{32,64})"',               "secret"),
    ]

    # =========================================================================
    # Required: _call_api
    # =========================================================================

    def _call_api(self, bl_no: str) -> dict:
        """
        1. Warm session (GET tracking page → cookies + secret key)
        2. Build n= token
        3. Call PIL JSON API
        """
        session = requests.Session()
        session.headers.update(self._HEADERS)

        secret, secret_type, pre_n = self._warm_session(session, bl_no)

        # ── Build n= and timestamp ────────────────────────────────────────
        if pre_n:
            # Page already gave us a fully-formed n value — use it directly
            n_param = pre_n
            logger.info(f"PIL: using pre-computed n from page JS")
        else:
            n_param = self._build_n(secret)
            logger.info(f"PIL: built n via {secret_type} + md5")

        ts_ms = str(int(time.time() * 1000))

        params = {
            "module":    "TrackTraceBL",
            "refNo":     bl_no,
            "n":         n_param,
            "timestamp": ts_ms,
        }

        referer = (
            f"{self._PAGE_URL}?tab=customer&id=track-trace"
            f"&label=containerTandT&module=TrackTraceBL&refNo={bl_no}"
        )
        session.headers["Referer"] = referer

        try:
            resp = session.get(self._API_URL, params=params, timeout=20)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            return {"error": f"PIL HTTP error: {str(e)}"}

        # ── Parse JSON ───────────────────────────────────────────────────
        try:
            data = resp.json()
        except ValueError:
            raw_text = resp.text[:500]
            logger.error(f"PIL non-JSON response: {raw_text}")
            return {"error": f"PIL returned non-JSON: {raw_text}"}

        # ── Check for API-level error ────────────────────────────────────
        if isinstance(data, dict):
            status = data.get("status") or data.get("result") or ""
            if str(status).lower() in ("error", "fail", "false", "0"):
                msg = data.get("message") or data.get("msg") or str(data)
                return {"error": f"PIL API error: {msg}"}

        return data

    # =========================================================================
    # Required: _normalize
    # =========================================================================

    def _normalize(self, raw: dict, bl_no: str) -> dict:
        """
        Map PIL raw JSON → UnifiedTracking schema.

        PIL API returns data under various top-level keys depending on
        firmware version. This handles both known shapes with fallbacks.

        ⚠ After first live run, check `raw` in the output and update
          field names below to match actual PIL response structure.
        """
        # ── Locate BL-level data block ────────────────────────────────────
        # PIL wraps results: {"status":"success","data":{...}} OR returns root
        bl_data = (
            raw.get("data")
            or raw.get("blDetails")
            or raw.get("bl")
            or raw
        )

        containers_raw = (
            raw.get("containers")
            or bl_data.get("containers")
            or bl_data.get("container")
            or []
        )

        # BL-level movement events (if not nested inside containers)
        bl_events_raw = (
            raw.get("movements")
            or raw.get("events")
            or bl_data.get("movements")
            or bl_data.get("events")
            or []
        )

        # ── BL-level scalar fields ────────────────────────────────────────
        pol = (
            self._safe_get(bl_data, "polName")
            or self._safe_get(bl_data, "portOfLoading")
            or self._safe_get(bl_data, "pol")
        )
        pod = (
            self._safe_get(bl_data, "podName")
            or self._safe_get(bl_data, "portOfDischarge")
            or self._safe_get(bl_data, "pod")
        )
        vessel = (
            self._safe_get(bl_data, "vesselName")
            or self._safe_get(bl_data, "vessel")
        )
        voyage = (
            self._safe_get(bl_data, "voyageNo")
            or self._safe_get(bl_data, "voyage")
            or self._safe_get(bl_data, "voyageNumber")
        )
        etd = self._fmt_date(
            self._safe_get(bl_data, "etd")
            or self._safe_get(bl_data, "departureDate")
            or self._safe_get(bl_data, "atd")
        )
        eta = self._fmt_date(
            self._safe_get(bl_data, "eta")
            or self._safe_get(bl_data, "arrivalDate")
            or self._safe_get(bl_data, "ata")
        )
        booking_no = (
            self._safe_get(bl_data, "bookingNo")
            or self._safe_get(bl_data, "bookingNumber")
            or self._safe_get(bl_data, "bkgNo")
        )

        # ── Build containers ──────────────────────────────────────────────
        containers = []
        for c in (containers_raw if isinstance(containers_raw, list) else [containers_raw]):
            if not c:
                continue

            cno = (
                self._safe_get(c, "containerNo")
                or self._safe_get(c, "ctnNo")
                or self._safe_get(c, "cntrNo")
                or self._safe_get(c, "containerNumber")
            )
            raw_type = (
                self._safe_get(c, "sizeType")
                or self._safe_get(c, "cntrType")
                or self._safe_get(c, "containerType")
                or ""
            )
            size  = raw_type[:2] if len(raw_type) >= 2 else raw_type
            ctype = raw_type[2:] if len(raw_type) > 2 else ""

            c_pol = self._safe_get(c, "polName") or self._safe_get(c, "pol") or pol
            c_pod = self._safe_get(c, "podName") or self._safe_get(c, "pod") or pod
            c_vsl = self._safe_get(c, "vesselName") or self._safe_get(c, "vessel") or vessel
            c_voy = self._safe_get(c, "voyageNo") or self._safe_get(c, "voyage") or voyage
            c_etd = self._fmt_date(self._safe_get(c, "etd") or self._safe_get(c, "departureDate")) or etd
            c_eta = self._fmt_date(self._safe_get(c, "eta") or self._safe_get(c, "arrivalDate")) or eta

            # Container-level events
            c_events_raw = (
                c.get("movements")
                or c.get("events")
                or c.get("trackingEvents")
                or []
            )
            c_events = [self._parse_event(ev) for ev in c_events_raw if ev]

            containers.append(make_container(
                container_no = cno,
                size         = size,
                type         = ctype,
                pol          = c_pol,
                pod          = c_pod,
                vessel       = c_vsl,
                voyage       = c_voy,
                etd          = c_etd,
                eta          = c_eta,
                events       = c_events,
            ))

        # ── BL-level events ───────────────────────────────────────────────
        bl_events = [
            self._parse_event(ev)
            for ev in (bl_events_raw if isinstance(bl_events_raw, list) else [])
            if ev
        ]

        return UnifiedTracking(
            bl_number  = bl_no,
            carrier    = self.CARRIER_NAME,
            booking_no = booking_no,
            pol        = pol,
            pod        = pod,
            vessel     = vessel,
            voyage     = voyage,
            etd        = etd,
            eta        = eta,
            containers = containers,
            vessels    = [],
            route      = [],
            events     = bl_events,
            raw        = raw,
        ).to_dict()

    # =========================================================================
    # Private helpers
    # =========================================================================

    def _warm_session(
        self, session: requests.Session, bl_no: str
    ) -> tuple[Optional[str], str, Optional[str]]:
        """
        GET the PIL tracking page to:
          (a) collect session cookies (especially OClmoOot)
          (b) extract the secret key / pre-built n from page JS

        Returns: (secret_or_none, type_label, pre_built_n_or_none)
        """
        page_url = (
            f"{self._PAGE_URL}"
            f"?tab=customer&id=track-trace"
            f"&label=containerTandT"
            f"&module=TrackTraceBL"
            f"&refNo={bl_no}"
        )
        try:
            resp = session.get(page_url, timeout=15)
            html = resp.text

            # Optional debug dump
            if os.getenv("PIL_DEBUG"):
                with open("/tmp/pil_page.html", "w") as f:
                    f.write(html)
                logger.debug("PIL: dumped page HTML to /tmp/pil_page.html")

            for pattern, kind in self._SECRET_PATTERNS:
                m = re.search(pattern, html)
                if m:
                    val = m.group(1)
                    if kind == "full_n":
                        logger.info(f"PIL: extracted pre-built n from page")
                        return None, "full_n", val
                    else:
                        logger.info(f"PIL: extracted secret ({kind}) from page")
                        return val, kind, None

            logger.warning(
                "PIL: could not extract secret from page. "
                "Check /tmp/pil_page.html (set PIL_DEBUG=1) and update "
                "_SECRET_PATTERNS in pil.py"
            )

        except requests.exceptions.RequestException as e:
            logger.warning(f"PIL: session warm failed: {e}")

        # No secret found — return empty string, _build_n will warn
        return None, "fallback", None

    def _build_n(self, secret: Optional[str]) -> str:
        """
        Build the n= token: "{unix_sec}|{md5(secret + unix_sec)}"

        If no secret is available, uses a placeholder hash that will likely
        fail server-side — update _SECRET_PATTERNS in that case.
        """
        ts_sec = int(time.time())

        if secret:
            raw_hash = hashlib.md5(f"{secret}{ts_sec}".encode()).hexdigest()
        else:
            # ⚠ Fallback — will fail unless PIL accepts unsigned requests
            # TODO: confirm hash formula from page JS then update this
            logger.warning(
                "PIL: no secret key found — n hash may be wrong. "
                "Run with PIL_DEBUG=1 to capture page HTML and inspect JS."
            )
            raw_hash = hashlib.md5(str(ts_sec).encode()).hexdigest()

        return f"{ts_sec}|{raw_hash}"

    def _parse_event(self, ev: dict) -> object:
        """Map a single PIL movement dict → TrackingEvent."""
        raw_date = (
            self._safe_get(ev, "eventDate")
            or self._safe_get(ev, "date")
            or self._safe_get(ev, "activityDate")
            or self._safe_get(ev, "timestamp")
        )
        status = (
            self._safe_get(ev, "eventDesc")
            or self._safe_get(ev, "description")
            or self._safe_get(ev, "activity")
            or self._safe_get(ev, "status")
        )
        location = (
            self._safe_get(ev, "location")
            or self._safe_get(ev, "portName")
            or self._safe_get(ev, "place")
        )
        return make_event(
            timestamp  = self._fmt_date(raw_date),
            status     = status,
            location   = location,
            country    = self._safe_get(ev, "country", default=None) or None,
            vessel     = self._safe_get(ev, "vesselName", default=None)
                         or self._safe_get(ev, "vessel", default=None) or None,
            voyage     = self._safe_get(ev, "voyageNo", default=None)
                         or self._safe_get(ev, "voyage", default=None) or None,
            event_type = self._safe_get(ev, "eventType", default=None) or None,
            raw_code   = self._safe_get(ev, "eventCode", default=None)
                         or self._safe_get(ev, "code", default=None) or None,
        )

    @staticmethod
    def _fmt_date(val) -> Optional[str]:
        """
        Normalize PIL date strings → ISO 8601.
        PIL uses formats like "2026-04-15 00:00:00" or "2026-04-15".
        """
        if not val:
            return None
        s = str(val).strip()
        if not s or s in ("-", "N/A", "null", "None", "0"):
            return None
        # "2026-04-15 14:30:00" → "2026-04-15T14:30:00"
        if " " in s and "T" not in s:
            return s.replace(" ", "T")
        return s
