import requests
import json
from datetime import datetime
from typing import Optional

from base import BaseAdapter
from schema import (
    UnifiedTracking, Container, TrackingEvent,
    make_event, make_container
)


class OneLineAdapter(BaseAdapter):
    """
    Adapter for ONE LINE carrier tracking.
    API: GET https://ecomm.one-line.com/api/v2/edh/containers/track-and-trace/cop-events
    Params: booking_no, container_no
    Auth: Cookie-based (sessLocale, loginType, isPin)
    """

    CARRIER_NAME = "ONE_LINE"

    BASE_URL = "https://ecomm.one-line.com/api/v2/edh/containers/track-and-trace/cop-events"

    # -------------------------------------------------------------------------
    # Cookie — paste your full cookie string here from DevTools/Postman
    # It expires; you'll need to refresh it periodically
    # -------------------------------------------------------------------------
    COOKIE = "sessLocale=en; loginType=okta; isPin=false;"  # <-- paste full cookie here

    HEADERS = {
        "accept":               "application/json, text/plain, */*",
        "accept-language":      "en-GB,en-US;q=0.9,en;q=0.8",
        "cache-control":        "no-cache, no-store, must-revalidate",
        "priority":             "u=1, i",
        "referer":              "https://ecomm.one-line.com/",
        "sec-ch-ua":            '"Google Chrome";v="149", "Chromium";v="149", "Not.A/Brand";v="99"',
        "sec-ch-ua-mobile":     "?0",
        "sec-ch-ua-platform":   '"macOS"',
        "sec-fetch-dest":       "empty",
        "sec-fetch-mode":       "cors",
        "sec-fetch-site":       "same-origin",
        "user-agent":           "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "Cookie":               COOKIE,
    }

    # -------------------------------------------------------------------------
    # ONE LINE quirk: needs container_no alongside booking_no
    # If you only have BL/booking_no, pass container_no="" and it still works
    # but returns less granular event data
    # -------------------------------------------------------------------------

    def fetch(self, bl_no: str, container_no: str = "") -> dict:
        """
        Override fetch to accept optional container_no.
        ONE LINE uses booking_no + container_no as query params.
        """
        bl_no = bl_no.strip().upper()
        raw = self._call_api(bl_no, container_no)
        if "error" in raw:
            return self._error_schema(bl_no, raw["error"])
        try:
            return self._normalize(raw, bl_no)
        except Exception as e:
            return self._error_schema(bl_no, f"Normalization failed: {str(e)}")

    # -------------------------------------------------------------------------
    # API call
    # -------------------------------------------------------------------------

    def _call_api(self, bl_no: str, container_no: str = "") -> dict:
        """
        GET cop-events with booking_no + container_no as query params.

        URL structure from screenshot:
        /cop-events?booking_no=CPTG13635800&container_no=FFAU2906595
        """

        # In one_line.py _call_api()
