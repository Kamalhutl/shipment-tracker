import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

TRACKING_URL = "https://www.maersk.com/tracking/{bl}"
WAIT         = 30

def _wait_for(driver, css, timeout=WAIT):
    try:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css))
        )
    except TimeoutException:
        return None

def _js_text(driver, css):
    try:
        return driver.execute_script(
            "var el = document.querySelector(arguments[0]);"
            "return el ? el.innerText : '';",
            css
        ).strip()
    except Exception:
        return ""

def _dismiss_cookies(driver):
    for sel in [
        "[data-test='coi-allow-all-button']",
        "[data-test='coi-allow-all-button-mobile']",
        "#onetrust-accept-btn-handler",
    ]:
        try:
            btn = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
            )
            btn.click()
            time.sleep(0.5)
            return
        except Exception:
            continue

def _maersk_not_found(driver):
    for sel in ["[data-test='track-error-heading']", "[data-test='track-error-text']"]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el and el.text.strip():
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
        "Latest Status": "",
    }

    bl = bl.strip()
    url = TRACKING_URL.format(bl=bl)

    driver.get(url)
    time.sleep(2)

    _dismiss_cookies(driver)

    container_location_el = _wait_for(driver, "[data-test='container-location']", WAIT)

    if container_location_el is None:
        if _maersk_not_found(driver):
            result["Latest Status"] = "BL not found on Maersk"
            return result

        try:
            with open(f"debug_{bl}.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
        except Exception:
            pass

        result["Latest Status"] = "Timeout - tracking data did not load"
        return result

    try:
        body_lines = [
            l.strip()
            for l in driver.find_element(By.TAG_NAME, "body").text.split("\n")
            if l.strip()
        ]
        from_idx = next((i for i, l in enumerate(body_lines) if l == "From"), None)
        to_idx   = next((i for i, l in enumerate(body_lines) if l == "To"),   None)
        if from_idx is not None and from_idx + 1 < len(body_lines):
            result["POL"] = body_lines[from_idx + 1]
        if to_idx is not None and to_idx + 1 < len(body_lines):
            result["POD"] = body_lines[to_idx + 1]
    except Exception:
        pass

    try:
        hdr = driver.find_element(By.CSS_SELECTOR, "[data-test^='container-header-']")
        result["Container No"] = hdr.text.split("|")[0].strip()
    except Exception:
        pass

    try:
        eta_el = driver.find_element(By.CSS_SELECTOR, "[data-test='container-eta']")
        for line in reversed(eta_el.text.split("\n")):
            line = line.strip()
            if line and any(c.isdigit() for c in line):
                result["ATA"] = line
                break
    except Exception:
        pass

    if not result["ATA"]:
        for line in reversed(_js_text(driver, "[data-test='container-eta']").split("\n")):
            line = line.strip()
            if line and any(c.isdigit() for c in line):
                result["ATA"] = line
                break

    sublabel = ""
    try:
        loc_lines = [l.strip() for l in container_location_el.text.split("\n") if l.strip()]
        for i, line in enumerate(loc_lines):
            if "latest event" in line.lower():
                if i + 1 < len(loc_lines):
                    sublabel = loc_lines[i + 1]
                break
            elif "•" in line:
                sublabel = line
                break
        if not sublabel and loc_lines:
            sublabel = loc_lines[-1]
    except Exception:
        pass

    if not sublabel:
        raw_lines = [
            l.strip()
            for l in _js_text(driver, "[data-test='container-location']").split("\n")
            if l.strip()
        ]
        for i, line in enumerate(raw_lines):
            if "latest event" in line.lower() and i + 1 < len(raw_lines):
                sublabel = raw_lines[i + 1]
                break
        if not sublabel and raw_lines:
            sublabel = raw_lines[-1]

    result["Latest Status"] = sublabel

    if "•" in sublabel:
        result["Vessel"] = sublabel.split("•")[0].strip()
    elif sublabel:
        result["Vessel"] = sublabel

    return result
