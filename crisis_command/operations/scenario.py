"""Seeded scenarios loaded through the command service.

All scenario data is synthetic. Place names are illustrative labels on a grid, not a
validated representation of Richmond roads or emergency resources.
"""

from dataclasses import dataclass, field
from typing import Any, Callable

from ..model import City, Coordinate, EmergencyService, Incident, Road, Severity
from ..simulation import World


@dataclass(frozen=True)
class Disruption:
    """A scripted report injected through normal intake; it is confirmed like any other."""

    label: str
    content: str
    source: str
    fields: dict[str, Any]


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    build_world: Callable[[], World]
    places: dict[str, Coordinate]
    disruptions: dict[str, Disruption] = field(default_factory=dict)
    # Map-only context lines such as a river: (label, points). Not part of the road graph.
    annotations: tuple[tuple[str, tuple[Coordinate, ...]], ...] = ()
    # Sample flood cover per place (percent of the area under water), used by the Vision
    # agent until segmentation output is wired in. Invented exercise values.
    flood_sample: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Street-level descriptions for the report reader, and readable road names for the UI.
    place_notes: dict[str, str] = field(default_factory=dict)
    road_names: dict[str, str] = field(default_factory=dict)


def build_demo_world() -> World:
    fire_station = Coordinate(1, 1)
    ambulance_station = Coordinate(1, 2)
    police_station = Coordinate(0, 4)
    intersection_a = Coordinate(4, 1)
    intersection_b = Coordinate(4, 2)
    intersection_c = Coordinate(4, 4)
    fire_location = Coordinate(8, 5)
    collision_location = Coordinate(2, 4)
    city = City("Demo City")
    for road_id, start, end in (
        ("fire-road-1", fire_station, intersection_a),
        ("fire-road-2", intersection_a, fire_location),
        ("medical-road-1", ambulance_station, intersection_b),
        ("connector-1", intersection_b, intersection_a),
        ("connector-2", intersection_b, intersection_c),
        ("police-road-1", police_station, collision_location),
        ("collision-road", collision_location, intersection_c),
    ):
        city.add_road(Road(road_id, start, end))

    return World(
        incidents=[
            Incident(
                id="incident-1",
                kind="building fire",
                location=fire_location,
                severity=Severity.CRITICAL,
                required_services=frozenset({"fire", "medical"}),
            ),
            Incident(
                id="incident-2",
                kind="road collision",
                location=collision_location,
                severity=Severity.HIGH,
                required_services=frozenset({"medical", "police"}),
            ),
        ],
        services=[
            EmergencyService(
                id="unit-fire-1",
                name="Fire engine 1",
                location=fire_station,
                capabilities=frozenset({"fire"}),
                speed=2.0,
            ),
            EmergencyService(
                id="unit-medical-1",
                name="Ambulance 1",
                location=ambulance_station,
                capabilities=frozenset({"medical"}),
                speed=2.5,
            ),
            EmergencyService(
                id="unit-police-1",
                name="Police unit 1",
                location=police_station,
                capabilities=frozenset({"police"}),
                speed=3.0,
            ),
        ],
        city=city,
    )


def main() -> None:
    coordinator = Coordinator(build_demo_world())
    try:
        while any(
            incident.status != IncidentStatus.RESOLVED
            for incident in coordinator.world.incidents
        ):
            plan = coordinator.step()
            if plan.assignments:
                dispatches = ", ".join(
                    f"{dispatch.service_id} -> {dispatch.incident_id} "
                    f"(ETA {dispatch.estimated_arrival:.1f})"
                    for dispatch in plan.assignments
                )
                print(f"Tick {coordinator.world.tick_count}: {dispatches}")
            elif plan.unassigned_incident_ids:
                print(
                    "Waiting for available services: "
                    + ", ".join(plan.unassigned_incident_ids)
                )
    except UnserviceableIncidentsError as error:
        print(error)
        raise SystemExit(1) from error

    print(f"All incidents resolved in {coordinator.world.tick_count} ticks.")