# ONE LINE booking_no doesn't include carrier prefix
        booking_no = bl_no.lstrip("ONEY") if bl_no.startswith("ONEY") else bl_no
        params = {"booking_no": booking_no, "container_no": container_no}
        # Remove container_no param if empty (cleaner request)
        if not container_no:
            params.pop("container_no")

        try:
            response = requests.get(
                self.BASE_URL,
                headers=self.HEADERS,
                params=params,
                timeout=30,
            )

            # 401 = cookie expired
            if response.status_code == 401:
                return {"error": "ONE LINE cookie expired. Update COOKIE in one_line.py"}

            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            return {"error": "ONE LINE API timeout", "carrier": self.CARRIER_NAME, "bl_number": bl_no}

        except requests.exceptions.HTTPError as e:
            return {"error": f"ONE LINE HTTP error: {e.response.status_code}", "carrier": self.CARRIER_NAME, "bl_number": bl_no}

        except requests.exceptions.RequestException as e:
            return {"error": f"ONE LINE request failed: {str(e)}", "carrier": self.CARRIER_NAME, "bl_number": bl_no}

        except json.JSONDecodeError:
            return {"error": "ONE LINE returned non-JSON response", "carrier": self.CARRIER_NAME, "bl_number": bl_no}

    # -------------------------------------------------------------------------
    # Normalizer
    # -------------------------------------------------------------------------

    def _normalize(self, raw: dict, bl_no: str) -> dict:
        """
        Map raw ONE LINE response → unified schema.

        Raw ONE LINE structure (from screenshot):
        {
            "e": "Success",
            "a": [                          ← list of events
                {
                    "eventName":            "Empty Container Release to Shipper",
                    "eventLocalPortDate":   "2026-04-24T15:45:00.000Z",
                    "eventDate":            "2026-04-24T13:45:00.000Z",
                    "triggerType":          "ACTUAL",
                    "matrixId":             "E012",
                    "copSequence":          1011,
                    "opusCode":             "MOTYDO",
                    "nodeCode":             "ZADUR19",
                    "location": {
                        "code":             "ZADUR",
                        "locationName":     "DURBAN",
                        "countryName":      "SOUTH AFRICA"
                    }
                },
                ...
            ]
        }
        """

        print("RAW RESPONSE:", json.dumps(raw, indent=2))
        status  = raw.get("e", "")
        events_raw = raw.get("a", [])

        if status != "Success" and not events_raw:
            return self._error_schema(bl_no, f"ONE LINE returned status: {status}")

        events = [self._parse_event(e) for e in events_raw]

        # Sort events by date ascending (oldest first)
        events.sort(key=lambda x: x["timestamp"] or "")

        # Derive POL/POD from first/last ACTUAL events
        pol, pod = self._derive_pol_pod(events_raw)

        return {
            "bl_number":  bl_no,
            "carrier":    self.CARRIER_NAME,
            "booking_no": bl_no,
            "pol":        pol,
            "pod":        pod,
            "vessel":     "",       # ONE LINE cop-events doesn't return vessel at top level
            "voyage":     "",
            "etd":        None,
            "eta":        None,
            "containers": [],       # container_no passed separately; not in this response
            "vessels":    [],
            "route":      [],
            "events":     events,   # BL-level events (all movement milestones)
            "raw":        raw,
        }

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _parse_event(self, item: dict) -> dict:
        """Parse a single cop-event entry."""
        location     = item.get("location", {})
        location_name = location.get("locationName", "")
        country      = location.get("countryName", "")

        return make_event(
            timestamp=  self._parse_date(item.get("eventDate") or item.get("eventLocalPortDate")),
            status=     item.get("eventName", ""),
            location=   location_name,
            country=    country,
            vessel=     None,   # not in cop-events response
            voyage=     None,
            event_type= item.get("triggerType", ""),   # "ACTUAL" | "ESTIMATED"
            raw_code=   item.get("matrixId", ""),       # e.g. "E012", "E040", "E058"
        )

    def _derive_pol_pod(self, events_raw: list) -> tuple[str, str]:
        """
        Attempt to derive POL and POD from event sequence.
        POL = location of first 'Gate In' or 'Loaded on Vessel' event
        POD = location of last 'Discharged' or 'Empty Return' event
        """
        pol_keywords = ["gate in", "loaded on vessel", "stuffing"]
        pod_keywords = ["discharged", "delivery", "empty container return", "empty return"]

        pol = ""
        pod = ""

        for e in events_raw:
            name     = e.get("eventName", "").lower()
            location = e.get("location", {}).get("locationName", "")

            if not pol:
                if any(k in name for k in pol_keywords):
                    pol = location

            if any(k in name for k in pod_keywords):
                pod = location

        return pol, pod

    def _parse_date(self, raw_date: Optional[str]) -> Optional[str]:
        """
        Normalize ONE LINE date strings to ISO 8601.
        Input format: "2026-04-24T15:45:00.000Z"
        Output:       "2026-04-24T15:45:00"
        """
        if not raw_date:
            return None
        try:
            # Strip milliseconds and Z, keep datetime
            dt = datetime.strptime(raw_date[:19], "%Y-%m-%dT%H:%M:%S")
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except (ValueError, IndexError):
            return raw_date


# -------------------------------------------------------------------------
# Quick test
# -------------------------------------------------------------------------

if __name__ == "__main__":
    adapter = OneLineAdapter()

    # Pass booking_no + container_no from your Postman screenshot
    result = adapter.fetch("CPTG13635800", container_no="FFAU2906595")

    print(json.dumps(result, indent=2, default=str))