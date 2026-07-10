"""
router.py
─────────
Maps a BL number → correct adapter class.

Strategy:
  1. BL prefix match  (fast, works for most carriers)
  2. BL pattern match (regex fallback for ambiguous prefixes)
  3. Manual override  (caller can force a carrier)

Adding a new carrier:
  → Add its BL prefixes to BL_PREFIX_MAP
  → Add the adapter class to ADAPTER_MAP
  → Done.
"""

import re
from typing import Type, Optional

# ── Import all adapters ────────────────────────────────────────────────────────
from adap.adapters.kmtc       import KMTCAdapter
from adap.adapters.blue_water import BlueWaterAdapter
from adap.adapters.trans_line import TransLineAdapter
from adap.adapters.one_line   import OneLineAdapter
from adap.adapters.msc        import MSCAdapter
from adap.adapters.hmm        import HMMAdapter
from adap.adapters.pil        import PILAdapter
from adap.adapters.hapag import HapagAdapter
# from adap.adapters.cosco    import COSCOAdapter     # add when ready
# from adap.adapters.interasia import InterAsiaAdapter
from adap.base import BaseAdapter
from adap.adapters.trans_asia import TransAsiaAdapter


# ─────────────────────────────────────────────────────────────────────────────
# BL Prefix → Carrier name
# Source: industry knowledge + your Postman screenshots
# ─────────────────────────────────────────────────────────────────────────────

BL_PREFIX_MAP: dict[str, str] = {

    # KMTC  (seen in screenshots: FCIU, KMTU, TXGU under BL SIN3292439)
    "KMTU": "KMTC",
    "FCIU": "KMTC",
    "TXGU": "KMTC",
    "KKFU": "KMTC",
    "KKLU": "KMTC",
    "KMTC": "KMTC",  # add this line to BL_PREFIX_MAP
    # Blue Water Lines
    "BSIU": "BLUE_WATER",
    "BLWU": "BLUE_WATER",
    "JKT":  "BLUE_WATER",   # ← add this line
    # Trans Line (BL prefix seen: TRLS)
    "TRLS": "TRANS_LINE",
    "TRLSINTUT": "TRANS_LINE",   # longer match wins

    # ONE LINE (BL seen: CPTG)
    "CPTG": "ONE_LINE",
    "ONEY": "ONE_LINE",
    "ONEU": "ONE_LINE",

    # MSC
    "MSCU": "MSC",
    "MEDU": "MSC",

    # Maersk
    "MSKU": "MAERSK",
    "MRKU": "MAERSK",

    # CMA CGM
    "CMAU": "CMA_CGM",
    "CGMU": "CMA_CGM",

    # Hapag-Lloyd
    "HLCU": "HAPAG",
    "HLXU": "HAPAG",

    # COSCO
    "COSU": "COSCO",
    "CBHU": "COSCO",

    # InterAsia
    "IASU": "INTERASIA",
    "IATU": "INTERASIA",

    # HMM
    "HMMU": "HMM",
    "HEMU": "HMM",
    "BCNA": "HMM",
    
    # PIL
    "PCIU": "PIL",
    "PLCU": "PIL",
    "BKK": "PIL",
    
    # TRANS ASIA
    "TASU": "TRANS_ASIA",
    "TAL": "TRANS_ASIA",
    "TALTLS": "TRANS_ASIA",
}


# ─────────────────────────────────────────────────────────────────────────────
# Carrier name → Adapter class
# ─────────────────────────────────────────────────────────────────────────────

ADAPTER_MAP: dict[str, Type[BaseAdapter]] = {
    "KMTC":       KMTCAdapter,
    "BLUE_WATER": BlueWaterAdapter,
    "TRANS_LINE": TransLineAdapter,
    "ONE_LINE":   OneLineAdapter,
    "MSC":        MSCAdapter,
    # "MAERSK":     MaerskAdapter,
    # "CMA_CGM":    CMACGMAdapter,
    "HAPAG":        HapagAdapter,
    # "COSCO":      COSCOAdapter,
    #"INTERASIA":  InterAsiaAdapter,
    "HMM":        HMMAdapter,
    "PIL": PILAdapter,
    "TRANS_ASIA": TransAsiaAdapter,
}


# ─────────────────────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────────────────────

class UnknownCarrierError(Exception):
    pass


def detect_carrier(bl_no: str) -> str:
    """
    Detect carrier name from BL number prefix.
    Tries longest prefix first to avoid short-prefix false matches.

    Returns carrier string e.g. "KMTC", "BLUE_WATER"
    Raises UnknownCarrierError if no match found.
    """
    bl_upper = bl_no.strip().upper()

    # Sort by length descending so longer prefixes match first
    for prefix in sorted(BL_PREFIX_MAP.keys(), key=len, reverse=True):
        if bl_upper.startswith(prefix):
            return BL_PREFIX_MAP[prefix]

    raise UnknownCarrierError(
        f"Cannot detect carrier for BL '{bl_no}'. "
        f"Add its prefix to BL_PREFIX_MAP in router.py."
    )


def get_adapter(bl_no: str, force_carrier: Optional[str] = None) -> BaseAdapter:
    """
    Resolve and instantiate the correct adapter for a BL number.

    Args:
        bl_no:          Bill of Lading number
        force_carrier:  Optional override e.g. "KMTC" — skips auto-detection

    Returns:
        Instantiated adapter ready to call .fetch(bl_no)

    Raises:
        UnknownCarrierError: if carrier not detected and no override given
        ValueError: if forced carrier not in ADAPTER_MAP
    """
    carrier = force_carrier.upper() if force_carrier else detect_carrier(bl_no)

    if carrier not in ADAPTER_MAP:
        available = ", ".join(sorted(ADAPTER_MAP.keys()))
        raise ValueError(
            f"Carrier '{carrier}' has no adapter yet. "
            f"Available: {available}"
        )

    return ADAPTER_MAP[carrier]()


def list_supported_carriers() -> list[str]:
    """Return list of carriers that have a working adapter."""
    return sorted(ADAPTER_MAP.keys())


def list_known_prefixes() -> dict[str, str]:
    """Return full BL prefix → carrier mapping."""
    return dict(BL_PREFIX_MAP)