DEMO_SCENARIO = Scenario(
    name="demo-city",
    description="Two-incident demo world used by tests.",
    build_world=build_demo_world,
    places={
        "Fire Station 1": Coordinate(1, 1),
        "Ambulance Station": Coordinate(1, 2),
        "Police Station": Coordinate(0, 4),
        "Junction A": Coordinate(4, 1),
        "Junction B": Coordinate(4, 2),
        "Junction C": Coordinate(4, 4),
        "Warehouse District": Coordinate(8, 5),
        "Collision Corner": Coordinate(2, 4),
    },
)


# A river runs east-west at y = 3.5. The upriver bridge is already closed when the
# scenario starts, so the main street bridge is the only crossing.
FLOOD_PLACES = {
    "West End": Coordinate(1, 5),
    "Fire Station North": Coordinate(2, 6),
    "Hospital": Coordinate(3, 7),
    "Broad & 3rd": Coordinate(3, 5),
    "Downtown Junction": Coordinate(5, 5),
    "High School Shelter": Coordinate(6, 7),
    "Shockoe Bottom": Coordinate(7, 4.5),
    "East Junction": Coordinate(9, 5),
    "Upriver North": Coordinate(0, 5),
    "Upriver South": Coordinate(0, 2),
    "Manchester": Coordinate(3, 2),
    "South Junction": Coordinate(5, 2),
    "South Station": Coordinate(6, 1),
    "Riverside Apartments": Coordinate(8, 2),
}

FLOOD_ROADS = (
    ("west-broad", "West End", "Broad & 3rd", False),
    ("station-broad", "Fire Station North", "Broad & 3rd", False),
    ("hospital-broad", "Hospital", "Broad & 3rd", False),
    ("broad-street", "Broad & 3rd", "Downtown Junction", False),
    ("shelter-road", "Downtown Junction", "High School Shelter", False),
    ("hospital-shelter", "Hospital", "High School Shelter", False),
    ("dock-street", "Downtown Junction", "Shockoe Bottom", False),
    ("east-main", "Shockoe Bottom", "East Junction", False),
    ("upriver-north-road", "West End", "Upriver North", False),
    ("main-street-bridge", "Downtown Junction", "South Junction", False),
    ("upriver-bridge", "Upriver North", "Upriver South", True),
    ("upriver-south-road", "Upriver South", "Manchester", False),
    ("hull-street", "Manchester", "South Junction", False),
    ("station-road", "South Junction", "South Station", False),
    ("riverside-drive", "South Junction", "Riverside Apartments", False),
    ("river-road", "South Station", "Riverside Apartments", False),
)


def build_flood_world() -> World:
    places = FLOOD_PLACES
    city = City("Richmond-inspired flood exercise (synthetic)")
    for road_id, start, end, closed in FLOOD_ROADS:
        city.add_road(Road(road_id, places[start], places[end], is_closed=closed))

    incidents = [
        Incident("incident-1", "residents trapped by rising water", places["Riverside Apartments"],
                 Severity.CRITICAL, frozenset({"rescue", "medical"}), people=5),
        Incident("incident-2", "vehicle stranded in flooded underpass", places["Shockoe Bottom"],
                 Severity.HIGH, frozenset({"rescue"}), people=2),
        Incident("incident-3", "transformer fire", places["Downtown Junction"],
                 Severity.HIGH, frozenset({"fire", "utility"})),
        Incident("incident-4", "dialysis patient cut off from clinic", places["Manchester"],
                 Severity.MEDIUM, frozenset({"medical"}), people=1),
        Incident("incident-5", "downed power lines across road", places["East Junction"],
                 Severity.MEDIUM, frozenset({"police"})),
        Incident("incident-6", "vehicle fire at flooded intersection", places["Broad & 3rd"],
                 Severity.LOW, frozenset({"fire"})),
    ]
    services = [
        EmergencyService("fire-1", "Fire Engine 1", places["West End"], frozenset({"fire"}), speed=2.0),
        EmergencyService("fire-2", "Fire Engine 2", places["South Junction"], frozenset({"fire"}), speed=2.0),
        EmergencyService("medic-1", "Ambulance 1", places["Hospital"], frozenset({"medical"}), speed=2.5, capacity=2),
        EmergencyService("medic-2", "Ambulance 2", places["South Station"], frozenset({"medical"}), speed=2.5, capacity=2),
        EmergencyService("boat-1", "Rescue Boat 1", places["Shockoe Bottom"], frozenset({"rescue"}), speed=1.5, capacity=4),
        EmergencyService("boat-2", "Rescue Boat 2", places["South Station"], frozenset({"rescue"}), speed=1.5, capacity=3),
        EmergencyService("police-1", "Police Unit 1", places["Downtown Junction"], frozenset({"police"}), speed=3.0),
        EmergencyService("utility-1", "Utility Crew 1", places["East Junction"], frozenset({"utility"}), speed=2.0),
    ]
    return World(incidents=incidents, services=services, city=city)


