import requests
import json
from datetime import datetime
from typing import Optional

from adap.base import BaseAdapter
from adap.schema import make_event, make_container


class HapagAdapter(BaseAdapter):
    """
    Adapter for Hapag-Lloyd carrier tracking.

    API: GET https://tracking.api.hlag.cloud/api/tracking/events?reference=<BL/container>

    This replaces the old Imperva-guarded
    `hapag-lloyd.com/.../track-by-container-solution.json` endpoint, which
    Hapag has retired in favor of this simpler public BFF API.

    Auth: static header `x-token: public` -- no cookie, no session warm-up.
    Confirmed via DevTools + curl on 2026-07-07.

    Confirmed real response shape:
        {
          "groups": [
            {
              "containerNumber": "BMOU4954164",
              "events": [
                {
                  "containerNumber":     "BMOU4954164",
                  "containerType":       "45GP",
                  "eventDescription":    "Gated out" | "Gated in" | "Loaded" | "Discharged",
                  "eventLocation":       "LONDON GATEWAY PORT",
                  "eventDate":           "2026-04-15",
                  "eventTime":           "12:05",
                  "eventTransport":      "Truck" | "<VESSEL NAME>",
                  "eventVoyageNo":       "616E",              # only present for vessel legs
                  "eventClassifierCode": "Actual" | "Estimated"
                },
                ...
              ]
            },
            ...  # one group per container on this BL
          ]
        }
    """

    CARRIER_NAME = "HAPAG"

    EVENTS_URL = "https://tracking.api.hlag.cloud/api/tracking/events"

    HEADERS = {
        "accept":             "application/json, text/plain, */*",
        "accept-language":    "en-GB,en-US;q=0.9,en;q=0.8",
        "origin":             "https://www.hapag-lloyd.com",
        "referer":            "https://www.hapag-lloyd.com/",
        "sec-fetch-dest":     "empty",
        "sec-fetch-mode":     "cors",
        "sec-fetch-site":     "cross-site",
        "user-agent":         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "x-token":            "public",
    }

    # -------------------------------------------------------------------------
    # API call
    # -------------------------------------------------------------------------

    def _call_api(self, bl_no: str) -> dict:
        """GET tracking data from Hapag-Lloyd's hlag.cloud events API."""
        try:
            response = requests.get(
                self.EVENTS_URL,
                headers=self.HEADERS,
                params={"reference": bl_no},
                timeout=30,
            )

            if response.status_code == 401:
                return {"error": "Hapag-Lloyd unauthorized (401). x-token header may have changed -- recheck via DevTools."}

            if response.status_code == 403:
                return {"error": "Hapag-Lloyd blocked (403). Possible bot detection re-enabled -- recheck via DevTools."}

            if response.status_code == 404:
                return {"error": f"Hapag-Lloyd: BL/container '{bl_no}' not found (404)."}

            response.raise_for_status()

            if not response.text.strip():
                return {"error": "Hapag-Lloyd returned empty response."}

            return response.json()

        except requests.exceptions.Timeout:
            return {"error": "Hapag-Lloyd API timeout (30 s)."}

        except requests.exceptions.HTTPError as e:
            return {"error": f"Hapag-Lloyd HTTP error: {e.response.status_code}"}

        except requests.exceptions.RequestException as e:
            return {"error": f"Hapag-Lloyd request failed: {str(e)}"}

        except json.JSONDecodeError:
            return {"error": "Hapag-Lloyd returned non-JSON response."}

    # -------------------------------------------------------------------------
    # Normalizer
    # -------------------------------------------------------------------------

    def _normalize(self, raw: dict, bl_no: str) -> dict:
        """Map raw hlag.cloud `groups` response -> unified schema."""
        groups = raw.get("groups", [])

        if not groups:
            return self._error_schema(bl_no, "Hapag-Lloyd: no container groups in response.")

        containers_out = []
        all_events     = []
        top_pol = top_pod = top_vessel = top_voyage = ""
        top_etd = top_eta = None

        for group in groups:
            container_no = group.get("containerNumber", "")
            events_raw   = group.get("events", [])

            events = [self._parse_event(e) for e in events_raw]
            events.sort(key=lambda x: x.timestamp or "")
            all_events.extend(events)

            size, ctype = self._parse_container_type(
                events_raw[0].get("containerType", "") if events_raw else ""
            )

            pol, pod, vessel, voyage, etd, eta = self._derive_leg_info(events_raw)

            containers_out.append(make_container(
                container_no=container_no,
                size=size,
                type=ctype,
                pol=pol,
                pod=pod,
                vessel=vessel,
                voyage=voyage,
                etd=etd,
                eta=eta,
                events=events,
            ))

            # Use first container's leg info as the BL-level summary
            if not top_pol:
                top_pol, top_pod = pol, pod
                top_vessel, top_voyage = vessel, voyage
                top_etd, top_eta = etd, eta

        all_events.sort(key=lambda x: x.timestamp or "")

        return {
            "bl_number":  bl_no,
            "carrier":    self.CARRIER_NAME,
            "booking_no": bl_no,
            "pol":        top_pol,
            "pod":        top_pod,
            "vessel":     top_vessel,
            "voyage":     top_voyage,
            "etd":        top_etd,
            "eta":        top_eta,
            "containers": [self._container_to_dict(c) for c in containers_out],
            "vessels":    [],
            "route":      [],
            "events":     all_events,
            "raw":        raw,
        }

    # -------------------------------------------------------------------------
    # Sub-parsers
    # -------------------------------------------------------------------------

    def _parse_event(self, item: dict):
        """Parse a single hlag.cloud event entry into a TrackingEvent."""
        transport = item.get("eventTransport", "") or ""
        is_vessel = transport.lower() != "truck" and bool(transport)

        return make_event(
            timestamp=  self._parse_date(item.get("eventDate"), item.get("eventTime")),
            status=     item.get("eventDescription", ""),
            location=   item.get("eventLocation", ""),
            country=    None,   # not provided by this endpoint
            vessel=     transport if is_vessel else None,
            voyage=     item.get("eventVoyageNo") or None,
            event_type= item.get("eventClassifierCode", ""),   # "Actual" | "Estimated"
            raw_code=   item.get("containerNumber", ""),
        )

    def _derive_leg_info(self, events_raw: list) -> tuple:
        """
        Derive POL/POD/vessel/voyage/etd/eta for one container from its events.

        POL = location of the FIRST "Loaded" event
        POD = location of the LAST  "Discharged" event
        vessel/voyage = taken from that same last "Loaded" leg (the final
                        vessel carrying the container toward its destination)
        etd = date/time of that first "Loaded" event
        eta = date/time of that last "Discharged" event
        """
        pol = pod = vessel = voyage = ""
        etd = eta = None

        loaded_events      = [e for e in events_raw if e.get("eventDescription", "").lower() == "loaded"]
        discharged_events  = [e for e in events_raw if e.get("eventDescription", "").lower() == "discharged"]

        if loaded_events:
            first_load = loaded_events[0]
            last_load  = loaded_events[-1]
            pol = first_load.get("eventLocation", "")
            etd = self._parse_date(first_load.get("eventDate"), first_load.get("eventTime"))
            vessel = last_load.get("eventTransport", "")
            voyage = last_load.get("eventVoyageNo", "")

        if discharged_events:
            last_disc = discharged_events[-1]
            pod = last_disc.get("eventLocation", "")
            eta = self._parse_date(last_disc.get("eventDate"), last_disc.get("eventTime"))

        return pol, pod, vessel, voyage, etd, eta

    def _parse_container_type(self, raw_type: str) -> tuple:
        """
        Split "45GP" -> size="45", type="GP".
        Handles "20GP", "40HC", "45GP", "40RF", etc.
        """
        if not raw_type:
            return "", ""
        i = 0
        while i < len(raw_type) and raw_type[i].isdigit():
            i += 1
        return raw_type[:i], raw_type[i:]

    def _container_to_dict(self, c) -> dict:
        """Convert Container dataclass -> dict for JSON output."""
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

    def _parse_date(self, date_str, time_str=None):
        """
        Combine hlag.cloud's separate date/time fields into ISO 8601.
        Input:  eventDate="2026-04-15", eventTime="12:05"
        Output: "2026-04-15T12:05:00"
        """
        if not date_str:
            return None
        date_str = date_str.strip()
        if time_str:
            try:
                dt = datetime.strptime(f"{date_str} {time_str.strip()}", "%Y-%m-%d %H:%M")
                return dt.strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return date_str


# -------------------------------------------------------------------------
# Quick test
# -------------------------------------------------------------------------

if __name__ == "__main__":
    adapter = HapagAdapter()
    result = adapter.fetch("HLCULIV260401168")
    print(json.dumps(result, indent=2, default=str))