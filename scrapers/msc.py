import time
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

TRACKING_URL = "https://www.msc.com/en/track-a-shipment"
WAIT         = 45

def _wait_for(driver, css, timeout=WAIT):
    try:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css))
        )
    except TimeoutException:
        return None

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
    try:
        driver.execute_script("""
            var ts = new Date().toISOString();
            document.cookie = 'OptanonAlertBoxClosed=' + ts + '; path=/';
            document.cookie = 'OptanonConsent=isGpcEnabled=0&datestamp=' + ts
                + '&version=202509.1.0&browserGpcFlag=0&isIABGlobal=false'
                + '&hosts=&consentId=abc123&interactionCount=1&isAnonUser=1'
                + '&landingPath=NotLandingPage'
                + '&groups=C0001%3A1%2CC0002%3A1%2CC0003%3A1%2CC0004%3A1'
                + '&geolocation=IN%3BMH&AwaitingReconsent=false; path=/';
        """)
    except Exception:
        pass

def _label_value(body_lines, label):
    for i, line in enumerate(body_lines):
        if line.strip().lower() == label.lower():
            for j in range(i + 1, min(i + 4, len(body_lines))):
                val = body_lines[j].strip()
                if val and val.lower() != label.lower():
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
    time.sleep(2)
    _dismiss_cookies(driver)
    time.sleep(1)

    inp = _wait_for_visible(driver, "input#trackingNumber", timeout=20)
    if inp is None:
        inp = _wait_for_visible(driver, "input[data-type='search']", timeout=10)
    if inp is None:
        result["Latest Status"] = "Could not find tracking input on page"
        return result

    _js_click(driver, inp)
    time.sleep(0.3)
    inp.clear()
    time.sleep(0.2)
    inp.send_keys(bl)
    time.sleep(1.0)

    submitted = False
    for btn_sel in [
        "button.msc-cta-icon-simple.msc-search-autocomplete__search",
        ".msc-search-autocomplete__search",
        "button[class*='autocomplete'][class*='search']",
    ]:
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

    data_appeared = False
    for data_sel in [
        ".msc-flow-tracking__bar",
        ".msc-flow-tracking__heading",
        "[class*='tracking__result']",
    ]:
        if _wait_for_visible(driver, data_sel, timeout=30):
            data_appeared = True
            break

    if not data_appeared:
        time.sleep(5)
        body = driver.find_element(By.TAG_NAME, "body").text
        if "No results" in body or bl not in body:
            result["Latest Status"] = "No tracking data found for this BL"
            return result

    time.sleep(3)

    try:
        body_lines = driver.find_element(By.TAG_NAME, "body").text.split("\n")

        pol = _label_value(body_lines, "Port of Load")
        if not pol:
            pol = _label_value(body_lines, "Shipped From")
        result["POL"] = pol

        pod = _label_value(body_lines, "Port of Discharge")
        if not pod:
            pod = _label_value(body_lines, "Shipped To")
        result["POD"] = pod

    except Exception:
        pass

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        match = re.search(r'\b([A-Z]{4}\d{7})\b', body_text)
        if match:
            result["Container No"] = match.group(1)
    except Exception:
        pass

    try:
        bars = driver.find_elements(By.CSS_SELECTOR, ".msc-flow-tracking__bar")
        for bar in bars:
            try:
                _js_click(driver, bar)
                time.sleep(0.8)
            except StaleElementReferenceException:
                continue
        _wait_for_visible(driver, ".msc-flow-tracking__steps", timeout=10)
        time.sleep(1.5)
    except Exception:
        pass

    def first_nonempty(cells):
        for c in cells:
            try:
                t = c.text.strip()
                if t:
                    return t
            except StaleElementReferenceException:
                continue
        return ""

    try:
        date_cells   = driver.find_elements(By.CSS_SELECTOR,
            ".msc-flow-tracking__steps .msc-flow-tracking__cell--two span.data-value")
        loc_cells    = driver.find_elements(By.CSS_SELECTOR,
            ".msc-flow-tracking__steps .msc-flow-tracking__cell--three span.data-value")
        desc_cells   = driver.find_elements(By.CSS_SELECTOR,
            ".msc-flow-tracking__steps .msc-flow-tracking__cell--four span.data-value")
        vessel_cells = driver.find_elements(By.CSS_SELECTOR,
            ".msc-flow-tracking__steps .msc-flow-tracking__cell--five span.data-value")

        ata      = first_nonempty(date_cells)
        location = first_nonempty(loc_cells)
        desc     = first_nonempty(desc_cells)
        vessel   = first_nonempty(vessel_cells)

        if ata:
            result["ATA"] = ata

        parts = [p for p in [desc, location, ata] if p]
        if parts:
            result["Latest Status"] = " • ".join(parts)

        if vessel and vessel.upper() not in ("LADEN", "EMPTY", ""):
            for vl in [l.strip() for l in vessel.split("\n") if l.strip()]:
                if vl.upper() not in ("LADEN", "EMPTY", ""):
                    result["Vessel"] = vl
                    break

    except Exception:
        pass

    if not result["Latest Status"]:
        try:
            raw = driver.execute_script(
                "var els = document.querySelectorAll("
                "'.msc-flow-tracking__steps .msc-flow-tracking__cell--four span.data-value');"
                "for(var i=0;i<els.length;i++){ if(els[i].innerText.trim()) return els[i].innerText.trim(); }"
                "return '';"
            )
            if raw:
                result["Latest Status"] = raw.split("\n")[0].strip()
        except Exception:
            pass

    if not result["ATA"]:
        try:
            raw = driver.execute_script(
                "var els = document.querySelectorAll("
                "'.msc-flow-tracking__steps .msc-flow-tracking__cell--two span.data-value');"
                "for(var i=0;i<els.length;i++){ if(els[i].innerText.trim()) return els[i].innerText.trim(); }"
                "return '';"
            )
            if raw:
                result["ATA"] = raw.split("\n")[0].strip()
        except Exception:
            pass

    return result
