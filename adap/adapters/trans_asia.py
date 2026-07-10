import re
import requests
import json
from datetime import datetime
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

from adap.base import BaseAdapter
from adap.schema import make_event, make_container


class TransAsiaAdapter(BaseAdapter):
    """
    Adapter for Trans Asia Line (TAL / TASFREIGHT tasnet portal).

    Flow (2 hops, cookie/session-based, NOT JSON — plain ASP.NET WebForms HTML):

      1. GET BLTracking.aspx?blno={BL_NO}
         -> establishes ASP.NET_SessionId cookie
         -> returns grid of containers under the BL (container no, type,
            tare wt, seal no, BL status)

      2. GET ContainerTracking.aspx?containerno={CONTAINER_NO}&blNo={BL_NO}
         (same session cookie) for EACH container found in step 1
         -> returns:
            - a tracking-events table (Port / ICD / Move Date / Description /
              Vessel / Voyage / Conn: Vessel / Conn: Voyage / Conn:Vess ETA)
            - an "Expected Routing" table (From / To / Vessel / Voyage / ETD / ETA)
            - a free-text line with GrossWt/TareWt/Payload

    No auth token needed beyond the session cookie picked up in step 1 —
    unlike MSC/HMM there's no Akamai/Imperva bot-protection on this host.
    """

    CARRIER_NAME = "TRANS_ASIA"

    BASE_URL = "http://182.72.192.230/TASFREIGHT/AppTasnet"

    HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,hi;q=0.7",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    }

    # (connect, read) — this host is a slow legacy IIS server; a single
    # sequential 30s-per-request budget across 3-4 containers blew past the
    # frontend's 45s abort. Keep the read timeout tight so a stuck request
    # fails fast instead of eating the whole budget.
    TIMEOUT = (10, 20)
    MAX_WORKERS = 4

    # -------------------------------------------------------------------------
    # API call
    # -------------------------------------------------------------------------

    def _call_api(self, bl_no: str) -> dict:
        session = requests.Session()
        session.headers.update(self.HEADERS)

        # ── Step 1: BL page → container list + session cookie ──────────────────
        bl_url = f"{self.BASE_URL}/BLTracking.aspx"
        try:
            bl_resp = session.get(
                bl_url,
                params={"blno": bl_no},
                timeout=self.TIMEOUT,
                verify=False,
            )
            bl_resp.raise_for_status()
        except requests.exceptions.Timeout:
            return {"error": "Trans Asia Line: BL page timeout"}
        except requests.exceptions.RequestException as e:
            return {"error": f"Trans Asia Line: BL page request failed: {str(e)}"}

        if not bl_resp.text.strip():
            return {"error": "Trans Asia Line: empty BL page response"}

        containers = self._parse_bl_grid(bl_resp.text)

        if not containers:
            # Some BLs may render the container link directly without the
            # grid parsing above picking it up — fall back to a raw regex
            # scan for container numbers embedded in the page.
            containers = self._fallback_container_scan(bl_resp.text)

        if not containers:
            return {"error": f"Trans Asia Line: no containers found for BL '{bl_no}'"}

        # ── Step 2: container detail page for each container ───────────────────
        # Done CONCURRENTLY, not sequentially — with several containers on
        # this BL, running them one after another (each up to TIMEOUT) is
        # what was blowing past the frontend's 45s abort. All containers
        # share the same session/cookie, so they can be fired in parallel.
        ct_url = f"{self.BASE_URL}/ContainerTracking.aspx"
        referer = f"{bl_url}?&blno={bl_no}"
        container_pages = {}

        def _fetch_container(cno: str):
            try:
                resp = session.get(
                    ct_url,
                    params={"containerno": cno, "blNo": bl_no},
                    headers={"Referer": referer},
                    timeout=self.TIMEOUT,
                    verify=False,
                )
                resp.raise_for_status()
                return cno, resp.text
            except requests.exceptions.RequestException:
                return cno, None

        with ThreadPoolExecutor(max_workers=min(self.MAX_WORKERS, len(containers))) as pool:
            futures = [pool.submit(_fetch_container, c["container_no"]) for c in containers]
            for future in as_completed(futures):
                cno, html = future.result()
                container_pages[cno] = html

        return {
            "bl_no": bl_no,
            "containers_meta": containers,
            "container_pages": container_pages,
        }

    # -------------------------------------------------------------------------
    # Step 1 parsing — BL grid
    # -------------------------------------------------------------------------

    def _parse_bl_grid(self, html: str) -> List[dict]:
        """
        Parse the BLTracking.aspx grid into a list of:
          {container_no, size, type, tare_wt, seal_no, bl_status}
        """
        soup = BeautifulSoup(html, "html.parser")
        out = []

        table = self._find_table_by_headers(
            soup, required_any=["container", "tare", "seal", "bl status"]
        )
        if table is None:
            return out

        rows = table.find_all("tr")
        if not rows:
            return out

        header_cells = [self._cell_text(c).lower() for c in rows[0].find_all(["th", "td"])]

        def col_idx(*needles):
            for i, h in enumerate(header_cells):
                if all(n in h for n in needles):
                    return i
            return None

        idx_container = col_idx("container")
        idx_type       = col_idx("type")
        idx_tare       = col_idx("tare")
        idx_seal       = col_idx("seal")
        idx_status     = col_idx("status")

        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells:
                continue

            # Container number is usually inside an <a>/postback link in col 0
            link = row.find("a")
            container_no = (link.get_text(strip=True) if link else
                             self._cell_text(cells[idx_container] if idx_container is not None and idx_container < len(cells) else cells[0]))
            container_no = container_no.strip().upper()

            if not re.match(r"^[A-Z]{4}\d{6,7}$", container_no):
                continue

            size_type = self._cell_text(cells[idx_type]) if idx_type is not None and idx_type < len(cells) else ""
            size, ctype = self._split_size_type(size_type)

            out.append({
                "container_no": container_no,
                "size": size,
                "type": ctype,
                "tare_wt": self._cell_text(cells[idx_tare]) if idx_tare is not None and idx_tare < len(cells) else "",
                "seal_no": self._cell_text(cells[idx_seal]) if idx_seal is not None and idx_seal < len(cells) else "",
                "bl_status": self._cell_text(cells[idx_status]) if idx_status is not None and idx_status < len(cells) else "",
            })

        return out

    def _fallback_container_scan(self, html: str) -> List[dict]:
        """Last-resort: scan raw HTML for container-number-shaped tokens."""
        found = sorted(set(re.findall(r"\b([A-Z]{4}\d{6,7})\b", html.upper())))
        return [{"container_no": c, "size": "", "type": "", "tare_wt": "", "seal_no": "", "bl_status": ""} for c in found]

    # -------------------------------------------------------------------------
    # Normalizer
    # -------------------------------------------------------------------------

    def _normalize(self, raw: dict, bl_no: str) -> dict:
        containers_meta = {c["container_no"]: c for c in raw.get("containers_meta", [])}
        container_pages = raw.get("container_pages", {})

        containers: List[dict] = []
        all_events: List[dict] = []

        pol = pod = vessel = voyage = ""
        etd = eta = None

        for cno, html in container_pages.items():
            meta = containers_meta.get(cno, {})

            if not html:
                containers.append(self._container_dict(
                    make_container(container_no=cno, size=meta.get("size", ""), type=meta.get("type", ""))
                ))
                continue

            soup = BeautifulSoup(html, "html.parser")

            events = self._parse_events_table(soup, cno)
            routing = self._parse_routing_table(soup)

            c_pol  = routing.get("from", "")
            c_pod  = routing.get("to", "")
            c_vsl  = routing.get("vessel", "")
            c_voy  = routing.get("voyage", "")
            c_etd  = routing.get("etd")
            c_eta  = routing.get("eta")

            # Use first container with routing info as the BL-level summary
            if not pol and c_pol:
                pol, pod, vessel, voyage, etd, eta = c_pol, c_pod, c_vsl, c_voy, c_etd, c_eta

            container_obj = make_container(
                container_no=cno,
                size=meta.get("size", ""),
                type=meta.get("type", ""),
                pol=c_pol,
                pod=c_pod,
                vessel=c_vsl,
                voyage=c_voy,
                etd=c_etd,
                eta=c_eta,
                events=events,
            )
            containers.append(self._container_dict(container_obj))
            all_events.extend([self._event_dict(e) for e in events])

        all_events.sort(key=lambda x: x["timestamp"] or "")

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
            "events":     all_events,
            "raw":        raw,
        }

    # -------------------------------------------------------------------------
    # Step 2 parsing — events + routing tables
    # -------------------------------------------------------------------------

    def _parse_events_table(self, soup: BeautifulSoup, container_no: str):
        table = self._find_table_by_headers(
            soup, required_any=["port", "move date", "description", "vessel", "voyage"]
        )
        if table is None:
            return []

        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        header_cells = [self._cell_text(c).lower() for c in rows[0].find_all(["th", "td"])]

        def col_idx(*needles):
            for i, h in enumerate(header_cells):
                if all(n in h for n in needles):
                    return i
            return None

        idx_port   = col_idx("port")
        idx_date   = col_idx("move", "date")
        idx_desc   = col_idx("description")
        idx_vessel = col_idx("vessel")
        idx_voyage = col_idx("voyage")

        events = []
        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            texts = [self._cell_text(c) for c in cells]
            if not any(texts):
                continue

            port   = texts[idx_port]   if idx_port   is not None and idx_port   < len(texts) else ""
            date_s = texts[idx_date]   if idx_date   is not None and idx_date   < len(texts) else ""
            desc   = texts[idx_desc]   if idx_desc   is not None and idx_desc   < len(texts) else ""
            vsl    = texts[idx_vessel] if idx_vessel is not None and idx_vessel < len(texts) else ""
            voy    = texts[idx_voyage] if idx_voyage is not None and idx_voyage < len(texts) else ""

            if not desc and not port:
                continue

            events.append(make_event(
                timestamp=  self._parse_ddmmyyyy(date_s),
                status=     desc,
                location=   port,
                country=    None,
                vessel=     vsl,
                voyage=     voy,
                event_type= "ACTUAL",
                raw_code=   None,
            ))

        return events

    def _parse_routing_table(self, soup: BeautifulSoup) -> dict:
        table = self._find_table_by_headers(
            soup, required_any=["from", "to", "vessel", "voyage", "etd", "eta"]
        )
        if table is None:
            return {}

        rows = table.find_all("tr")
        if len(rows) < 2:
            return {}

        header_cells = [self._cell_text(c).lower() for c in rows[0].find_all(["th", "td"])]

        def col_idx(name):
            for i, h in enumerate(header_cells):
                if h == name or name in h:
                    return i
            return None

        idx_from   = col_idx("from")
        idx_to     = col_idx("to")
        idx_vessel = col_idx("vessel")
        idx_voyage = col_idx("voyage")
        idx_etd    = col_idx("etd")
        idx_eta    = col_idx("eta")

        # First data row with a real "from" value is the routing summary —
        # trailing rows in this table are IGM/ITEM/CFS key-value junk, not
        # additional routing legs.
        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            texts = [self._cell_text(c) for c in cells]

            from_v = texts[idx_from] if idx_from is not None and idx_from < len(texts) else ""
            if not from_v or "igm" in from_v.lower() or "item" in from_v.lower() or "cfs" in from_v.lower():
                continue

            return {
                "from":   from_v,
                "to":     texts[idx_to]     if idx_to     is not None and idx_to     < len(texts) else "",
                "vessel": texts[idx_vessel] if idx_vessel is not None and idx_vessel < len(texts) else "",
                "voyage": texts[idx_voyage] if idx_voyage is not None and idx_voyage < len(texts) else "",
                "etd":    self._parse_free_date(texts[idx_etd]) if idx_etd is not None and idx_etd < len(texts) else None,
                "eta":    self._parse_free_date(texts[idx_eta]) if idx_eta is not None and idx_eta < len(texts) else None,
            }

        return {}

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _find_table_by_headers(self, soup: BeautifulSoup, required_any: list):
        """Return the first <table> whose header row contains a majority of required_any tokens."""
        best, best_score = None, 0
        for table in soup.find_all("table"):
            first_row = table.find("tr")
            if not first_row:
                continue
            header_text = " ".join(
                self._cell_text(c).lower() for c in first_row.find_all(["th", "td"])
            )
            score = sum(1 for token in required_any if token in header_text)
            if score > best_score:
                best, best_score = table, score

        # Require at least 2 matching header tokens to avoid false positives
        return best if best_score >= 2 else None

    def _cell_text(self, cell) -> str:
        return cell.get_text(strip=True) if cell is not None else ""

    def _split_size_type(self, size_type: str):
        """'40HC' -> ('40', 'HC'); '20GP' -> ('20', 'GP')."""
        size_type = (size_type or "").strip().upper()
        m = re.match(r"(\d{2})([A-Z]+)?", size_type)
        if not m:
            return "", ""
        return m.group(1) or "", m.group(2) or ""

    def _container_dict(self, c) -> dict:
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
            "events":       [self._event_dict(e) for e in c.events],
        }

    def _event_dict(self, e) -> dict:
        return {
            "timestamp":  e.timestamp,
            "status":     e.status,
            "location":   e.location,
            "country":    e.country,
            "vessel":     e.vessel,
            "voyage":     e.voyage,
            "event_type": e.event_type,
            "raw_code":   e.raw_code,
        }

    def _parse_ddmmyyyy(self, raw_date: Optional[str]) -> Optional[str]:
        """'07-05-2026' -> '2026-05-07T00:00:00'."""
        if not raw_date:
            return None
        try:
            return datetime.strptime(raw_date.strip(), "%d-%m-%Y").strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None

    def _parse_free_date(self, raw_date: Optional[str]) -> Optional[str]:
        """
        Handles the loose date formats seen in the routing table:
          'Sailed On 25-04-2026'   -> 2026-04-25T00:00:00
          'Arrived On Apr 27 2026' -> 2026-04-27T00:00:00
          '25-04-2026'             -> 2026-04-25T00:00:00
        """
        if not raw_date:
            return None
        raw_date = raw_date.strip()

        m = re.search(r"(\d{2})-(\d{2})-(\d{4})", raw_date)
        if m:
            dd, mm, yyyy = m.groups()
            try:
                return datetime(int(yyyy), int(mm), int(dd)).strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass

        m = re.search(r"([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})", raw_date)
        if m:
            try:
                return datetime.strptime(" ".join(m.groups()), "%b %d %Y").strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass

        return None


# -------------------------------------------------------------------------
# Quick test
# -------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    adapter = TransAsiaAdapter()
    result = adapter.fetch("TALTLS03086437")
    print(json.dumps(result, indent=2, default=str))