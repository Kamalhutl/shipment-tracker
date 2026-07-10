"""
website/routes/tracking.py
──────────────────────────────────────────────────────────────────────────────
Tracking API Routes

Responsibilities
  ✓ Receive requests from the website frontend
  ✓ Validate BL Number (format + length)
  ✓ Delegate to adap.tracker.fetch() — never touches scrapers directly
  ✓ Return structured JSON (APIResponse envelope)
  ✓ Handle all error cases with appropriate HTTP status codes
  ✓ Structured logging with per-request timing

NOTE: This router is mounted at prefix="/api" inside website/app.py.
      Do NOT add a duplicate prefix here — routes are /ping, /track, etc.
"""

import logging
import re
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from adap.tracker import fetch  # existing adapter — never modified

# ---------------------------------------------------------------------------
# Router — no prefix here; app.py mounts this under /api
# ---------------------------------------------------------------------------
router = APIRouter(tags=["Tracking"])

logger = logging.getLogger("tracking-api")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_BL_PATTERN = re.compile(r"^[A-Z0-9\-]{5,40}$")


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class TrackRequest(BaseModel):
    """Payload for a single-shipment tracking request."""

    bl_number: str = Field(
        ...,
        min_length=5,
        max_length=40,
        description="Bill of Lading or booking reference number.",
        examples=["MEDU1234567", "HLCU123456789"],
    )
    force_carrier: Optional[str] = Field(
        default=None,
        description="Optional carrier code to skip auto-detection.",
        examples=["MSC", "CMA"],
    )

    @field_validator("bl_number")
    @classmethod
    def normalise_and_validate(cls, value: str) -> str:
        """Strip whitespace, uppercase, and enforce alphanumeric-dash pattern."""
        cleaned = value.strip().upper()
        if not _BL_PATTERN.match(cleaned):
            raise ValueError(
                "BL Number must contain only letters, digits, and hyphens (5–40 chars)."
            )
        return cleaned

class APIResponse(BaseModel):
    """Standard envelope for all tracking responses."""

    success: bool
    message: str
    data: dict[str, Any]


class BatchRequest(BaseModel):
    """Payload for bulk-tracking up to 20 shipments."""

    bl_numbers: list[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="List of BL numbers (max 20 per request).",
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _safe_fetch(bl: str, force_carrier: Optional[str] = None) -> dict[str, Any]:
    """
    Call adap.tracker.fetch and guarantee a dict is always returned.

    Passes force_carrier only if the adapter accepts it to avoid
    TypeErrors on adapters that expose only bl_no.
    """
    try:
        result = fetch(bl_no=bl, force_carrier=force_carrier)
    except TypeError:
        # Fallback: adapter signature does not accept force_carrier
        result = fetch(bl_no=bl)

    if not isinstance(result, dict):
        return {"error": "Tracker returned an unexpected response type."}
    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "/ping",
    summary="Liveness probe",
    response_description="Returns 200 OK when the tracking API is reachable.",
)
async def ping() -> dict[str, Any]:
    """Simple liveness check — no database or external dependency involved."""
    return {"success": True, "message": "Tracking API is running."}


@router.post(
    "/track",
    response_model=APIResponse,
    summary="Track a single shipment",
    response_description="Shipment details from the carrier adapter.",
    status_code=status.HTTP_200_OK,
)
def track(request: TrackRequest) -> APIResponse:
    """
    Track a single Bill of Lading number.

    - Validates and normalises the BL number.
    - Calls ``adap.tracker.fetch`` (existing engine, never modified).
    - Returns a structured ``APIResponse`` envelope.
    - If the tracker signals its own error the response has ``success=False``
      but still returns HTTP 200 so the frontend can render the error message.
    """
    started = time.perf_counter()
    bl = request.bl_number  # already validated + normalised by Pydantic

    logger.info("Track request received | BL=%s carrier=%s", bl, request.force_carrier)

    try:
        result = _safe_fetch(bl, request.force_carrier)

    except Exception as exc:
        logger.exception("Unexpected error while fetching BL=%s", bl)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tracking service encountered an internal error. Please try again.",
        ) from exc

    duration = round(time.perf_counter() - started, 3)

    if result.get("error"):
        logger.warning("Tracker returned error | BL=%s | %s | %.3fs", bl, result["error"], duration)
        return APIResponse(
            success=False,
            message=result["error"],
            data=result,
        )

    logger.info("Track successful | BL=%s | %.3fs", bl, duration)
    return APIResponse(
        success=True,
        message="Shipment tracking retrieved successfully.",
        data=result,
    )


@router.post(
    "/track/batch",
    summary="Track multiple shipments",
    response_description="List of tracking results; partial failures are included inline.",
    status_code=status.HTTP_200_OK,
)
def batch_track(request: BatchRequest) -> dict[str, Any]:
    """
    Track up to 20 BL numbers in a single request.

    Each entry is fetched independently; one failure does not abort the rest.
    Errors are reported per-BL in the ``results`` list.
    """
    results: list[dict[str, Any]] = []

    for raw_bl in request.bl_numbers:
        bl = raw_bl.strip().upper()

        if not _BL_PATTERN.match(bl):
            results.append({"bl_number": bl, "error": "Invalid BL Number format.", "success": False})
            continue

        try:
            tracking = _safe_fetch(bl)
            tracking["success"] = not bool(tracking.get("error"))
            results.append(tracking)

        except Exception as exc:
            logger.exception("Batch error | BL=%s", bl)
            results.append({"bl_number": bl, "error": str(exc), "success": False})

    succeeded = sum(1 for r in results if r.get("success"))
    logger.info("Batch complete | total=%d succeeded=%d", len(results), succeeded)

    return {
        "success": True,
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


@router.get(
    "/status",
    summary="API status",
    response_description="Service name, version, and current status.",
)
async def api_status() -> dict[str, str]:
    """Detailed status endpoint — safe to expose to monitoring dashboards."""
    return {
        "status": "online",
        "api": "Shipment Tracker",
        "version": "1.0.0",
    }
