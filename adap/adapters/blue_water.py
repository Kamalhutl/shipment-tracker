import requests
import json
from datetime import datetime
from typing import Optional

from base import BaseAdapter
from schema import make_event, make_container


class BlueWaterAdapter(BaseAdapter):
    """
    Adapter for Blue Water Lines carrier tracking.
    API: POST https://bluewaterlines.net/api/LoginApi/GetTrackingDtls
    Params: RefType, RefID (container_no), BLNo (bl_no) — passed as query params
    Auth: Cookie-based (ASP.NET_SessionId)
    """

    CARRIER_NAME = "BLUE_WATER"

    BASE_URL = "https://bluewaterlines.net/api/LoginApi/GetTrackingDtls"

    # -------------------------------------------------------------------------
    # Cookie — paste your full ASP.NET_SessionId cookie string here
    # Expires with session; grab fresh one from DevTools/Postman when needed
    # -------------------------------------------------------------------------
    COOKIE = "ASP.NET_SessionId=jx1mf0bl1v0jmwwwx02fd..."  # <-- paste full cookie

    HEADERS = {
        "accept":             "application/json, text/javascript, */*; q=0.01",
        "accept-language":    "en-GB,en-US;q=0.9,en;q=0.8",
        "content-length":     "0",
        "content-type":       "application/json; charset=utf-8",
        "origin":             "https://bluewaterlines.net",
        "priority":           "u=1, i",
        "referer":            "https://bluewaterlines.net/Login/BLCntrTracki...",
        "sec-ch-ua":          '"Google Chrome";v="149", "Chromium";v="149", "Not.A/Brand";v="99"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest":     "empty",
        "sec-fetch-mode":     "cors",
        "sec-fetch-site":     "same-origin",
        "user-agent":         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "x-requested-with":   "XMLHttpRequest",
        "Cookie":             COOKIE,
    }

    # -------------------------------------------------------------------------
    # Blue Water quirk:
    # Needs BOTH container_no (RefID) AND bl_no (BLNo) as query params
    # RefType is always "Container"
    # Body is empty (content-length: 0)
    # -------------------------------------------------------------------------

    def fetch(self, bl_no: str, container_no: str = "") -> dict:
        """
        Override fetch to accept optional container_no.
        Blue Water needs RefID (container_no) + BLNo (bl_no).
        If container_no not provided, makes a BL-only search with RefType=BL.
        """
        bl_no = bl_no.strip().upper()
        raw = self._call_api(bl_no, container_no)
        if "error" in raw:
            return self._error_schema(bl_no, raw["error"])
        try:
            return self._normalize(raw, bl_no, container_no)
        except Exception as e:
            return self._error_schema(bl_no, f"Normalization failed: {str(e)}")

    # -------------------------------------------------------------------------
    # API call
    # -------------------------------------------------------------------------

    def _call_api(self, bl_no: str, container_no: str = "") -> dict:
        """
        POST to Blue Water tracking endpoint.
        Params are query string, body is empty.

        URL structure from screenshot:
        /GetTrackingDtls?RefType=Container&RefID=BSIU9309868&BLNo=JKT2602003087
        """
        if container_no:
            params = {
                "RefType": "Container",
                "RefID":   container_no,
                "BLNo":    bl_no,
            }
        else:
            # BL-only search fallback
            params = {
                "RefType": "BL",
                "RefID":   bl_no,
                "BLNo":    bl_no,
            }

        try:
            response = requests.post(
                self.BASE_URL,
                headers=self.HEADERS,
                params=params,
                # body is intentionally empty
                timeout=30,
            )

            if response.status_code == 401:
                return {"error": "Blue Water session expired. Update COOKIE in blue_water.py"}

            if response.status_code == 302:
                return {"error": "Blue Water redirected to login — cookie expired"}

            response.raise_for_status()

            # Blue Water may return empty body for invalid BL
            if not response.text.strip():
                return {"error": "Blue Water returned empty response — check BL/container number"}

            return response.json()

        except requests.exceptions.Timeout:
            return {"error": "Blue Water API timeout"}

        except requests.exceptions.HTTPError as e:
            return {"error": f"Blue Water HTTP error: {e.response.status_code}"}

        except requests.exceptions.RequestException as e:
            return {"error": f"Blue Water request failed: {str(e)}"}

        except json.JSONDecodeError:
            return {"error": "Blue Water returned non-JSON response — likely session expired"}

    # -------------------------------------------------------------------------
    # Normalizer
    # -------------------------------------------------------------------------

    def _normalize(self, raw, bl_no: str, container_no: str = "") -> dict:
        """
        Map raw Blue Water response → unified schema.

        Raw Blue Water response (from screenshot):
        [
            {
                "Dtmovement":   "2026-04-08T00:00:00",
                "Location":     "ASHUTOSH CFS - EMPTY YARD",
                "StatusCode":   "MA",
                "Activity":     "EMPTY DISCHARGED IN EMPTY YARD/TERMINAL/CFS",
                "DtActivity":   "08/04/2026",
                "VesselVoyage": "INTERASIA TRIBUTE/E014-MUNDRA, AICTPL ...",
                "POLETD":       "",
                "PODETA":       ""
            },
            ...
        ]

        Note: response is a LIST not a dict.
        """
        # Handle both list and dict responses
        if isinstance(raw, dict):
            events_raw = raw.get("data", raw.get("Data", [raw]))
        elif isinstance(raw, list):
            events_raw = raw
        else:
            return self._error_schema(bl_no, "Unexpected Blue Water response format")

        if not events_raw:
            return self._error_schema(bl_no, "Blue Water returned no tracking events")

        events = [self._parse_event(e) for e in events_raw]

        # Sort ascending by timestamp (oldest first = chronological)
        # make_event returns a TrackingEvent dataclass — use attribute access not dict
        events.sort(key=lambda x: x.timestamp or "")

        # Extract vessel/voyage from first event that has VesselVoyage
        vessel, voyage = self._extract_vessel_voyage(events_raw)

        # Extract POL ETD / POD ETA from events
        etd  = self._extract_date(events_raw, "POLETD")
        eta  = self._extract_date(events_raw, "PODETA")
        pol  = self._extract_location(events_raw, ["stuffed", "gate in", "on board"])
        pod  = self._extract_location(events_raw, ["empty discharged", "delivery", "empty yard"])

        containers = []
        if container_no:
            containers.append(make_container(
                container_no=container_no,
                pol=pol,
                pod=pod,
                vessel=vessel,
                voyage=voyage,
                etd=etd,
                eta=eta,
                events=events,
            ))

        return {
            "bl_number":  bl_no,
            "carrier":    self.CARRIER_NAME,
            "booking_no": bl_no,
            "pol":        pol,
            "pod":        pod,
            "vessel":     vessel,
            "voyage":     voyage,
            "etd":        etd,
            "eta":        eta,
            "containers": containers,
            "vessels":    [],
            "route":      [],
            "events":     events,
            "raw":        raw,
        }

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _parse_event(self, item: dict) -> dict:
        """Parse a single Blue Water movement event."""

        # VesselVoyage format: "INTERASIA TRIBUTE/E014-MUNDRA, AICTPL..."
        vessel, voyage = self._split_vessel_voyage(item.get("VesselVoyage", ""))

        return make_event(
            timestamp=  self._parse_date(item.get("Dtmovement")),
            status=     item.get("Activity", ""),
            location=   item.get("Location", ""),
            country=    None,   # Blue Water doesn't return country code
            vessel=     vessel,
            voyage=     voyage,
            event_type= item.get("StatusCode", ""),  # "MA", "FU", "FV", "FB"
            raw_code=   item.get("StatusCode", ""),
        )

    def _split_vessel_voyage(self, raw: str) -> tuple[str, str]:
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
            # voyage is before the dash+port: "E014-MUNDRA" → "E014"
            voyage = parts[1].split("-")[0].strip()
        return vessel, voyage

    def _extract_vessel_voyage(self, events_raw: list) -> tuple[str, str]:
        """Get vessel/voyage from first event that has VesselVoyage set."""
        for e in events_raw:
            vv = e.get("VesselVoyage", "").strip()
            if vv:
                return self._split_vessel_voyage(vv)
        return "", ""

    def _extract_date(self, events_raw: list, field: str) -> Optional[str]:
        """Extract first non-empty date from a field across all events."""
        for e in events_raw:
            val = e.get(field, "").strip()
            if val:
                return self._parse_date(val)
        return None

    def _extract_location(self, events_raw: list, keywords: list) -> str:
        """Find location from first event whose Activity matches any keyword."""
        for e in events_raw:
            activity = e.get("Activity", "").lower()
            if any(k in activity for k in keywords):
                return e.get("Location", "")
        return ""

    def _parse_date(self, raw_date: Optional[str]) -> Optional[str]:
        """
        Normalize Blue Water date strings to ISO 8601.
        Handles:
          "2026-04-08T00:00:00"  → "2026-04-08T00:00:00"
          "08/04/2026"           → "2026-04-08"
        """
        if not raw_date:
            return None
        raw_date = raw_date.strip()
        # Already ISO
        if "T" in raw_date:
            try:
                return datetime.strptime(raw_date[:19], "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass
        # DD/MM/YYYY
        try:
            return datetime.strptime(raw_date, "%d/%m/%Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
        # MM/DD/YYYY fallback
        try:
            return datetime.strptime(raw_date, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            return raw_date


# -------------------------------------------------------------------------
# Quick test
# -------------------------------------------------------------------------

if __name__ == "__main__":
    adapter = BlueWaterAdapter()

    # From your Postman screenshot
    result = adapter.fetch("JKT2602003087", container_no="BSIU9309868")

    print(json.dumps(result, indent=2, default=str))
