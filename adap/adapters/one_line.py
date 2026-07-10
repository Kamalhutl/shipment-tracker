import requests
import json
from datetime import datetime
from typing import Optional

from adap.base import BaseAdapter
from adap.schema import (
    UnifiedTracking, Container, TrackingEvent,
    make_event, make_container
)


class OneLineAdapter(BaseAdapter):
    """
    Adapter for ONE LINE carrier tracking.
    API: POST https://ecomm.one-line.com/api/v2/edh/containers/track-and-trace/search
    Body: {"page": 1, "page_length": 10,
           "filters": {"search_text": "<booking/container no>", "search_type": "BKG_NO" | "CN"},
           "timestamp": <epoch ms>}
    Auth: Cookie-based (sessLocale, loginType, isPin, __cf_bm, _cfuvid, etc.)

    NOTE: the previous version of this adapter called a GET .../cop-events
    endpoint. That endpoint returns HTTP 400 for these inputs — it's not
    what ecomm.one-line.com itself uses. The POST .../search endpoint above
    is the one confirmed working via browser devtools capture.
    """

    CARRIER_NAME = "ONE_LINE"

    BASE_URL = "https://ecomm.one-line.com/api/v2/edh/containers/track-and-trace/search"

    # -------------------------------------------------------------------------
    # Cookie — paste your full cookie string here from DevTools/Postman
    # It expires (watch for __cf_bm / _cfuvid / _dd_s_v2); you'll need to
    # refresh it periodically.
    # -------------------------------------------------------------------------
    COOKIE = "sessLocale=en; loginType=okta; isPin=false;"  # <-- paste full cookie here

    HEADERS = {
        "accept":               "application/json, text/plain, */*",
        "accept-language":      "en-GB,en-US;q=0.9,en;q=0.8",
        "cache-control":        "no-cache, no-store, must-revalidate",
        "content-type":         "application/json",
        "origin":               "https://ecomm.one-line.com",
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

    # ONE LINE booking numbers commonly appear prefixed with the carrier's
    # SCAC code "ONEY" (e.g. "ONEYDOHG00091700" -> "DOHG00091700").
    BOOKING_PREFIX = "ONEY"

    # -------------------------------------------------------------------------
    # Allow an optional container_no override; ONE LINE's search filter
    # supports either a booking/BL search (BKG_NO) or a container search (CN).
    # -------------------------------------------------------------------------

    def fetch(self, bl_no: str, container_no: str = "") -> dict:
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
        POST track-and-trace/search with a JSON body.

        search_text is the booking number (ONEY prefix stripped) unless a
        container_no was explicitly supplied, in which case we search by
        container number instead.
        """

        if container_no:
            search_text = container_no.strip().upper()
            search_type = "CN"
        else:
            # Strip the literal "ONEY" prefix (NOT lstrip — lstrip removes
            # any leading chars found in the given set, not the substring,
            # and would over-strip a number like "ONEYONE123456").
            search_text = bl_no
            if search_text.startswith(self.BOOKING_PREFIX):
                search_text = search_text[len(self.BOOKING_PREFIX):]
            search_type = "BKG_NO"

        payload = {
            "page": 1,
            "page_length": 10,
            "filters": {
                "search_text": search_text,
                "search_type": search_type,
            },
            "timestamp": int(datetime.utcnow().timestamp() * 1000),
        }

        try:
            response = requests.post(
                self.BASE_URL,
                headers=self.HEADERS,
                json=payload,
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
            body_snippet = ""
            try:
                body_snippet = e.response.text[:300]
            except Exception:
                pass
            return {
                "error": f"ONE LINE HTTP error: {e.response.status_code} {body_snippet}".strip(),
                "carrier": self.CARRIER_NAME,
                "bl_number": bl_no,
            }

        except requests.exceptions.RequestException as e:
            return {"error": f"ONE LINE request failed: {str(e)}", "carrier": self.CARRIER_NAME, "bl_number": bl_no}

        except json.JSONDecodeError:
            return {"error": "ONE LINE returned non-JSON response", "carrier": self.CARRIER_NAME, "bl_number": bl_no}

    # -------------------------------------------------------------------------
    # Normalizer
    # -------------------------------------------------------------------------

    def _normalize(self, raw: dict, bl_no: str) -> dict:
        """
        Map raw ONE LINE /search response → unified schema.

        IMPORTANT: this adapter was previously written against the
        .../cop-events response shape ({"e": "Success", "a": [...]}).
        The .../search endpoint we're now calling is a different API and
        very likely returns a different envelope (e.g. a "data"/"list"/
        "result" wrapper around per-booking or per-container records,
        possibly paginated per the page/page_length we send).

        We don't yet have a captured sample of a real /search response, so
        this parses defensively: it looks for a list of records/events
        under several common key names, and falls back to treating the
        whole payload as a single record if none match. Once you've hit
        the endpoint successfully, print(raw) and tighten this to match
        the actual field names.
        """

        print("RAW RESPONSE:", json.dumps(raw, indent=2))

        # Old cop-events shape, kept for backward compatibility in case
        # this adapter is ever pointed back at that endpoint.
        if "e" in raw and "a" in raw:
            status = raw.get("e", "")
            events_raw = raw.get("a", [])
            if status != "Success" and not events_raw:
                return self._error_schema(bl_no, f"ONE LINE returned status: {status}")
        else:
            # New /search shape: look for the list of results under any of
            # these commonly-used envelope keys.
            events_raw = None
            for key in ("data", "list", "result", "results", "records", "rows", "items"):
                value = raw.get(key)
                if isinstance(value, list):
                    events_raw = value
                    break
                if isinstance(value, dict):
                    for nested_key in ("list", "result", "results", "records", "rows", "items"):
                        nested = value.get(nested_key)
                        if isinstance(nested, list):
                            events_raw = nested
                            break
                    if events_raw is not None:
                        break

            if events_raw is None:
                # Nothing matched — no results found for this search_text,
                # or the shape is unrecognized. Surface the raw payload so
                # it's easy to inspect and adjust the key names above.
                error = self._error_schema(
                    bl_no,
                    "ONE LINE /search returned no recognizable result list "
                    "(check 'raw' in the error payload to find the correct key).",
                )
                error["raw"] = raw
                return error

        events_raw = self._flatten_events(events_raw)

        events = [self._parse_event(e) for e in events_raw]

        # Sort events by date ascending (oldest first)
        # NOTE: make_event() returns a TrackingEvent dataclass instance,
        # not a dict — use attribute access, not subscripting.
        events.sort(key=lambda x: x.timestamp or "")

        # Derive POL/POD from first/last ACTUAL events
        pol, pod = self._derive_pol_pod(events_raw)

        # Vessel/voyage/etd/eta aren't guaranteed to live on the event
        # records themselves — search the whole raw payload for them too.
        vessel = self._deep_find(raw, ("vesselname", "vessel")) or ""
        voyage = self._deep_find(raw, ("voyageno", "voyage", "voyagenumber")) or ""
        etd = self._parse_date(self._deep_find(raw, ("etd", "estimateddeparture", "departuredate")))
        eta = self._parse_date(self._deep_find(raw, ("eta", "estimatedarrival", "arrivaldate")))

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
            "containers": [],       # container_no passed separately; not in this response
            "vessels":    [],
            "route":      [],
            "events":     events,   # BL-level events (all movement milestones)
            "raw":        raw,
        }

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    # Keys under which a single result record might nest its own list of
    # movement/status events, rather than being an event itself.
    _NESTED_EVENT_LIST_KEYS = (
        "events", "eventlist", "movements", "milestones",
        "history", "containerevents", "trackingevents", "copevents",
        "statuslist", "activities",
    )

    def _flatten_events(self, records: list) -> list:
        """
        The /search endpoint may return booking/container-level records
        that each nest their own event history under a key like "events"
        or "movements", rather than the records themselves being events.
        Flatten to a single list of raw event dicts either way.
        """
        flattened = []
        for record in records:
            if not isinstance(record, dict):
                continue
            nested = None
            for key in self._NESTED_EVENT_LIST_KEYS:
                value = self._get_ci(record, key)
                if isinstance(value, list) and value:
                    nested = value
                    break
            if nested is not None:
                flattened.extend(item for item in nested if isinstance(item, dict))
            else:
                flattened.append(record)
        return flattened

    def _get_ci(self, d: dict, key: str):
        """Case-insensitive single-level dict lookup."""
        if not isinstance(d, dict):
            return None
        for k, v in d.items():
            if k.lower() == key.lower():
                return v
        return None

    def _deep_find(self, node, candidates, max_depth: int = 4):
        """
        Recursively search dicts/lists for the first non-empty value whose
        key (case-insensitive) matches one of `candidates`. Used because we
        don't have a confirmed sample of the /search response shape, so we
        can't hardcode exact key names or nesting depth.
        """
        candidates = {c.lower() for c in candidates}
        stack = [(node, 0)]
        while stack:
            current, depth = stack.pop(0)
            if isinstance(current, dict):
                for k, v in current.items():
                    if k.lower() in candidates and v not in (None, "", [], {}):
                        return v
                if depth < max_depth:
                    for v in current.values():
                        if isinstance(v, (dict, list)):
                            stack.append((v, depth + 1))
            elif isinstance(current, list):
                if depth < max_depth:
                    for item in current:
                        if isinstance(item, (dict, list)):
                            stack.append((item, depth + 1))
        return None

    def _parse_event(self, item: dict) -> dict:
        """
        Parse a single event/movement record. Field names are guessed
        defensively via _deep_find since the exact /search response shape
        isn't confirmed yet — tighten these candidate lists once you have
        a real sample (see the RAW RESPONSE print in _normalize).
        """
        status = self._deep_find(item, (
            "eventname", "status", "eventdesc", "eventdescription",
            "description", "milestone", "activityname", "movementtype",
            "statusdesc", "activity",
        )) or ""

        location = self._deep_find(item, (
            "locationname", "location", "portname", "port",
            "placename", "place", "terminal",
        )) or ""
        # location might itself be a nested dict like {"locationName": ...}
        if isinstance(location, dict):
            location = self._deep_find(location, ("locationname", "name")) or ""

        country = self._deep_find(item, ("countryname", "country")) or ""
        if isinstance(country, dict):
            country = self._deep_find(country, ("countryname", "name")) or ""

        timestamp_raw = self._deep_find(item, (
            "eventdate", "eventlocalportdate", "date", "eventdatetime",
            "actualdate", "eventtime", "occurredat", "statusdate",
            "movementdate", "timestamp",
        ))

        vessel = self._deep_find(item, ("vesselname", "vessel")) or None
        voyage = self._deep_find(item, ("voyageno", "voyage", "voyagenumber")) or None
        event_type = self._deep_find(item, (
            "triggertype", "eventstatus", "actualestimated", "type",
        )) or ""
        raw_code = self._deep_find(item, ("matrixid", "eventcode", "code")) or ""

        return make_event(
            timestamp=  self._parse_date(timestamp_raw),
            status=     str(status),
            location=   str(location),
            country=    str(country) if country else None,
            vessel=     str(vessel) if vessel else None,
            voyage=     str(voyage) if voyage else None,
            event_type= str(event_type),
            raw_code=   str(raw_code),
        )

    def _derive_pol_pod(self, events_raw: list) -> tuple[str, str]:
        """
        Attempt to derive POL and POD from event sequence.
        POL = location of first 'Gate In' or 'Loaded on Vessel' event
        POD = location of last 'Discharged' or 'Empty Return' event
        """
        pol_keywords = ["gate in", "loaded on vessel", "stuffing", "load"]
        pod_keywords = ["discharged", "delivery", "empty container return", "empty return"]

        pol = ""
        pod = ""

        for e in events_raw:
            if not isinstance(e, dict):
                continue
            name = str(self._deep_find(e, (
                "eventname", "status", "eventdesc", "description", "activity",
            )) or "").lower()
            location = self._deep_find(e, ("locationname", "location", "portname", "port"))
            if isinstance(location, dict):
                location = self._deep_find(location, ("locationname", "name")) or ""
            location = str(location or "")

            if not pol and any(k in name for k in pol_keywords):
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