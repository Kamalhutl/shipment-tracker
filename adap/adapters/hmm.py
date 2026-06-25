import json
import re
from datetime import datetime
from typing import Optional

# curl_cffi mimics Chrome TLS fingerprint — required to bypass Akamai blocking Python requests
# Install: pip install curl-cffi
try:
    from curl_cffi import requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    import requests
    CURL_CFFI_AVAILABLE = False

from base import BaseAdapter
from schema import make_event, make_route_point


class HMMAdapter(BaseAdapter):
    """
    Adapter for HMM (Hyundai Merchant Marine) carrier tracking.
    API: POST https://www.hmm21.com/e-service/general/trackNTrace/selectTrackNTrace.do
    Body: {"type":"bl","listBl":["<BL>"],"listCntr":[],"listBkg":[],"listPo":[]}
    Response: HTML (not JSON!) — must parse with regex/string parsing
    Auth: Heavy cookie (Akamai bm_sz/bm_sv/ak_bmsc/_abck + JSESSIONID + Oracle LBS)

    ⚠️  HMM uses Akamai bot protection AND Java JSESSIONID.
        Both expire quickly. x-csrf-token also required and session-bound.
        Grab fresh cookies + csrf token from DevTools before each session.
    """

    CARRIER_NAME = "HMM"

    BASE_URL = "https://www.hmm21.com/e-service/general/trackNTrace/selectTrackNTrace.do"

    # -------------------------------------------------------------------------
    # Paste your full cookie string here — grab from Postman/DevTools
    # Critical: JSESSIONID, ak_bmsc, bm_sz, bm_sv, _abck, X-Oracle-BMC-LBS-Route
    # -------------------------------------------------------------------------
    COOKIE = (
        "WMONID=sgff69rgS6w; "
        "JSESSIONID=4f817c83a4c74ecbb93813a6cfe8227a4565a29a1441e0b69f37!1630274900; "
        "X-Oracle-BMC-LBS-Route=6f87e2864f32a20cf423186fe64fc2e9857044fce8c201ef1f596191ac75de24112a9104a727ffc3; "
        "bm_sz=A48A5E07198C8E9EB11E7B5FB6120D68~...; "
        "bm_sv=94AD97F32029A38177F21D97B567D3AD~...; "
        "ak_bmsc=9D3505556D8395A89566A6343FE9A958~...; "
        "_abck=CC9F292EBCD8D136F13A8587796D4CFC~..."
        # paste your full cookie string here
    )

    # -------------------------------------------------------------------------
    # CSRF token — session-bound, grab fresh one from DevTools each session
    # Found in: request headers as x-csrf-token
    # -------------------------------------------------------------------------
    CSRF_TOKEN = "dd3fab4f-39ea-4f58-88a2-bdd16e92218e"  # <-- update each session

    HEADERS = {
        "accept":             "text/html, */*; q=0.01",
        "accept-language":    "en-GB,en-US;q=0.9,en;q=0.8",
        "content-type":       "application/json; charset=UTF-8",
        "origin":             "https://www.hmm21.com",
        "priority":           "u=0, i",
        "referer":            "https://www.hmm21.com/e-service/general/trackNTrace/TrackNTrace.do",
        "sec-ch-ua":          '"Google Chrome";v="149", "Chromium";v="149", "Not.A/Brand";v="99"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest":     "empty",
        "sec-fetch-mode":     "cors",
        "sec-fetch-site":     "same-origin",
        "user-agent":         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "x-csrf-token":       CSRF_TOKEN,
        "x-requested-with":   "XMLHttpRequest",
        "Cookie":             COOKIE,
    }

    # -------------------------------------------------------------------------
    # API call
    # -------------------------------------------------------------------------

    def _call_api(self, bl_no: str) -> dict:
        """
        POST with BL in listBl array.
        Response is HTML, not JSON — wrap it in a dict for _normalize.

        Body from curl:
        {"type":"bl","listBl":["BCNA66532900"],"listCntr":[],"listBkg":[],"listPo":[]}
        """
        payload = {
            "type":    "bl",
            "listBl":  [bl_no],
            "listCntr": [],
            "listBkg":  [],
            "listPo":   [],
        }

        try:
            # impersonate="chrome110" tells curl_cffi to use Chrome TLS fingerprint
            # This bypasses Akamai's TLS fingerprint detection that blocks Python requests
            kwargs = dict(
                headers=self.HEADERS,
                json=payload,
                timeout=60,
            )
            if CURL_CFFI_AVAILABLE:
                kwargs["impersonate"] = "chrome110"

            response = requests.post(self.BASE_URL, **kwargs)

            if response.status_code == 403:
                return {"error": "HMM blocked by Akamai (403). Refresh cookie + CSRF token in hmm.py"}

            if response.status_code == 401:
                return {"error": "HMM unauthorized (401). JSESSIONID expired — refresh cookie."}

            response.raise_for_status()

            html = response.text.strip()

            if not html:
                return {"error": "HMM returned empty response — cookie/CSRF likely expired"}

            # Check for login redirect
            if "login" in html.lower() and len(html) < 500:
                return {"error": "HMM redirected to login — JSESSIONID expired"}

            # Akamai challenge page
            if "ak_bmsc" in html and "<script" in html and len(html) < 2000:
                return {"error": "HMM Akamai challenge page — refresh ak_bmsc cookie"}

            # Return HTML wrapped in dict for _normalize
            return {"html": html, "bl_no": bl_no}

        except requests.exceptions.Timeout:
            return {"error": "HMM API timeout"}

        except requests.exceptions.HTTPError as e:
            return {"error": f"HMM HTTP error: {e.response.status_code}"}

        except requests.exceptions.RequestException as e:
            return {"error": f"HMM request failed: {str(e)}"}

    # -------------------------------------------------------------------------
    # Normalizer — parse HTML response
    # -------------------------------------------------------------------------

    def _normalize(self, raw: dict, bl_no: str) -> dict:
        """
        Parse HMM HTML response → unified schema.

        HTML table structure (from screenshot):
        Columns: Origin | Loading Port | T/S Port | T/S port | Discharging Port | Destination
        Rows:    Location | Terminal | Arrival(ETB) | Departure

        Example data visible:
        Origin:           VALENCIA, SPAIN    | APM TERMINALS VALENCIA  | 2026-04-14 11:53
        Loading Port:     VALENCIA, SPAIN    | APM TERMINALS VALENCIA  | 2026-04-14 20:45 | 2026-04-18 14:56
        T/S Port:         MUNDRA, INDIA      | ADANI MUNDRA ...        | 2026-06-21 18:24 | 2026-06-27 23:00
        T/S port:         COLOMBO, SRILANKA  | SOUTH ASIA GATEWAY ...  | 2026-07-07 00:00 | 2026-07-12 19:00
        Discharging Port: TUTICORIN, INDIA   | TUTICORIN CONTAINER ... | 2026-07-13 20:00
        Destination:      TUTICORIN, INDIA   | TUTICORIN CONTAINER ... | 2026-07-14 20:00
        """
        html = raw.get("html", "")

        if not html:
            return self._error_schema(bl_no, "No HTML content to parse")

        # Parse route stops from HTML table
        route_stops = self._parse_route_table(html)

        if not route_stops:
            return self._error_schema(bl_no, "HMM: Could not parse route table from HTML")

        # Build events from route stops
        events = self._build_events_from_route(route_stops)
        events.sort(key=lambda x: x.timestamp or "")

        # POL = Loading Port, POD = Discharging Port
        pol = self._find_stop_location(route_stops, "Loading Port")
        pod = self._find_stop_location(route_stops, "Discharging Port")

        # ETD from Loading Port departure, ETA from Discharging Port arrival
        etd = self._find_stop_date(route_stops, "Loading Port", "departure")
        eta = self._find_stop_date(route_stops, "Discharging Port", "arrival")

        # Route points for mapping
        route = [
            make_route_point(
                latitude=0.0,   # HMM doesn't return coordinates
                longitude=0.0,
                port_name=stop.get("location", ""),
                country=self._extract_country(stop.get("location", "")),
            )
            for stop in route_stops
        ]

        return {
            "bl_number":  bl_no,
            "carrier":    self.CARRIER_NAME,
            "booking_no": bl_no,
            "pol":        pol,
            "pod":        pod,
            "vessel":     "",   # HMM table doesn't show vessel name
            "voyage":     "",
            "etd":        etd,
            "eta":        eta,
            "containers": [],
            "vessels":    [],
            "route":      [self._route_to_dict(r) for r in route],
            "events":     events,
            "raw":        {"html_length": len(html)},   # don't store full HTML
        }

    # -------------------------------------------------------------------------
    # HTML parsers
    # -------------------------------------------------------------------------

    def _parse_route_table(self, html: str) -> list[dict]:
        """
        Parse the HTML route table into structured stops.
        Returns list of dicts with: stop_type, location, terminal, arrival, departure
        """
        stops = []

        # Column headers seen in screenshot
        col_headers = [
            "Origin",
            "Loading Port",
            "T/S Port",
            "Discharging Port",
            "Destination",
        ]

        # Strategy: find table rows and extract td content
        # HMM returns an HTML fragment, not full page

        # Extract all table cells — clean HTML tags
        clean = re.sub(r'<[^>]+>', '|', html)
        clean = re.sub(r'\|+', '|', clean)
        clean = re.sub(r'\s+', ' ', clean)
        cells = [c.strip() for c in clean.split('|') if c.strip()]

        # Find column positions
        header_indices = []
        for i, cell in enumerate(cells):
            for header in col_headers:
                if header.lower() in cell.lower():
                    header_indices.append((i, header))
                    break

        # Row labels
        row_labels = ["Location", "Terminal", "Arrival", "Departure"]

        # Build stop data by scanning cells after headers
        # This is a heuristic parser — adjust if HMM changes their HTML structure
        current_stop = {}
        current_field = None
        stop_type_idx = 0

        date_pattern = re.compile(r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}')
        found_header = False

        for cell in cells:
            cell_lower = cell.lower()

            # Detect column header
            for header in col_headers:
                if cell.strip() == header:
                    if current_stop:
                        stops.append(current_stop)
                    current_stop = {"stop_type": header, "location": "", "terminal": "", "arrival": None, "departure": None}
                    current_field = "location"
                    found_header = True
                    break

            if not found_header:
                continue

            # Detect row label
            if cell.strip() in ["Location", "Terminal"]:
                current_field = cell.strip().lower()
                continue
            if "arrival" in cell_lower and "etb" in cell_lower:
                current_field = "arrival"
                continue
            if cell.strip() == "Departure":
                current_field = "departure"
                continue

            # Fill data into current stop
            if current_stop and current_field:
                if current_field == "location" and not current_stop.get("location"):
                    current_stop["location"] = cell
                elif current_field == "terminal" and not current_stop.get("terminal"):
                    current_stop["terminal"] = cell
                elif current_field == "arrival":
                    if date_pattern.match(cell) and not current_stop.get("arrival"):
                        current_stop["arrival"] = cell
                elif current_field == "departure":
                    if date_pattern.match(cell) and not current_stop.get("departure"):
                        current_stop["departure"] = cell

        if current_stop:
            stops.append(current_stop)

        # Fallback: if regex parsing fails, try direct td extraction
        if not stops:
            stops = self._parse_table_fallback(html)

        return stops

    def _parse_table_fallback(self, html: str) -> list[dict]:
        """
        Fallback parser using direct regex on td tags.
        More brittle but catches different HTML structures.
        """
        stops = []

        # Find all td content
        tds = re.findall(r'<td[^>]*>(.*?)</td>', html, re.DOTALL | re.IGNORECASE)
        tds = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]
        tds = [td for td in tds if td]

        col_headers = ["Origin", "Loading Port", "T/S Port", "Discharging Port", "Destination"]
        date_pat    = re.compile(r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}')

        current = {}
        for td in tds:
            if td in col_headers:
                if current:
                    stops.append(current)
                current = {"stop_type": td, "location": "", "terminal": "", "arrival": None, "departure": None}
            elif current:
                if not current["location"] and td not in ["Location", "Terminal", "Arrival(ETB)", "Departure"]:
                    if not date_pat.match(td):
                        current["location"] = td
                elif date_pat.match(td):
                    if not current["arrival"]:
                        current["arrival"] = td
                    elif not current["departure"]:
                        current["departure"] = td

        if current:
            stops.append(current)

        return stops

    def _build_events_from_route(self, route_stops: list) -> list:
        """Convert route stops into tracking events."""
        events = []
        for stop in route_stops:
            stop_type = stop.get("stop_type", "")
            location  = stop.get("location", "")
            terminal  = stop.get("terminal", "")

            # Arrival event
            if stop.get("arrival"):
                events.append(make_event(
                    timestamp=  self._parse_date(stop["arrival"]),
                    status=     f"Arrived at {stop_type}",
                    location=   f"{location} - {terminal}" if terminal else location,
                    country=    self._extract_country(location),
                    event_type= "ACTUAL",
                    raw_code=   f"{stop_type}_ARR",
                ))

            # Departure event
            if stop.get("departure"):
                events.append(make_event(
                    timestamp=  self._parse_date(stop["departure"]),
                    status=     f"Departed from {stop_type}",
                    location=   f"{location} - {terminal}" if terminal else location,
                    country=    self._extract_country(location),
                    event_type= "ACTUAL",
                    raw_code=   f"{stop_type}_DEP",
                ))

        return events

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _find_stop_location(self, stops: list, stop_type: str) -> str:
        for stop in stops:
            if stop.get("stop_type", "").lower() == stop_type.lower():
                return stop.get("location", "")
        return ""

    def _find_stop_date(self, stops: list, stop_type: str, date_field: str) -> Optional[str]:
        for stop in stops:
            if stop.get("stop_type", "").lower() == stop_type.lower():
                raw = stop.get(date_field)
                return self._parse_date(raw) if raw else None
        return None

    def _extract_country(self, location: str) -> str:
        """Extract country from 'CITY, COUNTRY' format."""
        if "," in location:
            return location.split(",")[-1].strip()
        return ""

    def _route_to_dict(self, r) -> dict:
        return {
            "latitude":  r.latitude,
            "longitude": r.longitude,
            "port":      r.port,
            "port_name": r.port_name,
            "country":   r.country,
            "timestamp": r.timestamp,
        }

    def _parse_date(self, raw_date: Optional[str]) -> Optional[str]:
        """
        Normalize HMM date strings to ISO 8601.
        Input:  "2026-04-14 20:45"   (from HTML table)
        Output: "2026-04-14T20:45:00"
        """
        if not raw_date:
            return None
        raw_date = raw_date.strip()
        formats = [
            ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"),
            ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S"),
            ("%Y-%m-%d", "%Y-%m-%d"),
            ("%d/%m/%Y", "%Y-%m-%d"),
        ]
        for in_fmt, out_fmt in formats:
            try:
                return datetime.strptime(raw_date[:len(in_fmt)], in_fmt).strftime(out_fmt)
            except ValueError:
                continue
        return raw_date


# -------------------------------------------------------------------------
# Quick test
# -------------------------------------------------------------------------

if __name__ == "__main__":
    adapter = HMMAdapter()

    result = adapter.fetch("BCNA66532900")
    print(json.dumps(result, indent=2, default=str))
