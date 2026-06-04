import re
import time
import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

TRACKING_URL = (
    "https://elines.coscoshipping.com/ebusiness/cargoTracking"
    "?trackingType=BILLOFLADING&number={bl}"
)

IFRAME_URL = (
    "https://elines.coscoshipping.com/scct/public/ct/base"
    "?lang=en&trackingType=BILLOFLADING&number={bl}"
)

API_URL = "https://elines.coscoshipping.com/ebtracking/public/bill/export?blNo={bl}"
WAIT    = 15

_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://elines.coscoshipping.com/",
}

_EMPTY = {
    "POL": "", "POD": "", "Container No": "",
    "Vessel": "", "ATA": "", "Latest Status": "", "FND": "",
}

_STATUS_KEYWORDS = [
    "Transport Completed", "In Transit", "Arrived", "Departed",
    "Delivered", "Discharged", "Gate Out", "Empty Return",
    "Vessel Departed", "Cargo Received",
]

def _api_scrape(bl: str) -> dict:
    result = dict(_EMPTY)

    bare = re.sub(r"^COSU", "", bl.strip(), flags=re.IGNORECASE)
    variants = {bl.strip(), f"COSU{bare}"}

    for bl_try in variants:
        try:
            r = requests.get(
                API_URL.format(bl=bl_try),
                headers=_API_HEADERS,
                timeout=20,
            )
            data = r.json()
            print(f"COSCO API [{bl_try}]: {data.get('message', '')}")

            if data.get("message") == "无数据":
                continue

            bill = (data.get("data") or {}).get("bill") or {}
            if not bill:
                continue

            result["POL"] = bill.get("polName", "")
            result["POD"] = bill.get("podName", "")

            containers = bill.get("containers") or []
            if containers:
                result["Container No"] = containers[0].get("containerNo", "")
                events = containers[0].get("events") or []
                if events:
                    result["Latest Status"] = events[0].get("eventName", "")
                    result["ATA"]           = events[0].get("eventTime", "")
                    result["FND"]           = bill.get("fndTime", "") or bill.get("eta", "")

            schedules = bill.get("schedules") or []
            if schedules:
                result["Vessel"] = schedules[-1].get("vesselName", "")

            if any(result.values()):
                print(f"COSCO API: success with [{bl_try}]")
                return result

        except Exception as e:
            print(f"COSCO API ERROR [{bl_try}]:", e)

    return result

def _get(driver, css, timeout=WAIT, default=""):
    try:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css))
        ).text.strip()
    except Exception:
        return default

def _accept_cookies(driver):
    for sel in ("#onetrust-accept-btn-handler", ".cookie-accept", "[class*='accept']"):
        try:
            driver.find_element(By.CSS_SELECTOR, sel).click()
            time.sleep(1)
            return
        except Exception:
            continue

def _selenium_scrape(driver, bl: str) -> dict:
    result = dict(_EMPTY)

    driver.get(IFRAME_URL.format(bl=bl.strip()))
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    _accept_cookies(driver)

    try:
        WebDriverWait(driver, 45).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "span.date"))
        )
        time.sleep(2)
    except TimeoutException:
        print("COSCO Selenium: span.date never appeared in iframe URL")
        with open("cosco_debug2.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        return result

    with open("cosco_debug2.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)

    try:
        dates = driver.find_elements(By.CSS_SELECTOR, "span.date")
        times = driver.find_elements(By.CSS_SELECTOR, "span.time")
        tzs   = driver.find_elements(By.CSS_SELECTOR, "span.timezone")
        print(f"DATES ({len(dates)}): {[d.text for d in dates]}")
        print(f"TIMES ({len(times)}): {[t.text for t in times]}")

        def _dt(i):
            d = dates[i].text.strip() if i < len(dates) else ""
            t = times[i].text.strip() if i < len(times) else ""
            z = tzs[i].text.strip()   if i < len(tzs)   else ""
            return f"{d} {t} {z}".strip()

        if len(dates) >= 5:
            result["ATA"] = _dt(3)
            result["FND"] = _dt(4)
        elif len(dates) >= 4:
            result["ATA"] = _dt(2)
            result["FND"] = _dt(3)
        elif len(dates) >= 2:
            result["ATA"] = _dt(len(dates) - 2)
            result["FND"] = _dt(len(dates) - 1)
    except Exception as e:
        print("ATA/FND ERROR:", e)

    for header_text, field in (("First POL", "POL"), ("Last POD", "POD")):
        try:
            hdr = driver.find_element(
                By.XPATH,
                f"//*[normalize-space(text())='{header_text}']",
            )
            col = hdr.find_element(
                By.XPATH,
                "ancestor::div[contains(@class,'ant-col')][1]",
            )
            candidates = []
            for el in col.find_elements(By.XPATH, ".//*"):
                t = el.text.strip()
                if (
                    t
                    and t != header_text
                    and not re.match(r"^\d{4}-", t)
                    and not re.match(r"^\d{2}:\d{2}", t)
                    and 2 < len(t) < 60
                ):
                    candidates.append(t)
            if candidates:
                result[field] = candidates[0]
        except Exception as e:
            print(f"{field} ERROR: {e}")

    for kw in _STATUS_KEYWORDS:
        try:
            els = driver.find_elements(
                By.XPATH, f"//*[contains(text(),'{kw}')]"
            )
            if els:
                result["Latest Status"] = els[0].text.strip()
                break
        except Exception:
            pass

    if not result["Latest Status"]:
        result["Latest Status"] = _get(
            driver,
            "[class*='latest-status-content'],[class*='event-name'],"
            "[class*='status-badge'],[class*='cargo-status']",
            timeout=5,
        )

    try:
        cntr = driver.execute_script(
            "var m = document.body.innerText.match(/[A-Z]{4}\\d{7}/);"
            "return m ? m[0] : '';"
        )
        result["Container No"] = cntr or ""
    except Exception as e:
        print("Container JS ERROR:", e)

    try:
        for tr in driver.find_elements(By.CSS_SELECTOR, "table tbody tr"):
            cells = tr.find_elements(By.TAG_NAME, "td")
            if cells:
                v = cells[0].text.strip()
                if v and len(v) > 3:
                    result["Vessel"] = v
                    break
    except Exception:
        pass

    if not result["Vessel"]:
        result["Vessel"] = _get(
            driver,
            "[class*='vessel-name'],[class*='vesselName']",
            timeout=5,
        )

    return result

def scrape(driver, bl: str) -> dict:
    api_result = _api_scrape(bl.strip())
    if any(api_result.values()):
        return api_result
    print("COSCO: API returned no data — falling back to Selenium")
    return _selenium_scrape(driver, bl)
