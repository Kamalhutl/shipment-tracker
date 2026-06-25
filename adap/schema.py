from dataclasses import dataclass, field, asdict
from typing import Optional, List
from datetime import datetime


# ─────────────────────────────────────────────
# Sub-schemas
# ─────────────────────────────────────────────

@dataclass
class TrackingEvent:
    """Single movement/status event for a container."""
    timestamp:  Optional[str]   # ISO 8601: "2026-03-28T00:00:00"
    status:     str             # "IMPORT LOADED CONTAINER PICKED UP"
    location:   str             # "MUNDRA"
    country:    Optional[str]   # "IN"
    vessel:     Optional[str]   # "INTERASIA TRIBUTE"
    voyage:     Optional[str]   # "E014"
    event_type: Optional[str]   # "ACTUAL" | "ESTIMATED" | "MA" | "FU" etc.
    raw_code:   Optional[str]   # carrier-specific status code e.g. "FU", "MA"


@dataclass
class Container:
    """Single container under a BL."""
    container_no: str           # "FCIU7134486"
    size:         str           # "40"
    type:         str           # "HC"
    size_type:    str           # "40HC"
    pol:          str           # Port of Loading name
    pod:          str           # Port of Discharge name
    vessel:       str           # Vessel name
    voyage:       str           # Voyage number
    etd:          Optional[str] # Estimated Time of Departure (ISO date)
    eta:          Optional[str] # Estimated Time of Arrival (ISO date)
    events:       List[TrackingEvent] = field(default_factory=list)


@dataclass
class VesselInfo:
    """Vessel details (from carriers like Trans Line that return vessels[]."""
    name:    str
    voyage:  str
    imo:     Optional[str]
    status:  Optional[str]
    etd:     Optional[str]
    atd:     Optional[str]  # Actual time of departure
    eta:     Optional[str]
    ata:     Optional[str]  # Actual time of arrival


@dataclass
class RoutePoint:
    """A point on the shipping route (lat/lon + port info)."""
    latitude:   float
    longitude:  float
    port:       Optional[str]
    port_name:  Optional[str]
    country:    Optional[str]
    timestamp:  Optional[str]


# ─────────────────────────────────────────────
# Root unified schema
# ─────────────────────────────────────────────

@dataclass
class UnifiedTracking:
    """
    Unified tracking schema returned by every adapter.
    All adapters must produce this shape.
    """
    bl_number:  str
    carrier:    str             # "KMTC" | "BLUE_WATER" | "TRANS_LINE" | "ONE_LINE" | ...
    booking_no: str
    pol:        str             # Port of Loading
    pod:        str             # Port of Discharge
    vessel:     str             # Primary vessel name
    voyage:     str             # Primary voyage
    etd:        Optional[str]   # ISO date string
    eta:        Optional[str]   # ISO date string

    containers: List[Container]       = field(default_factory=list)
    vessels:    List[VesselInfo]      = field(default_factory=list)
    route:      List[RoutePoint]      = field(default_factory=list)
    events:     List[TrackingEvent]   = field(default_factory=list)  # BL-level events

    error:      Optional[str]  = None  # populated only on failure
    raw:        dict           = field(default_factory=dict)  # original API response

    def to_dict(self) -> dict:
        return asdict(self)

    def has_error(self) -> bool:
        return self.error is not None


# ─────────────────────────────────────────────
# Builder helpers — adapters use these
# ─────────────────────────────────────────────

def make_event(
    timestamp:  Optional[str]  = None,
    status:     str            = "",
    location:   str            = "",
    country:    Optional[str]  = None,
    vessel:     Optional[str]  = None,
    voyage:     Optional[str]  = None,
    event_type: Optional[str]  = None,
    raw_code:   Optional[str]  = None,
) -> TrackingEvent:
    return TrackingEvent(
        timestamp=timestamp,
        status=status,
        location=location,
        country=country,
        vessel=vessel,
        voyage=voyage,
        event_type=event_type,
        raw_code=raw_code,
    )


def make_container(
    container_no: str          = "",
    size:         str          = "",
    type:         str          = "",
    pol:          str          = "",
    pod:          str          = "",
    vessel:       str          = "",
    voyage:       str          = "",
    etd:          Optional[str]= None,
    eta:          Optional[str]= None,
    events:       list         = None,
) -> Container:
    return Container(
        container_no=container_no,
        size=size,
        type=type,
        size_type=f"{size}{type}",
        pol=pol,
        pod=pod,
        vessel=vessel,
        voyage=voyage,
        etd=etd,
        eta=eta,
        events=events or [],
    )


def make_vessel(
    name:   str            = "",
    voyage: str            = "",
    imo:    Optional[str]  = None,
    status: Optional[str]  = None,
    etd:    Optional[str]  = None,
    atd:    Optional[str]  = None,
    eta:    Optional[str]  = None,
    ata:    Optional[str]  = None,
) -> VesselInfo:
    return VesselInfo(
        name=name, voyage=voyage, imo=imo,
        status=status, etd=etd, atd=atd, eta=eta, ata=ata,
    )


def make_route_point(
    latitude:  float,
    longitude: float,
    port:      Optional[str] = None,
    port_name: Optional[str] = None,
    country:   Optional[str] = None,
    timestamp: Optional[str] = None,
) -> RoutePoint:
    return RoutePoint(
        latitude=latitude,
        longitude=longitude,
        port=port,
        port_name=port_name,
        country=country,
        timestamp=timestamp,
    )