FLOOD_SCENARIO = Scenario(
    name="flood-exercise",
    description=(
        "Synthetic flood on a Richmond-inspired grid: 6 incidents, 8 resources, river "
        "crossing at the main street bridge only."
    ),
    build_world=build_flood_world,
    places=FLOOD_PLACES,
    disruptions={
        "bridge_closure": Disruption(
            label="Confirm main street bridge closure",
            content="Road authority: main street bridge closed, water over the deck.",
            source="road authority",
            fields={
                "report_type": "road_status",
                "road_id": "main-street-bridge",
                "road_closed": True,
                "certainty": "confirmed",
            },
        ),
    },
    flood_sample={
        "Shockoe Bottom": {"percent": 85, "note": "sample imagery: Dock St and 17th St under water"},
        "Riverside Apartments": {"percent": 80, "note": "sample imagery: ground floor flooded"},
        "Manchester": {"percent": 65, "note": "sample imagery: low streets near Hull St flooded"},
        "East Junction": {"percent": 55, "note": "sample imagery: E Main St partly flooded"},
        "Downtown Junction": {"percent": 40, "note": "sample imagery: water at 14th St and Main St"},
        "South Junction": {"percent": 45, "note": "sample imagery: south end of the Mayo Bridge"},
        "Broad & 3rd": {"percent": 5, "note": "sample imagery: dry"},
    },
    annotations=(
        ("River", (Coordinate(-1, 3.5), Coordinate(10, 3.5))),
    ),
)


# ----- Richmond flood demo (the START DEMO SCENARIO run) -------------------------------

# A higher route between 14th St & Main and Rocketts over Church Hill: with Dock St under
# water, it keeps Shockoe Bottom reachable from the 14th St (Mayo) Bridge.
DEMO_EXTRA_ROADS = (("broad-street-east", "Downtown Junction", "East Junction", False),)

DEMO_ROAD_NAMES = {
    "main-street-bridge": "14th St (Mayo) Bridge",
    "upriver-bridge": "Lee Bridge",
    "dock-street": "Dock St",
    "east-main": "E Main St",
    "broad-street-east": "Church Hill route",
    "hull-street": "Hull St",
    "broad-street": "E Broad St",
    "riverside-drive": "Riverside Dr",
    "river-road": "Commerce Rd riverside",
    "station-road": "Commerce Rd",
    "hospital-broad": "E Broad St (VCU)",
    "west-broad": "W Broad St",
    "station-broad": "N 2nd St",
    "shelter-road": "Church Hill (Broad St)",
    "hospital-shelter": "E Marshall St",
    "upriver-north-road": "Belvidere St",
    "upriver-south-road": "Cowardin Ave",
}

