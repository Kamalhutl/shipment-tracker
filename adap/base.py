from abc import ABC, abstractmethod
from typing import Optional


class BaseAdapter(ABC):
    """
    Every carrier adapter must inherit this.
    Implement _call_api() and _normalize() only.
    fetch() is the unified public entry point.
    """

    # Override in each adapter
    CARRIER_NAME: str = ""

    # -------------------------------------------------------------------------
    # Public entry point — do NOT override
    # -------------------------------------------------------------------------

    def fetch(self, bl_no: str) -> dict:
        """
        Main entry point called by tracker.py.
        1. Calls the carrier API
        2. Normalizes to unified schema
        3. Returns unified dict or error dict
        """
        bl_no = bl_no.strip().upper()

        raw = self._call_api(bl_no)

        if "error" in raw:
            return self._error_schema(bl_no, raw["error"])

        try:
            return self._normalize(raw, bl_no)
        except Exception as e:
            return self._error_schema(bl_no, f"Normalization failed: {str(e)}")

    # -------------------------------------------------------------------------
    # Must implement in every adapter
    # -------------------------------------------------------------------------

    @abstractmethod
    def _call_api(self, bl_no: str) -> dict:
        """
        Call the carrier API.
        Return raw response dict.
        On failure return: {"error": "<message>"}
        """
        pass

    @abstractmethod
    def _normalize(self, raw: dict, bl_no: str) -> dict:
        """
        Map raw carrier response → unified schema from schema.py.
        Must return a dict matching UnifiedTracking structure.
        """
        pass

    # -------------------------------------------------------------------------
    # Shared helpers available to all adapters
    # -------------------------------------------------------------------------

    def _error_schema(self, bl_no: str, message: str) -> dict:
        """Standard error response matching unified schema shape."""
        return {
            "bl_number":  bl_no,
            "carrier":    self.CARRIER_NAME,
            "booking_no": "",
            "pol":        "",
            "pod":        "",
            "vessel":     "",
            "voyage":     "",
            "etd":        None,
            "eta":        None,
            "containers": [],
            "vessels":    [],
            "route":      [],
            "events":     [],
            "error":      message,
            "raw":        {},
        }

    def _safe_get(self, d: dict, *keys, default=""):
        """Safe nested dict access. _safe_get(d, 'a', 'b', 'c')"""
        for key in keys:
            if not isinstance(d, dict):
                return default
            d = d.get(key, default)
        return d if d is not None else default
