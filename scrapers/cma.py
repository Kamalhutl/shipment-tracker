import re
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

TRACKING_URL = "https://www.cma-cgm.com/ebusiness/tracking/search"
WAIT         = 20

def _wait_for_visible(driver, css, timeout=WAIT):
    try:
        return WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, css))
        )
    except TimeoutException:
        return None

def _js_click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", element)

def _dismiss_cookies(driver):
    for sel in [
        "#didomi-notice-agree-button",
        ".didomi-continue-without-agreeing",
        "button[id*='accept']",
        ".cookie-accept",
    ]:
        try:
            btn = WebDriverWait(driver, 4).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
            )
            _js_click(driver, btn)
            time.sleep(0.5)
            return
        except Exception:
            continue

def _label_value(body_lines, *labels):
    for i, line in enumerate(body_lines):
        if line.strip() in labels:
            for j in range(i + 1, min(i + 4, len(body_lines))):
                val = body_lines[j].strip()
                if val and val not in labels:
                    return val
    return ""

def scrape(driver, bl: str) -> dict:
    result = {
        "POL":           "",
        "POD":           "",
        "Container No":  "",
        "Vessel":        "",
        "ATA":           "",
        "Latest Status": "",
    }

    bl = bl.strip()

    driver.get(TRACKING_URL)
    time.sleep(3)
    _dismiss_cookies(driver)
    time.sleep(1)

    inp = _wait_for_visible(driver, "input#Reference", timeout=15)
    if inp is None:
        inp = _wait_for_visible(driver, "input[name='SearchViewModelReference']", timeout=10)
    if inp is None:
        inp = _wait_for_visible(driver, "input[name='Reference']", timeout=10)
    if inp is None:
        result["Latest Status"] = "Could not find tracking input on page"
        return result

    _js_click(driver, inp)
    time.sleep(0.3)
    inp.clear()
    time.sleep(0.2)
    inp.send_keys(bl)
    time.sleep(0.5)

    submitted = False
    for btn_sel in ["button#btnTracking", "button[name='search']", ".o-button.primary"]:
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, btn_sel))
            )
            _js_click(driver, btn)
            submitted = True
            break
        except Exception:
            pass
    if not submitted:
        inp.send_keys(Keys.RETURN)

    table_appeared = _wait_for_visible(driver, "table.k-grid-table", timeout=25)
    if table_appeared is None:
        table_appeared = _wait_for_visible(driver, ".k-grid tbody tr", timeout=10)
    if table_appeared is None:
        time.sleep(5)

    time.sleep(2)

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        match = re.search(r'\b([A-Z]{4}\d{7})\b', body_text)
        if match:
            result["Container No"] = match.group(1)
    except Exception:
        pass

    try:
        body_lines = driver.find_element(By.TAG_NAME, "body").text.split("\n")
        result["POL"] = _label_value(body_lines, "POL", "Port of Loading", "Port of Load")
        result["POD"] = _label_value(body_lines, "POD", "Port of Discharge")
    except Exception:
        pass

    def _cell_text(row, css):
        try:
            return row.find_element(By.CSS_SELECTOR, css).text.strip()
        except Exception:
            return ""

    try:
        current_rows = driver.find_elements(By.CSS_SELECTOR,
            "table.k-grid-table tbody tr.current, "
            "table.k-grid-table tbody tr.k-master-row.current"
        )
        all_rows = driver.find_elements(By.CSS_SELECTOR,
            "table.k-grid-table tbody tr.k-master-row, "
            "table.k-grid-table tbody tr[aria-rowindex]"
        )

        target_rows = current_rows if current_rows else all_rows

        for row in target_rows:
            date_text = ""
            try:
                cal = row.find_element(By.CSS_SELECTOR, "td.date span.calendar")
                date_text = cal.text.strip()
                try:
                    t = row.find_element(By.CSS_SELECTOR, "td.date span.time")
                    if t.text.strip():
                        date_text += " " + t.text.strip()
                except Exception:
                    pass
            except Exception:
                date_text = _cell_text(row, "td.date")

            status_text = ""
            for status_sel in [
                "td .status-label", "td [class*='status']",
                "td span[class*='badge']", "td .o-status",
            ]:
                try:
                    status_text = row.find_element(By.CSS_SELECTOR, status_sel).text.strip()
                    if status_text:
                        break
                except Exception:
                    pass

            location = _cell_text(row, "td.location")
            if not location:
                location = _cell_text(row, "td.location-col")

            vessel = _cell_text(row, "td.vesselVoyage")
            if not vessel:
                vessel = _cell_text(row, "td[class*='vessel']")

            if date_text:
                result["ATA"] = date_text
                if status_text:
                    result["Latest Status"] = status_text
                elif location:
                    result["Latest Status"] = location
                if vessel:
                    result["Vessel"] = vessel
                break

    except Exception:
        pass

    if not result["Latest Status"]:
        try:
            for badge_sel in [
                "[class*='status-label']",
                "[class*='cargo-status']",
                "[class*='shipment-status']",
                ".o-status",
            ]:
                els = driver.find_elements(By.CSS_SELECTOR, badge_sel)
                for el in els:
                    t = el.text.strip()
                    if t and len(t) > 2:
                        result["Latest Status"] = t
                        break
                if result["Latest Status"]:
                    break
        except Exception:
            pass

    if not result["ATA"]:
        try:
            raw = driver.execute_script(
                "var els = document.querySelectorAll('td.date span.calendar');"
                "for(var i=0;i<els.length;i++){ if(els[i].innerText.trim()) return els[i].innerText.trim(); }"
                "return '';"
            )
            if raw:
                result["ATA"] = raw.strip()
        except Exception:
            pass

    return result