DEMO_PLACE_NOTES = {
    "Shockoe Bottom": "E Main St between 15th and 21st St, including E 18th St, Dock St and Main Street Station",
    "Downtown Junction": "14th St at Main St, north end of the 14th St (Mayo) Bridge",
    "South Junction": "south end of the 14th St (Mayo) Bridge, Hull St at Commerce Rd",
    "East Junction": "Rocketts Landing, E Main St at Williamsburg Ave",
    "Riverside Apartments": "south-bank apartments east of the Mayo Bridge",
    "Manchester": "Hull St at Cowardin Ave and the blocks around it",
    "Broad & 3rd": "E Broad St at N 3rd St",
    "dock-street": "Dock St, along the river below Shockoe Bottom",
    "main-street-bridge": "the 14th St (Mayo) Bridge over the James River",
    "east-main": "E Main St from Shockoe Bottom to Rocketts Landing",
}


def build_richmond_demo_world() -> World:
    places = FLOOD_PLACES
    city = City("Richmond flood demo (simulation)")
    for road_id, start, end, closed in FLOOD_ROADS + DEMO_EXTRA_ROADS:
        city.add_road(Road(road_id, places[start], places[end], is_closed=closed))
    incidents = [
        Incident("incident-9", "residents trapped by rising water", places["Riverside Apartments"],
                 Severity.CRITICAL, frozenset({"rescue", "medical"}), people=5),
        Incident("incident-10", "transformer fire", places["Downtown Junction"],
                 Severity.HIGH, frozenset({"fire", "utility"})),
        Incident("incident-11", "dialysis patient cut off from clinic", places["Manchester"],
                 Severity.MEDIUM, frozenset({"medical"}), people=1),
        Incident("incident-12", "downed power lines across road", places["East Junction"],
                 Severity.MEDIUM, frozenset({"police"})),
        Incident("incident-13", "vehicle fire at flooded intersection", places["Broad & 3rd"],
                 Severity.LOW, frozenset({"fire"})),
    ]
    services = [
        EmergencyService("fire-1", "Fire Engine 1", places["West End"], frozenset({"fire"}), speed=2.0),
        EmergencyService("fire-2", "Fire Engine 2", places["South Junction"], frozenset({"fire"}), speed=2.0),
        EmergencyService("medic-1", "Ambulance 1", places["Hospital"], frozenset({"medical"}), speed=2.5, capacity=2),
        EmergencyService("medic-2", "Ambulance 2", places["South Station"], frozenset({"medical"}), speed=2.5, capacity=2),
        EmergencyService("swift-1", "Swift Water Team 1", places["Riverside Apartments"], frozenset({"rescue"}), speed=1.5, capacity=6),
        EmergencyService("swift-2", "Swift Water Team 2", places["South Station"], frozenset({"rescue"}), speed=1.5, capacity=6),
        EmergencyService("police-1", "Police Unit 1", places["Hospital"], frozenset({"police"}), speed=3.0),
        EmergencyService("utility-1", "Utility Crew 1", places["East Junction"], frozenset({"utility"}), speed=2.0),
    ]
    return World(incidents=incidents, services=services, city=city)


DEMO_FLOOD_SCENARIO = Scenario(
    name="richmond-flood-demo",
    description="Seeded Richmond flash flood run by START DEMO SCENARIO (simulation).",
    build_world=build_richmond_demo_world,
    places=FLOOD_PLACES,
    flood_sample={key: value for key, value in FLOOD_SCENARIO.flood_sample.items() if key != "Shockoe Bottom"},
    place_notes=DEMO_PLACE_NOTES,
    road_names=DEMO_ROAD_NAMES,
)


def build_richmond_live_world() -> World:
    """The Richmond road graph and units with no incidents: everything arrives through intake."""
    seeded = build_richmond_demo_world()
    return World(incidents=[], services=list(seeded.services), city=seeded.city)


LIVE_RICHMOND_SCENARIO = Scenario(
    name="richmond-live-intake",
    description="Richmond roads and units; incidents arrive only from the report page, the intake API "
                "and database connectors.",
    build_world=build_richmond_live_world,
    places=FLOOD_PLACES,
    place_notes=DEMO_PLACE_NOTES,
    road_names=DEMO_ROAD_NAMES,
)
