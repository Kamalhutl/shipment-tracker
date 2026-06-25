import requests
import json
from datetime import datetime
from typing import Optional

from base import BaseAdapter
from schema import make_event, make_container


class MSCAdapter(BaseAdapter):
    """
    Adapter for MSC carrier tracking.
    API: POST https://www.msc.com/api/feature/tools/TrackingInfo
    Body: {"trackingNumber": "<BL>", "trackingMode": "0"}
    Auth: Heavy cookie (Akamai bot protection + ASP.NET_SessionId)

    ⚠️  MSC uses Akamai bot protection (ak_bmsc cookie).
        This cookie is short-lived (~2 hours) and device-fingerprinted.
        You MUST grab a fresh cookie from DevTools/Postman each session.
        If you get 403 or empty response → cookie expired, grab new one.
    """

    CARRIER_NAME = "MSC"

    BASE_URL = "https://www.msc.com/api/feature/tools/TrackingInfo"

    # -------------------------------------------------------------------------
    # Cookie — paste your FULL cookie string from Postman/DevTools here
    # Most critical ones: ak_bmsc, ASP.NET_SessionId, AKA_A2
    # Expires every ~2 hours due to Akamai fingerprinting
    # -------------------------------------------------------------------------
    COOKIE = (
        "OptanonAlertBoxClosed=2026-06-19T08:18:38.742Z; "
        "ASP.NET_SessionId=z2ov05lqcqrmchwyybmkpkrl; "
        "isLoggedUser=false; "
        "AKA_A2=A; "
        "ak_bmsc=5D987532C7B09A683AF43DC16033E36B~000000000000000000000000000000~..."  # paste full
        # Add all remaining cookies from your curl here
    )

    HEADERS = {
        "accept":             "application/json, text/plain, */*",
        "accept-language":    "en-GB,en-US;q=0.9,en;q=0.8",
        "content-type":       "application/json",
        "origin":             "https://www.msc.com",
        "priority":           "u=1, i",
        "referer":            "https://www.msc.com/en/track-a-shipment",
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

    # trackingMode: "0" = BL, "1" = Container, "2" = Booking
    TRACKING_MODE_BL        = "0"
    TRACKING_MODE_CONTAINER = "1"
    TRACKING_MODE_BOOKING   = "2"

    # -------------------------------------------------------------------------
    # API call
    # -------------------------------------------------------------------------

    def _call_api(self, bl_no: str) -> dict:
        """
        POST TrackingInfo with BL number.

        Body from curl:
        {"trackingNumber": "MEDUGR867656", "trackingMode": "0"}
        """
        payload = {
            "trackingNumber": bl_no,
            "trackingMode":   self.TRACKING_MODE_BL,
        }

        try:
            response = requests.post(
                self.BASE_URL,
                headers=self.HEADERS,
                json=payload,
                timeout=30,
            )

            # Akamai blocks → 403
            if response.status_code == 403:
                return {"error": "MSC blocked by Akamai (403). Cookie expired — grab fresh cookie from DevTools."}

            if response.status_code == 401:
                return {"error": "MSC unauthorized (401). Update COOKIE in msc.py"}

            response.raise_for_status()

            if not response.text.strip():
                return {"error": "MSC returned empty response — Akamai cookie likely expired"}

            return response.json()

        except requests.exceptions.Timeout:
            return {"error": "MSC API timeout"}

        except requests.exceptions.HTTPError as e:
            return {"error": f"MSC HTTP error: {e.response.status_code}"}

        except requests.exceptions.RequestException as e:
            return {"error": f"MSC request failed: {str(e)}"}

        except json.JSONDecodeError:
            return {"error": "MSC returned non-JSON — likely Akamai challenge page (cookie expired)"}

    # -------------------------------------------------------------------------
    # Normalizer
    # -------------------------------------------------------------------------

    def _normalize(self, raw: dict, bl_no: str) -> dict:
        """
        Map raw MSC response → unified schema.

        Raw MSC structure (from screenshot):
        {
            "IsSuccess": true,
            "Data": {
                "TrackingType":          "Bill Of Lading",
                "TrackingTitle":         "BILL OF LADING:",
                "TrackingNumber":        "MEDUGR867656",
                "CurrentDate":           "23/06/2026",
                "PriceCalculationLabel": "...",
                "TrackingResultsLabel":  "...",
                "BillOfLadings": [
                    {
                        "BillOfLadingNumber":   "MEDUGR867656",
                        "NumberOfContainers":   5,
                        "GeneralTrackingInfo": {
                            "ShippedFrom": "SYDNEY, AU",
                            "ShippedTo":   "TUTICORIN, IN",
                            ...
                        },
                        "ContainersInfo": [
                            {
                                "ContainerNumber": "...",
                                "ContainerType":   "...",
                                "Events": [
                                    {
                                        "Order":        1,
                                        "Date":         "...",
                                        "Description":  "...",
                                        "Location":     "...",
                                        "Vessel":       "...",
                                        "Voyage":       "...",
                                        ...
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        }
        """
        if not raw.get("IsSuccess"):
            msg = raw.get("Message") or raw.get("message") or "MSC returned IsSuccess=false"
            return self._error_schema(bl_no, msg)

        data = raw.get("Data", {})
        if not data:
            return self._error_schema(bl_no, "MSC returned empty Data")

        bill_of_ladings = data.get("BillOfLadings", [])
        if not bill_of_ladings:
            return self._error_schema(bl_no, "MSC: no BillOfLadings in response")

        # Take first BL entry (usually only one when searching by BL)
        bl_data = bill_of_ladings[0]

        general     = bl_data.get("GeneralTrackingInfo", {})
        pol         = general.get("ShippedFrom", "")
        pod         = general.get("ShippedTo", "")

        # Parse containers + their events
        containers_raw = bl_data.get("ContainersInfo", [])
        containers     = [self._parse_container(c, pol, pod) for c in containers_raw]

        # Flatten all events across containers for BL-level events list
        all_events = []
        for c in containers:
            all_events.extend(c.events)
        all_events.sort(key=lambda x: x.timestamp or "")

        # Primary vessel/voyage from first event that has vessel info
        vessel, voyage = self._extract_primary_vessel(containers_raw)

        # ETD/ETA from GeneralTrackingInfo if present
        etd = self._parse_date(general.get("ETD") or general.get("Etd") or general.get("DepartureDate"))
        eta = self._parse_date(general.get("ETA") or general.get("Eta") or general.get("ArrivalDate"))

        return {
            "bl_number":  bl_no,
            "carrier":    self.CARRIER_NAME,
            "booking_no": bl_data.get("BillOfLadingNumber", bl_no),
            "pol":        pol,
            "pod":        pod,
            "vessel":     vessel,
            "voyage":     voyage,
            "etd":        etd,
            "eta":        eta,
            "containers": [self._container_to_dict(c) for c in containers],
            "vessels":    [],
            "route":      [],
            "events":     all_events,
            "raw":        raw,
        }

    # -------------------------------------------------------------------------
    # Sub-parsers
    # -------------------------------------------------------------------------

    def _parse_container(self, item: dict, pol: str, pod: str):
        """Parse a single MSC container entry."""
        events_raw = item.get("Events", [])
        events = [self._parse_event(e) for e in events_raw]
        events.sort(key=lambda x: x.timestamp or "")

        # Get vessel/voyage from first event that has them
        vessel = ""
        voyage = ""
        for e_raw in events_raw:
            v = e_raw.get("Vessel") or e_raw.get("VesselName", "")
            voy = e_raw.get("Voyage") or e_raw.get("VoyageNumber", "")
            if v:
                vessel = v
                voyage = voy
                break

        # Size/type parsing — MSC ContainerType e.g. "40' High Cube"
        raw_type = item.get("ContainerType", "")
        size, ctype = self._parse_container_type(raw_type)

        container = make_container(
            container_no=item.get("ContainerNumber", ""),
            size=size,
            type=ctype,
            pol=pol,
            pod=pod,
            vessel=vessel,
            voyage=voyage,
            events=events,
        )
        return container

    def _parse_event(self, item: dict):
        """Parse a single MSC event."""
        return make_event(
            timestamp=  self._parse_date(item.get("Date") or item.get("EventDate")),
            status=     item.get("Description") or item.get("Activity") or "",
            location=   item.get("Location") or item.get("Port") or "",
            country=    item.get("Country", ""),
            vessel=     item.get("Vessel") or item.get("VesselName") or "",
            voyage=     item.get("Voyage") or item.get("VoyageNumber") or "",
            event_type= item.get("EventType", ""),
            raw_code=   str(item.get("Order", "")),
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _parse_container_type(self, raw: str) -> tuple[str, str]:
        """
        Parse MSC container type string.
        "40' High Cube" → ("40", "HC")
        "20' Standard"  → ("20", "ST")
        "45' High Cube" → ("45", "HC")
        """
        size  = ""
        ctype = ""
        if not raw:
            return size, ctype

        raw_lower = raw.lower()

        # Extract size
        for s in ["45", "40", "20"]:
            if s in raw:
                size = s
                break

        # Extract type
        if "high cube" in raw_lower or "hc" in raw_lower:
            ctype = "HC"
        elif "reefer" in raw_lower or "rf" in raw_lower:
            ctype = "RF"
        elif "open top" in raw_lower or "ot" in raw_lower:
            ctype = "OT"
        elif "flat" in raw_lower or "fr" in raw_lower:
            ctype = "FR"
        else:
            ctype = "ST"  # Standard/General Purpose

        return size, ctype

    def _extract_primary_vessel(self, containers_raw: list) -> tuple[str, str]:
        """Extract primary vessel/voyage from first container's first event."""
        for container in containers_raw:
            for event in container.get("Events", []):
                v   = event.get("Vessel") or event.get("VesselName", "")
                voy = event.get("Voyage") or event.get("VoyageNumber", "")
                if v:
                    return v, voy
        return "", ""

    def _container_to_dict(self, c) -> dict:
        """Convert Container dataclass to dict for JSON output."""
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
        Normalize MSC date strings to ISO 8601.
        Handles multiple formats MSC uses:
          "23/06/2026"              → "2026-06-23"
          "2026-06-23T00:00:00"    → "2026-06-23T00:00:00"
          "23 Jun 2026"            → "2026-06-23"
        """
        if not raw_date:
            return None
        raw_date = raw_date.strip()

        formats = [
            "%d/%m/%Y",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
            "%d %b %Y",
            "%d-%b-%Y",
            "%b %d, %Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(raw_date[:len(raw_date)], fmt).strftime(
                    "%Y-%m-%dT%H:%M:%S" if "T" in raw_date else "%Y-%m-%d"
                )
            except ValueError:
                continue
        return raw_date


# -------------------------------------------------------------------------
# Quick test
# -------------------------------------------------------------------------

if __name__ == "__main__":
    adapter = MSCAdapter()

    # BL from your Postman screenshot
    result = adapter.fetch("MEDUGR867656")

    print(json.dumps(result, indent=2, default=str))
