"""
scrapers/hapag.py  –  fixed
"""

import re
import time
import logging

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

log = logging.getLogger(__name__)

TRACKING_URL = (
    "https://www.hapag-lloyd.com/en/online-business/track/track-by-booking-solution.html"
)
WAIT = 25


def _accept_cookies(driver):
    for css in [
        "#onetrust-accept-btn-handler",
        "button#accept-all",
        "[class*='accept-all']",
        "button[data-action='accept']",
        ".cookie-accept",
    ]:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, css)
            if btn.is_displayed():
                btn.click()
                time.sleep(1)
                return
        except Exception:
            pass


def _wait_for_cloudflare(driver, timeout=60):
    for _ in range(timeout):
        title = (driver.title or "").lower()
        if "just a moment" not in title and "checking" not in title:
            return True
        time.sleep(1)
    return False


def _cell_text(driver, cell) -> str:
    t = cell.text.strip()
    if not t:
        try:
            t = driver.execute_script("return arguments[0].innerText;", cell)
            if t:
                t = t.strip()
        except Exception:
            t = ""
    return t or ""


def _is_container_list(driver, table) -> bool:
    """Returns True if this is the container selection list, not the events table."""
    try:
        text = table.text.upper()
        # Container list always has 'CONTAINER NO.' header and type codes like 45GP
        if "CONTAINER NO." in text and re.search(r'\b\d{2}[A-Z]{2}\b', text):
            return True
        # Also check: no STATUS + PLACE OF ACTIVITY columns (those are events table)
        if "STATUS" not in text and "PLACE OF ACTIVITY" not in text:
            if re.search(r'\b[A-Z]{4}\s*\d{7}\b', text):
                return True
    except Exception:
        pass
    return False


def _extract_containers_from_table(driver, table) -> list:
    """Extract all container numbers from the container list table text."""
    containers = []
    try:
        text = table.text
        # Match patterns like: HAMU 3920481  or  FCIU7263420
        matches = re.findall(r'\b([A-Z]{4})\s*(\d{7})\b', text)
        for prefix, num in matches:
            containers.append(f"{prefix}{num}")
        log.info("[HAPAG] Containers found in table: %s", containers)
    except Exception as e:
        log.error("[HAPAG] Error extracting containers: %s", e)
    return containers


def _navigate_to_container(driver, container: str) -> bool:
    """
    Navigate directly to the container events page via URL.
    URL format observed: ?view=S8510&container=HAMU++3920481
    """
    try:
        # Format: HAMU3920481 → HAMU++3920481
        formatted = container[:4] + "++" + container[4:]
        url = f"{TRACKING_URL}?view=S8510&container={formatted}"
        log.info("[HAPAG] Navigating to container URL: %s", url)
        driver.get(url)
        time.sleep(5)
        return True
    except Exception as e:
        log.error("[HAPAG] URL navigation failed: %s", e)
        return False


def _container_from_url(driver) -> str:
    url = driver.current_url
    m = re.search(r'container=([A-Z]{4}\+*\d+)', url)
    if m:
        return m.group(1).replace("+", "").replace(" ", "")
    return ""


def _container_from_body(driver) -> str:
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
        m = re.search(r'\b([A-Z]{4}\s*\d{7})\b', body)
        if m:
            return m.group(1).replace(" ", "")
    except Exception:
        pass
    return ""


def _get_data_table(driver):
    """Find the events table (has STATUS + PLACE OF ACTIVITY columns)."""
    tbls = driver.find_elements(By.CSS_SELECTOR, "table[summary='LabelledComponentTable']")
    for tbl in tbls:
        h = tbl.text.upper()
        if "STATUS" in h and "PLACE OF ACTIVITY" in h:
            return tbl
    # Fallback: last table
    return tbls[-1] if tbls else None


