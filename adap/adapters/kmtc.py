import requests
import json
from datetime import datetime
from typing import Optional


class KMTCAdapter:
    """
    Adapter for KMTC carrier tracking.
    API: POST https://api.ekmtc.com/trans/trans/cargo-tracking/
    """

    BASE_URL = "https://api.ekmtc.com/trans/trans/cargo-tracking/"

    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Origin": "https://www.ekmtc.com",
        "Referer": "https://www.ekmtc.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not.A/Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "selected-profile": "{}",
        "service-ctrcd": "GB",
        "service-lang": "ENG",
        "service-path": "#/cargo-tracking",
    }

    # -------------------------------------------------------------------------
    # Public entry point
    # -------------------------------------------------------------------------

    def fetch(self, bl_no: str) -> dict:
        """
        Fetch tracking data for a given BL number and return unified schema.

        Args:
            bl_no: Bill of Lading number (e.g. "SIN3292439")

        Returns:
            Unified tracking dict. On error, contains 'error' key.
        """
        raw = self._call_api(bl_no)
        if "error" in raw:
            return raw
        return self._normalize(raw, bl_no)

    # -------------------------------------------------------------------------
    # API call
    # -------------------------------------------------------------------------

    def _call_api(self, bl_no: str) -> dict:
        """
        POST to KMTC cargo-tracking endpoint.

        KMTC accepts BL number as the search key.
        Body observed from DevTools: { "blNo": "<BL_NO>", "cntrNo": "" }
        """
        payload = {"dtKnd": "BL", "blNo": bl_no}

        try:
            response = requests.post(
                self.BASE_URL,
                headers=self.HEADERS,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            return {"error": "KMTC API timeout", "carrier": "KMTC", "bl_number": bl_no}

        except requests.exceptions.HTTPError as e:
            return {
                "error": f"KMTC HTTP error: {e.response.status_code}",
                "carrier": "KMTC",
                "bl_number": bl_no,
            }

        except requests.exceptions.RequestException as e:
            return {"error": f"KMTC request failed: {str(e)}", "carrier": "KMTC", "bl_number": bl_no}

        except json.JSONDecodeError:
            return {"error": "KMTC returned non-JSON response", "carrier": "KMTC", "bl_number": bl_no}

    # -------------------------------------------------------------------------
    # Normalizer
    # -------------------------------------------------------------------------

    def _normalize(self, raw: dict, bl_no: str) -> dict:
        """
        Map raw KMTC response → unified tracking schema.

        Raw KMTC response structure:
        {
            "cntrList": [
                {
                    "rowCnt":    6,
                    "blNo":      "SIN3292439",
                    "bkgNo":     "SG00380702",
                    "cntrNo":    "FCIU7134486",
                    "cntrSzCd":  "40",
                    "cntrTypCd": "HC",
                    "podPortNm": "TUTICORIN",
                    "polPortNm": "SINGAPORE",
                    "vslNm":     "1)KMTC YOKOHAMA/2601W",
                    "voyNo":     "2601W",
                    "etd":       "20260109..."
                },
                ...
            ]
        }
        """
        cntr_list = raw.get("cntrList", [])

        containers = []
        for item in cntr_list:
            containers.append(self._parse_container(item))

        # Pull top-level info from first container (all share same BL)
        first = cntr_list[0] if cntr_list else {}

        return {
            "bl_number":  bl_no,
            "carrier":    "KMTC",
            "booking_no": first.get("bkgNo", ""),
            "pol":        first.get("polPortNm", ""),   # Port of Loading
            "pod":        first.get("podPortNm", ""),   # Port of Discharge
            "vessel":     self._parse_vessel_name(first.get("vslNm", "")),
            "voyage":     first.get("voyNo", ""),
            "etd":        self._parse_date(first.get("etd", "")),
            "containers": containers,
            "raw":        raw,   # keep original for debugging; remove in prod if needed
        }

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _parse_container(self, item: dict) -> dict:
        """Parse a single container entry from cntrList."""
        return {
            "container_no": item.get("cntrNo", ""),
            "size":         item.get("cntrSzCd", ""),       # e.g. "40"
            "type":         item.get("cntrTypCd", ""),       # e.g. "HC"
            "size_type":    f"{item.get('cntrSzCd', '')}{item.get('cntrTypCd', '')}",  # "40HC"
            "pol":          item.get("polPortNm", ""),
            "pod":          item.get("podPortNm", ""),
            "vessel":       self._parse_vessel_name(item.get("vslNm", "")),
            "voyage":       item.get("voyNo", ""),
            "etd":          self._parse_date(item.get("etd", "")),
            "events":       [],  # KMTC cntrList doesn't return per-event data at this level
                                 # A second call per container would be needed for event history
        }

    def _parse_vessel_name(self, raw_name: str) -> str:
        """
        KMTC vessel name is sometimes prefixed: '1)KMTC YOKOHAMA/2601W'
        Strip the index prefix and voyage suffix.
        """
        if not raw_name:
            return ""
        # Remove '1)' style prefix
        if ")" in raw_name:
            raw_name = raw_name.split(")", 1)[-1].strip()
        # Remove '/VOYAGE' suffix
        if "/" in raw_name:
            raw_name = raw_name.split("/")[0].strip()
        return raw_name

    def _parse_date(self, raw_date: str) -> Optional[str]:
        """
        Normalize KMTC date strings to ISO 8601.
        KMTC ETD format observed: '20260109...' (YYYYMMDD prefix)
        """
        if not raw_date:
            return None
        try:
            # Try YYYYMMDD (first 8 chars)
            return datetime.strptime(raw_date[:8], "%Y%m%d").strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            return raw_date  # return as-is if unparseable


# -------------------------------------------------------------------------
# Quick test
# -------------------------------------------------------------------------

if __name__ == "__main__":
    adapter = KMTCAdapter()

    # Replace with a real BL number
    result = adapter.fetch("SIN3292439")

    print(json.dumps(result, indent=2, default=str))
