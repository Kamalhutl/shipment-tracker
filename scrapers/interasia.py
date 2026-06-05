"""
scrapers/interasia.py  –  fixed v2
"""

import time
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

TRACKING_URL = "https://www.interasia.cc/Service/Form?servicetype=0"
WAIT         = 20


def _recover_window(driver) -> bool:
    """Ensure driver has a live window to work with."""
    try:
        _ = driver.current_url
        return True
    except Exception:
        pass
    try:
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[-1])
            return True
    except Exception:
        pass
    return False


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

    # ── 1. Window recovery ────────────────────────────────────────────────────
    if not _recover_window(driver):
        result["Latest Status"] = "Error: No browser window available"
        return result

    # ── 2. Open tracking form ─────────────────────────────────────────────────
    driver.get(TRACKING_URL)
    time.sleep(5)

    if not _recover_window(driver):
        result["Latest Status"] = "Error: Window closed after navigation"
        return result

    # ── 3. Find BL input and enter BL number ──────────────────────────────────
    # From screenshot: input[name="query"] inside a contenteditable div
    # Also try regular input
    bl_entered = False

    # Try contenteditable div first (seen in screenshot)
    for css in [
        "div[contenteditable='true']",
        "input[name='query']",
        "input[type='text']",
        "input[placeholder*='B/L']",
        "input[placeholder*='Container']",
        "input[placeholder*='BL']",
    ]:
        try:
            el = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, css))
            )
            el.click()
            time.sleep(0.5)
            # Clear existing content
            el.send_keys("\ue009" + "a")   # Ctrl+A
            el.send_keys("\ue017")          # Delete
            el.send_keys(bl)
            bl_entered = True
            break
        except Exception:
            continue

    if not bl_entered:
        result["Latest Status"] = "Error: BL input not found"
        return result

    time.sleep(0.5)

    # ── 4. Submit form ────────────────────────────────────────────────────────
    submitted = False
    for css in [
        "button[type='submit']",
        "input[type='submit']",
        "button.search",
        "button.btn",
        "button[class*='search']",
        "button[class*='submit']",
        ".footer-group button",
    ]:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, css)
            if btn.is_displayed():
                btn.click()
                submitted = True
                break
        except Exception:
            continue

    if not submitted:
        # Try pressing Enter on the input
        try:
            from selenium.webdriver.common.keys import Keys
            el = driver.find_element(By.CSS_SELECTOR,
                "div[contenteditable='true'], input[name='query']")
            el.send_keys(Keys.RETURN)
            submitted = True
        except Exception:
            pass

    if not submitted:
        result["Latest Status"] = "Error: Could not submit form"
        return result

    time.sleep(3)

    # ── 5. Wait for results ───────────────────────────────────────────────────
    try:
        WebDriverWait(driver, WAIT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                "div.m-table-group table, div.m-list-group table, table tbody tr"))
        )
    except TimeoutException:
        result["Latest Status"] = "No result found"
        return result

    time.sleep(2)

    # ── 6. Check if no result ─────────────────────────────────────────────────
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        if "no result" in body_text.lower() or "not found" in body_text.lower():
            result["Latest Status"] = "No result found"
            return result
    except Exception:
        pass

    # ── 7. POL / POD / ETA from route table ───────────────────────────────────
    # <div class="m-table-group">
    #   <table>
    #     <thead><tr>
    #       <th>Loading Port</th><th>Discharging Port</th>
    #       <th>Estimated Departure Date</th><th>Estimated Arrival Date</th>
    #     </tr></thead>
    #     <tbody><tr>
    #       <td class="white">TWTXG(TAICHUNG)</td>
    #       <td class="white">INTUT(TUTICORIN (NEW TUTICORIN))</td>
    #       ...
    #     </tr></tbody>
    #   </table>
    # </div>
    try:
        tables = driver.find_elements(By.CSS_SELECTOR,
            "div.m-table-group table, table")
        for table in tables:
            headers = table.find_elements(By.TAG_NAME, "th")
            h_texts = [h.text.strip().lower() for h in headers]
            if not any("loading" in h or "discharging" in h for h in h_texts):
                continue

            pol_idx = next((i for i, h in enumerate(h_texts) if "loading"    in h), None)
            pod_idx = next((i for i, h in enumerate(h_texts) if "discharging" in h), None)
            eta_idx = next((i for i, h in enumerate(h_texts) if "arrival"    in h), None)

            rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
            if not rows:
                continue

            cells = rows[0].find_elements(By.TAG_NAME, "td")
            if pol_idx is not None and pol_idx < len(cells):
                result["POL"] = cells[pol_idx].text.strip()
            if pod_idx is not None and pod_idx < len(cells):
                result["POD"] = cells[pod_idx].text.strip()
            if eta_idx is not None and eta_idx < len(cells):
                result["ATA"] = cells[eta_idx].text.strip()
            break

    except Exception:
        pass

    # ── 8. Container No ───────────────────────────────────────────────────────
    container = ""
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        m = re.search(r'Container\s*No[^A-Z]*([A-Z]{4}\d{7})', body_text)
        if m:
            container = m.group(1)
    except Exception:
        pass

    if not container:
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            m = re.search(r'\b([A-Z]{4}\d{7})\b', body_text)
            if m:
                container = m.group(1)
        except Exception:
            pass

    result["Container No"] = container

    # ── 9. Events table — Latest Status + Vessel ──────────────────────────────
    status = ""
    vessel = ""
    try:
        event_tables = driver.find_elements(By.CSS_SELECTOR,
            "div.m-list-group table, table")
        events_table = None
        for table in event_tables:
            headers = table.find_elements(By.TAG_NAME, "th")
            h_texts = [h.text.strip().lower() for h in headers]
            if any("event" in h or "description" in h for h in h_texts):
                events_table = table
                break

        if events_table:
            headers = events_table.find_elements(By.TAG_NAME, "th")
            h_texts = [h.text.strip().lower() for h in headers]

            desc_idx   = next((i for i, h in enumerate(h_texts)
                               if "description" in h or "event" in h), None)
            vessel_idx = next((i for i, h in enumerate(h_texts)
                               if "vessel" in h), None)
            voyage_idx = next((i for i, h in enumerate(h_texts)
                               if "voyage" in h), None)
            date_idx   = next((i for i, h in enumerate(h_texts)
                               if "date" in h), None)

            rows = events_table.find_elements(By.CSS_SELECTOR, "tbody tr")
            if rows:
                cells = rows[0].find_elements(By.TAG_NAME, "td")
                if desc_idx is not None and desc_idx < len(cells):
                    status = cells[desc_idx].text.strip()
                if vessel_idx is not None and vessel_idx < len(cells):
                    vessel = cells[vessel_idx].text.strip()
                if voyage_idx is not None and voyage_idx < len(cells) and vessel:
                    voyage = cells[voyage_idx].text.strip()
                    if voyage:
                        vessel += f" / {voyage}"
                if date_idx is not None and date_idx < len(cells) and not result["ATA"]:
                    result["ATA"] = cells[date_idx].text.strip()

    except Exception:
        pass

    result["Latest Status"] = status
    result["Vessel"]        = vessel

    return result