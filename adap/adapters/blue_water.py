import requests
import json
from datetime import datetime
from typing import Optional

from adap.base import BaseAdapter
from adap.schema import make_event, make_container


class BlueWaterAdapter(BaseAdapter):
    """
    Adapter for Blue Water Lines carrier tracking.

    Confirmed via DevTools + curl on 2026-07-07. Three-step chain, all
    under one warmed ASP.NET session (no static cookie -- session is
    obtained fresh on every fetch() call, same as PIL's session warm-up):

      1. GET  /Login/BLCntrTracking?RefType=BL&RefID=<bl_no>&AL=0
         -> sets ASP.NET_SessionId cookie (page load, browser does this too)

      2. POST /api/LoginApi/GetTrackingDtls?RefType=BL&RefID=<bl_no>&BLNo=<bl_no>
         -> BL-level summary only, returns a ONE-ITEM LIST:
            [{"BLNumber":..., "Vessel":..., "Voyage":..., "DtDeparture":...,
              "POR": "<origin>", "FPD": "<destination>", ...}]
            NOTE: this does NOT include container-level movement events.

      3. POST /api/LoginApi/GetBLCntrDetails?BLID=<bl_no>
         -> list of containers on this BL (one snapshot row per container):
            [{"CntrNo": "BSIU9309868", "CntrType": "Dry - 40'HQ", ...}, ...]

      4. For EACH container number from step 3:
         POST /api/LoginApi/GetTrackingDtls?RefType=Container&RefID=<cntr_no>&BLNo=<bl_no>
         -> full movement-event list for that one container:
            [{"Dtmovement": "2026-04-08T00:00:00", "Location": "...",
              "StatusCode": "MA ", "Activity": "...", "VesselVoyage": "...", ...}]

    Only step 4's response contains real event history -- steps 2 and 3
    are BL-level summary and container discovery respectively.
    """

    CARRIER_NAME = "BLUE_WATER"

    PAGE_URL           = "https://bluewaterlines.net/Login/BLCntrTracking"
    TRACKING_DTLS_URL  = "https://bluewaterlines.net/api/LoginApi/GetTrackingDtls"
    BL_CNTR_DTLS_URL   = "https://bluewaterlines.net/api/LoginApi/GetBLCntrDetails"

    HEADERS = {
        "accept":             "application/json, text/javascript, */*; q=0.01",
        "accept-language":    "en-GB,en-US;q=0.9,en;q=0.8",
        "content-type":       "application/json; charset=utf-8",
        "origin":             "https://bluewaterlines.net",
        "sec-fetch-dest":     "empty",
        "sec-fetch-mode":     "cors",
        "sec-fetch-site":     "same-origin",
        "user-agent":         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "x-requested-with":   "XMLHttpRequest",
    }

    # -------------------------------------------------------------------------
    # Public entry point (overrides BaseAdapter.fetch -- multi-step chain
    # can't fit the single _call_api/_normalize pattern cleanly)
    # -------------------------------------------------------------------------

    def fetch(self, bl_no: str) -> dict:
        bl_no = bl_no.strip().upper()

        session = requests.Session()

        try:
            # ── Step 1: warm session cookie ───────────────────────────────
            page_url = f"{self.PAGE_URL}?RefType=BL&RefID={bl_no}&AL=0"
            session.get(page_url, headers={"user-agent": self.HEADERS["user-agent"]}, timeout=15)

            headers = dict(self.HEADERS)
            headers["referer"] = page_url

            # ── Step 2: BL-level summary ───────────────────────────────────
            bl_summary_raw = self._post_json(
                session, self.TRACKING_DTLS_URL,
                {"RefType": "BL", "RefID": bl_no, "BLNo": bl_no},
                headers,
            )
            if isinstance(bl_summary_raw, dict) and "error" in bl_summary_raw:
                return self._error_schema(bl_no, bl_summary_raw["error"])

            bl_summary = bl_summary_raw[0] if isinstance(bl_summary_raw, list) and bl_summary_raw else {}

            # ── Step 3: container list on this BL ──────────────────────────
            containers_raw = self._post_json(
                session, self.BL_CNTR_DTLS_URL,
                {"BLID": bl_no},
                headers,
            )
            if isinstance(containers_raw, dict) and "error" in containers_raw:
                return self._error_schema(bl_no, containers_raw["error"])
            if not isinstance(containers_raw, list):
                containers_raw = []

            container_numbers = sorted({c.get("CntrNo", "") for c in containers_raw if c.get("CntrNo")})

            if not container_numbers:
                return self._error_schema(bl_no, "Blue Water: no containers found for this BL.")

            # ── Step 4: full event history per container ───────────────────
            containers_out = []
            all_events     = []

            for cntr_no in container_numbers:
                events_raw = self._post_json(
                    session, self.TRACKING_DTLS_URL,
                    {"RefType": "Container", "RefID": cntr_no, "BLNo": bl_no},
                    headers,
                )
                if not isinstance(events_raw, list):
                    events_raw = []

                events = [self._parse_event(e) for e in events_raw]
                events.sort(key=lambda x: x.timestamp or "")
                all_events.extend(events)

                vessel, voyage = self._extract_vessel_voyage(events_raw)
                pol = self._extract_location(events_raw, ["ready to be loaded", "on board", "stuffing"])
                pod = self._extract_location(events_raw, ["discharged at terminal", "picked up by consignee", "delivery"])
                etd = self._extract_date(events_raw, "POLETD")
                eta = self._extract_date(events_raw, "PODETA")

                cntr_type = next((c.get("CntrType", "") for c in containers_raw if c.get("CntrNo") == cntr_no), "")

                containers_out.append(make_container(
                    container_no=cntr_no,
                    size=cntr_type,   # e.g. "Dry - 40'HQ" -- Blue Water doesn't give clean size/type codes
                    type="",
                    pol=pol,
                    pod=pod,
                    vessel=vessel,
                    voyage=voyage,
                    etd=etd,
                    eta=eta,
                    events=events,
                ))

            all_events.sort(key=lambda x: x.timestamp or "")

            return {
                "bl_number":  bl_no,
                "carrier":    self.CARRIER_NAME,
                "booking_no": bl_summary.get("BLNumber", bl_no),
                "pol":        bl_summary.get("POR", ""),
                "pod":        bl_summary.get("FPD", ""),
                "vessel":     bl_summary.get("Vessel", ""),
                "voyage":     bl_summary.get("Voyage", ""),
                "etd":        self._parse_ddmmyyyy(bl_summary.get("DtDeparture")),
                "eta":        None,   # not provided at BL level
                "containers": [self._container_to_dict(c) for c in containers_out],
                "vessels":    [],
                "route":      [],
                "events":     all_events,
                "raw":        {"bl_summary": bl_summary, "containers": containers_raw},
            }

        except Exception as e:
            return self._error_schema(bl_no, f"Blue Water request failed: {str(e)}")

    # -------------------------------------------------------------------------
    # Required abstract methods (unused directly since fetch() is overridden,
    # but BaseAdapter is an ABC and requires them to be implemented)
    # -------------------------------------------------------------------------

    def _call_api(self, bl_no: str) -> dict:
        raise NotImplementedError("BlueWaterAdapter uses a custom fetch() chain -- see fetch().")

    def _normalize(self, raw: dict, bl_no: str) -> dict:
        raise NotImplementedError("BlueWaterAdapter uses a custom fetch() chain -- see fetch().")

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _post_json(self, session: requests.Session, url: str, params: dict, headers: dict):
        """POST with empty body, return parsed JSON or {'error': ...}."""
        try:
            resp = session.post(url, headers=headers, params=params, timeout=30)

            if resp.status_code == 401:
                return {"error": "Blue Water unauthorized (401) -- session warm-up failed."}
            if resp.status_code == 302:
                return {"error": "Blue Water redirected to login -- session warm-up failed."}

            resp.raise_for_status()

            if not resp.text.strip():
                return []

            return resp.json()

        except requests.exceptions.Timeout:
            return {"error": "Blue Water API timeout"}
        except requests.exceptions.HTTPError as e:
            return {"error": f"Blue Water HTTP error: {e.response.status_code}"}
        except requests.exceptions.RequestException as e:
            return {"error": f"Blue Water request failed: {str(e)}"}
        except json.JSONDecodeError:
            return {"error": "Blue Water returned non-JSON response"}

    def _parse_event(self, item: dict):
        """Parse a single Blue Water movement event (from RefType=Container calls)."""
        vessel, voyage = self._split_vessel_voyage(item.get("VesselVoyage", ""))

        return make_event(
            timestamp=  self._parse_date(item.get("Dtmovement")),
            status=     item.get("Activity", ""),
            location=   item.get("Location", ""),
            country=    None,
            vessel=     vessel,
            voyage=     voyage,
            event_type= item.get("StatusCode", "").strip(),
            raw_code=   item.get("StatusCode", "").strip(),
        )

    def _split_vessel_voyage(self, raw: str) -> tuple:
        """
        Split "INTERASIA TRIBUTE/E014-MUNDRA, AICTPL..." into vessel + voyage.
        Format: "<VESSEL>/<VOYAGE>-<PORT>, <TERMINAL>"
        """
        if not raw:
            return "", ""
        parts = raw.split("/", 1)
        vessel = parts[0].strip()
        voyage = ""
        if len(parts) > 1:
            voyage = parts[1].split("-")[0].strip()
        return vessel, voyage

    def _extract_vessel_voyage(self, events_raw: list) -> tuple:
        for e in events_raw:
            vv = (e.get("VesselVoyage") or "").strip()
            if vv:
                return self._split_vessel_voyage(vv)
        return "", ""

    def _extract_date(self, events_raw: list, field: str) -> Optional[str]:
        for e in events_raw:
            val = (e.get(field) or "").strip()
            if val:
                return self._parse_date(val)
        return None

    def _extract_location(self, events_raw: list, keywords: list) -> str:
        for e in events_raw:
            activity = (e.get("Activity") or "").lower()
            if any(k in activity for k in keywords):
                return e.get("Location", "")
        return ""

    def _container_to_dict(self, c) -> dict:
        return {
            "container_no": c.container_no,
            "size":         c.size,
            "type":         c.type,
            "size_type":    c.size_type,
            "pol":          c.pol,
            "pod":          c.pod,
            "vessel":       c.vessel,
            "voyage":       c.voyage,
            "etd":          c.etd,
            "eta":          c.eta,
            "events": [
                {
                    "timestamp":  e.timestamp,
                    "status":     e.status,
                    "location":   e.location,
                    "country":    e.country,
                    "vessel":     e.vessel,
                    "voyage":     e.voyage,
                    "event_type": e.event_type,
                    "raw_code":   e.raw_code,
                }
                for e in c.events
            ],
        }

    def _parse_date(self, raw_date: Optional[str]) -> Optional[str]:
        """
        Normalize Blue Water date strings to ISO 8601.
        Handles:
          "2026-04-08T00:00:00"  -> "2026-04-08T00:00:00"
          "08/04/2026"           -> "2026-04-08"
        """
        if not raw_date:
            return None
        raw_date = raw_date.strip()
        if "T" in raw_date:
            try:
                return datetime.strptime(raw_date[:19], "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass
        return self._parse_ddmmyyyy(raw_date)

    def _parse_ddmmyyyy(self, raw_date: Optional[str]) -> Optional[str]:
        """Parse DD/MM/YYYY -> YYYY-MM-DD (used for POLETD/PODETA/DtDeparture)."""
        if not raw_date:
            return None
        raw_date = raw_date.strip()
        if not raw_date:
            return None
        try:
            return datetime.strptime(raw_date, "%d/%m/%Y").strftime("%Y-%m-%d")
        except ValueError:
            return raw_date


# -------------------------------------------------------------------------
# Quick test
# -------------------------------------------------------------------------

if __name__ == "__main__":
    adapter = BlueWaterAdapter()
    result = adapter.fetch("JKT2602003087")
    print(json.dumps(result, indent=2, default=str))