def scrape(driver, bl: str) -> dict:
    result = {
        "POL":           "",
        "POD":           "",
        "Container No":  "",
        "Vessel":        "",
        "ATA":           "",
        "FND":           "",
        "Latest Status": "",
    }

    bl = bl.strip()
    log.info("[HAPAG] Scraping BL: %s", bl)

    # ── 1. Open page ──────────────────────────────────────────────────────────
    try:
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[-1])
    except Exception:
        pass

    driver.get(TRACKING_URL)
    time.sleep(5)

    if not _wait_for_cloudflare(driver, timeout=60):
        log.error("[HAPAG] Cloudflare blocked")
        result["Latest Status"] = "Error: Cloudflare blocked"
        return result

    log.info("[HAPAG] Page loaded: %s", driver.title)
    _accept_cookies(driver)
    time.sleep(1)

    # ── 2. Enter BL ───────────────────────────────────────────────────────────
    bl_input = None
    for css in [
        "input[id*='hl16']",
        "input[name*='hl16']",
        "input[id*='blNo']",
        "input[placeholder*='Lading']",
        "input[id*='bookingNo']",
    ]:
        try:
            bl_input = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, css))
            )
            log.info("[HAPAG] Found BL input via: %s", css)
            break
        except TimeoutException:
            continue

    if bl_input is None:
        log.error("[HAPAG] BL input not found")
        result["Latest Status"] = "Error: BL input not found"
        return result

    bl_input.clear()
    bl_input.send_keys(bl)
    time.sleep(0.5)

    # ── 3. Click Search ───────────────────────────────────────────────────────
    searched = False
    for css in [
        "input[type='submit'][value*='Search']",
        "button[id*='search']",
        "button[class*='search']",
        "input[type='submit']",
        "button[type='submit']",
    ]:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, css)
            if btn.is_displayed():
                btn.click()
                searched = True
                log.info("[HAPAG] Clicked search via: %s", css)
                break
        except Exception:
            pass

    if not searched:
        bl_input.send_keys(Keys.RETURN)
        log.info("[HAPAG] Submitted via Enter key")

    # ── 4. Wait for table ─────────────────────────────────────────────────────
    try:
        WebDriverWait(driver, WAIT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                "table[summary='LabelledComponentTable'] tbody tr"))
        )
        time.sleep(3)
        log.info("[HAPAG] Table loaded")
    except TimeoutException:
        log.warning("[HAPAG] Timed out waiting for table")
        time.sleep(5)

    # ── 5. Detect container list → extract first container → navigate via URL ─
    first_table = _get_data_table(driver)
    if not first_table:
        # Try any LabelledComponentTable
        tbls = driver.find_elements(By.CSS_SELECTOR, "table[summary='LabelledComponentTable']")
        first_table = tbls[-1] if tbls else None

    container_no = ""

    if first_table and _is_container_list(driver, first_table):
        log.info("[HAPAG] Container list detected — extracting containers")
        containers = _extract_containers_from_table(driver, first_table)

        if containers:
            container_no = containers[0]
            log.info("[HAPAG] Using first container: %s", container_no)
            navigated = _navigate_to_container(driver, container_no)
            if navigated:
                # Wait for events table to load
                try:
                    WebDriverWait(driver, WAIT).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR,
                            "table[summary='LabelledComponentTable'] tbody tr"))
                    )
                    time.sleep(3)
                    log.info("[HAPAG] Events page loaded")
                except TimeoutException:
                    log.warning("[HAPAG] Timed out waiting for events table after navigation")
                    time.sleep(5)
            else:
                log.warning("[HAPAG] URL navigation failed")
        else:
            log.warning("[HAPAG] No containers found in list")
    else:
        log.info("[HAPAG] No container list detected — already on events page")

    # ── 6. Last Movement (POD + ATA fallback) ─────────────────────────────────
    _ata_from_last_movement = ""
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "span.nonEditableContent")
        for el in els:
            txt = el.text.strip()
            if "container" in txt.lower() and "arrived" in txt.lower():
                log.info("[HAPAG] Last movement: %s", txt)
                # "The container arrived in TUTICORIN at 2026-01-19 ."
                m = re.search(
                    r'arrived in ([A-Z][A-Z\s,]+?) at\s+([\d\-]+)',
                    txt, re.IGNORECASE
                )
                if m:
                    if not result["POD"]:
                        result["POD"] = m.group(1).strip().upper()
                    _ata_from_last_movement = m.group(2).strip()
                break
    except Exception as e:
        log.warning("[HAPAG] Last movement parse error: %s", e)

    # ── 7. Parse events table ─────────────────────────────────────────────────
    # Actual columns (from screenshots):
    # [empty td] | Status | Place of Activity | Date | Time | Transport | Voyage No.
    # → cell indices:   1         2               3      4        5            6

    data_table = _get_data_table(driver)

    status = ""
    pol    = ""
    ata    = ""
    vessel = ""

    try:
        if data_table:
            trs = data_table.find_elements(By.CSS_SELECTOR, "tbody tr")
            log.info("[HAPAG] Events table rows: %d", len(trs))

            for tr in trs:
                cells = tr.find_elements(By.TAG_NAME, "td")

                # Need at least 5 cells: [spacer, status, place, date, time]
                if len(cells) < 5:
                    continue

                # cells[0] is an empty spacer td — skip it
                s_text      = _cell_text(driver, cells[1])
                place_text  = _cell_text(driver, cells[2])
                date_text   = _cell_text(driver, cells[3])
                time_text   = _cell_text(driver, cells[4])
                trans_text  = _cell_text(driver, cells[5]) if len(cells) > 5 else ""
                voyage_text = _cell_text(driver, cells[6]) if len(cells) > 6 else ""

                log.info(
                    "[HAPAG] Row → status=%s | place=%s | date=%s | time=%s | transport=%s | voyage=%s",
                    s_text, place_text, date_text, time_text, trans_text, voyage_text
                )

                # Skip blank or header rows
                if not s_text or s_text.lower() in ("status", "type"):
                    continue

                # First valid row = latest status
                if not status:
                    status = s_text
                    result["Latest Status"] = status
                    log.info("[HAPAG] Latest status: %s", status)

                s_lower = s_text.lower()

                # ATA = date of Discharged event
                if not ata and "discharged" in s_lower:
                    ata = f"{date_text} {time_text}".strip()
                    if not vessel and trans_text:
                        vessel = trans_text
                        if voyage_text:
                            vessel += f" / {voyage_text}"
                    log.info("[HAPAG] ATA (discharged): %s", ata)

                # Vessel from Loaded event
                if not vessel and "loaded" in s_lower and trans_text:
                    vessel = trans_text
                    if voyage_text:
                        vessel += f" / {voyage_text}"
                    log.info("[HAPAG] Vessel (loaded): %s", vessel)

                # POL from Loaded event
                if not pol and "loaded" in s_lower and place_text:
                    pol = place_text
                    log.info("[HAPAG] POL: %s", pol)

                # POD fallback from Arrived / Discharged event
                if not result["POD"] and (
                    "arrived" in s_lower or "discharged" in s_lower
                ) and place_text:
                    result["POD"] = place_text
                    log.info("[HAPAG] POD (from events): %s", result['POD'])

        else:
            log.warning("[HAPAG] No events table found after navigation")

    except Exception as exc:
        log.error("[HAPAG] Error parsing events table: %s", exc)

    # ── 8. Container No ───────────────────────────────────────────────────────
    # Prefer what we already extracted from container list
    if not container_no:
        container_no = _container_from_url(driver)
    if not container_no:
        container_no = _container_from_body(driver)
    log.info("[HAPAG] Container: %s", container_no)

    # ── 9. ATA fallback from Last Movement ────────────────────────────────────
    if not ata and _ata_from_last_movement:
        ata = _ata_from_last_movement
        log.info("[HAPAG] ATA (last movement fallback): %s", ata)

    # ── 10. Fill result ───────────────────────────────────────────────────────
    result["POL"]          = pol
    result["ATA"]          = ata
    result["Vessel"]       = vessel
    result["Container No"] = container_no

    log.info("[HAPAG] Final result: %s", result)
    return result