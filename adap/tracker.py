"""
tracker.py
──────────
Single entry point for the entire adaptation layer.

Usage:
    from tracker import fetch, fetch_many

    # Single BL
    result = fetch("SIN3292439")

    # Multiple BLs in parallel
    results = fetch_many(["SIN3292439", "TRLSINTUT6515200", "BSIU9309868"])

    # Force a specific carrier (skip auto-detection)
    result = fetch("UNKNOWNBL123", force_carrier="KMTC")
"""

import json
import time
import logging
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from router import get_adapter, detect_carrier, UnknownCarrierError

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tracker")


# ─────────────────────────────────────────────────────────────────────────────
# Core: single BL fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch(bl_no: str, force_carrier: Optional[str] = None) -> dict:
    """
    Fetch tracking data for a single BL number.

    Args:
        bl_no:          Bill of Lading number e.g. "SIN3292439"
        force_carrier:  Optional carrier override e.g. "KMTC"

    Returns:
        Unified tracking dict (see schema.py → UnifiedTracking)
        Always returns a dict — errors are in result["error"]
    """
    bl_no = bl_no.strip().upper()
    start = time.time()

    try:
        # 1. Detect carrier
        carrier = force_carrier or detect_carrier(bl_no)
        logger.info(f"BL={bl_no} → carrier={carrier}")

        # 2. Get correct adapter
        adapter = get_adapter(bl_no, force_carrier=force_carrier)

        # 3. Fetch + normalize
        result = adapter.fetch(bl_no)

        elapsed = round(time.time() - start, 2)
        logger.info(f"BL={bl_no} done in {elapsed}s | error={result.get('error')}")

        return result

    except UnknownCarrierError as e:
        logger.warning(f"BL={bl_no} → {e}")
        return _error_response(bl_no, str(e))

    except ValueError as e:
        logger.warning(f"BL={bl_no} → {e}")
        return _error_response(bl_no, str(e))

    except Exception as e:
        logger.error(f"BL={bl_no} → unexpected error: {e}", exc_info=True)
        return _error_response(bl_no, f"Unexpected error: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# Batch: multiple BLs in parallel
# ─────────────────────────────────────────────────────────────────────────────

def fetch_many(
    bl_numbers: list[str],
    max_workers: int = 5,
    force_carrier: Optional[str] = None,
) -> list[dict]:
    """
    Fetch multiple BL numbers in parallel using a thread pool.

    Args:
        bl_numbers:     List of BL numbers
        max_workers:    Max parallel threads (default 5, be careful with rate limits)
        force_carrier:  Apply same carrier override to all BLs

    Returns:
        List of unified tracking dicts, same order as input bl_numbers
    """
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_bl = {
            executor.submit(fetch, bl, force_carrier): bl
            for bl in bl_numbers
        }

        for future in as_completed(future_to_bl):
            bl = future_to_bl[future]
            try:
                results[bl] = future.result()
            except Exception as e:
                logger.error(f"fetch_many: BL={bl} failed: {e}")
                results[bl] = _error_response(bl, str(e))

    # Return in original order
    return [results[bl] for bl in bl_numbers]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _error_response(bl_no: str, message: str) -> dict:
    return {
        "bl_number":  bl_no,
        "carrier":    "UNKNOWN",
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


# ─────────────────────────────────────────────────────────────────────────────
# CLI usage: python tracker.py <BL_NUMBER> [CARRIER_OVERRIDE]
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    bl       = sys.argv[1]
    carrier  = sys.argv[2] if len(sys.argv) > 2 else None
    result   = fetch(bl, force_carrier=carrier)
    print(json.dumps(result, indent=2, default=str))