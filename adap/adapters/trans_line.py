import requests
import json
from datetime import datetime
from typing import Optional

from adap.base import BaseAdapter
from adap.schema import make_event, make_container, make_vessel, make_route_point


class TransLineAdapter(BaseAdapter):
    """
    Adapter for Trans Line carrier tracking.
    API: GET https://translinergroup.track.tigris.systems/api/bookings/{BL_NO}
    Params: include_emails=true
    Auth: Cookie-based (_ga, _ga_0VL1...)
    """

    CARRIER_NAME = "TRANS_LINE"

    BASE_URL = "https://translinergroup.track.tigris.systems/api/bookings"

    # -------------------------------------------------------------------------
    # Cookie — paste your full _ga cookie string here from DevTools/Postman
    # _ga cookies are analytics-based, longer lived than session cookies
    # -------------------------------------------------------------------------
    COOKIE = "_ga=GA1.1.1275679198.1781855145; _ga_0VL1..."  # <-- paste full cookie

    HEADERS = {
        "accept":             "*/*",
        "accept-language":    "en-GB,en-US;q=0.9,en;q=0.8",
        "priority":           "u=1, i",
        "referer":            "https://translinergroup.track.tigris.systems/",
        "sec-ch-ua":          '"Google Chrome";v="149", "Chromium";v="149", "Not.A/Brand";v="99"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest":     "empty",
        "sec-fetch-mode":     "cors",
        "sec-fetch-site":     "same-origin",
        "user-agent":         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "Cookie":             COOKIE,
    }

    # -------------------------------------------------------------------------
    # API call
    # -------------------------------------------------------------------------

    def _call_api(self, bl_no: str) -> dict:
        """
        GET /api/bookings/{BL_NO}?include_emails=true

        URL structure from screenshot:
        /api/bookings/TRLSINTUT6515200?include_emails=true&=TRLSINTUT6515200

        Note: second param has empty key — just the BL repeated.
        Trans Line uses BL as path param, not query param.
        """
        url = f"{self.BASE_URL}/{bl_no}"

        params = {
            "include_emails": "true",
            "": bl_no,          # empty key param as seen in Postman
        }

        try:
            response = requests.get(
                url,
                headers=self.HEADERS,
                params=params,
                timeout=30,
            )

            if response.status_code == 401:
                return {"error": "Trans Line cookie expired. Update COOKIE in trans_line.py"}

            if response.status_code == 404:
                return {"error": f"Trans Line: BL '{bl_no}' not found"}

            response.raise_for_status()

            if not response.text.strip():
                return {"error": "Trans Line returned empty response"}

            return response.json()

        except requests.exceptions.Timeout:
            return {"error": "Trans Line API timeout"}

        except requests.exceptions.HTTPError as e:
            return {"error": f"Trans Line HTTP error: {e.response.status_code}"}

        except requests.exceptions.RequestException as e:
            return {"error": f"Trans Line request failed: {str(e)}"}

        except json.JSONDecodeError:
            return {"error": "Trans Line returned non-JSON response"}

    # -------------------------------------------------------------------------
    # Normalizer
    # -------------------------------------------------------------------------

    def _normalize(self, raw: dict, bl_no: str) -> dict:
        """
        Map raw Trans Line response → unified schema.

        Raw Trans Line structure (from screenshots):
        {
            "booking_number": "TRLSINTUT6515200",
            "bill_of_lading": "TRLSINTUT6515200",
            "route": [
                {
                    "coordinates": {"latitude": 1.2904, "longitude": 103.852},
                    "port":        "SGSIN",
                    "port_name":   "SGSIN",
                    "country":     "SG",
                    "timestamp":   null
                },
                ...
            ],
            "vessels": [
                {
                    "name":    "WAN HAI 358",
                    "voyage":  "N030",
                    "imo":     null,
                    "status":  "Accept",
                    "etd":     "2026-05-13T00:00:00Z",
                    "atd":     "2026-05-12T12:39:00Z",
                    "eta":     "2026-05-20T00:00:00Z",
                    "ata":     "2026-05-23T00:00:00Z"
                }
            ],
            "alerts": [],
            "events": [                         ← scrolled view in image 3
                {
                    "type":                    "BOOKING_CONFIRMED",
                    "actual_departure_date":   null,
                    "estimated_departure_date":"2026-05-13T00:00:00Z",
                    "actual_arrival_date":     null,
                    "estimated_arrival_date":  "2026-05-20T00:00:00Z",
                    "event_date":              "2026-04-02T00:00:00Z",
                    "location": {
                        "coordinates": {"latitude": 1.2904, "longitude": 103.852},
                        "port":        "SGSIN",
                        "port_name":   "SGSIN",
                        "country":     "SG",
                        "timestamp":   null
                    },
                    "vessel": {
                        "name":   "WAN HAI 358",
                        "voyage": "N030"
                    }
                },
                ...
            ]
        }
        """
        # ── Vessels ──────────────────────────────────────────────────────────
        vessels_raw = raw.get("vessels", [])
        vessels     = [self._parse_vessel(v) for v in vessels_raw]

        # Primary vessel = first in list
        primary_vessel  = vessels[0].name   if vessels else ""
        primary_voyage  = vessels[0].voyage if vessels else ""
        primary_etd     = vessels[0].etd    if vessels else None
        primary_eta     = vessels[0].eta    if vessels else None

        # ── Route ─────────────────────────────────────────────────────────────
        route_raw = raw.get("route", [])
        route     = [self._parse_route_point(r) for r in route_raw]

        # POL = first route point, POD = last route point
        pol = route[0].port_name  if route else ""
        pod = route[-1].port_name if route else ""

        # ── Events ───────────────────────────────────────────────────────────
        events_raw = raw.get("events", [])
        events     = [self._parse_event(e) for e in events_raw]
        events.sort(key=lambda x: x.timestamp or "")

        return {
            "bl_number":  bl_no,
            "carrier":    self.CARRIER_NAME,
            "booking_no": raw.get("booking_number", bl_no),
            "pol":        pol,
            "pod":        pod,
            "vessel":     primary_vessel,
            "voyage":     primary_voyage,
            "etd":        primary_etd,
            "eta":        primary_eta,
            "containers": [],       # Trans Line doesn't return container list in this endpoint
            "vessels":    [self._vessel_to_dict(v) for v in vessels],
            "route":      [self._route_to_dict(r) for r in route],
            "events":     events,
            "raw":        raw,
        }

    # -------------------------------------------------------------------------
    # Sub-parsers
    # -------------------------------------------------------------------------

    def _parse_event(self, item: dict):
        """Parse a single Trans Line event."""
        location    = item.get("location", {})
        vessel_info = item.get("vessel", {})

        # Prefer actual dates over estimated
        timestamp = (
            item.get("actual_departure_date")
            or item.get("estimated_departure_date")
            or item.get("event_date")
        )

        return make_event(
            timestamp=  self._parse_date(timestamp),
            status=     item.get("type", ""),           # "BOOKING_CONFIRMED", "GATE_OUT_DEPOT"
            location=   location.get("port_name", "") or location.get("port", ""),
            country=    location.get("country", ""),
            vessel=     vessel_info.get("name", ""),
            voyage=     vessel_info.get("voyage", ""),
            event_type= "ACTUAL" if item.get("actual_departure_date") else "ESTIMATED",
            raw_code=   item.get("type", ""),
        )

    def _parse_vessel(self, item: dict):
        """Parse vessel entry."""
        return make_vessel(
            name=   item.get("name", ""),
            voyage= item.get("voyage", ""),
            imo=    item.get("imo"),
            status= item.get("status", ""),
            etd=    self._parse_date(item.get("etd")),
            atd=    self._parse_date(item.get("atd")),
            eta=    self._parse_date(item.get("eta")),
            ata=    self._parse_date(item.get("ata")),
        )

    def _parse_route_point(self, item: dict):
        """Parse route point."""
        coords = item.get("coordinates", {})
        return make_route_point(
            latitude=  coords.get("latitude", 0.0),
            longitude= coords.get("longitude", 0.0),
            port=      item.get("port", ""),
            port_name= item.get("port_name", ""),
            country=   item.get("country", ""),
            timestamp= self._parse_date(item.get("timestamp")),
        )

    # -------------------------------------------------------------------------
    # Dataclass → dict helpers (schema dataclasses aren't auto-serialized)
    # -------------------------------------------------------------------------

    def _vessel_to_dict(self, v) -> dict:
        return {
            "name":   v.name,
            "voyage": v.voyage,
            "imo":    v.imo,
            "status": v.status,
            "etd":    v.etd,
            "atd":    v.atd,
            "eta":    v.eta,
            "ata":    v.ata,
        }

    def _route_to_dict(self, r) -> dict:
        return {
            "latitude":  r.latitude,
            "longitude": r.longitude,
            "port":      r.port,
            "port_name": r.port_name,
            "country":   r.country,
            "timestamp": r.timestamp,
        }

    # -------------------------------------------------------------------------
    # Date parser
    # -------------------------------------------------------------------------

    def _parse_date(self, raw_date: Optional[str]) -> Optional[str]:
        """
        Normalize Trans Line date strings to ISO 8601.
        Input:  "2026-05-13T00:00:00Z"  or  "2026-05-12T12:39:00Z"
        Output: "2026-05-13T00:00:00"
        """
        if not raw_date:
            return None
        try:
            # Strip trailing Z and parse
            return datetime.strptime(raw_date[:19], "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%dT%H:%M:%S")
        except (ValueError, IndexError):
            return raw_date


# -------------------------------------------------------------------------
# Quick test
# -------------------------------------------------------------------------

if __name__ == "__main__":
    adapter = TransLineAdapter()

    # BL from your Postman screenshot
    result = adapter.fetch("TRLSINTUT6515200")

    print(json.dumps(result, indent=2, default=str))