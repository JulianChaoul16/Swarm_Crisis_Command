from model import City, Coordinate, EmergencyService, Incident, IncidentStatus, Road, Severity
from simulation import Simulation, World


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
    simulation = Simulation(build_demo_world())
    while any(
        incident.status != IncidentStatus.RESOLVED
        for incident in simulation.world.incidents
    ):
        plan = simulation.step()
        if plan.assignments:
            dispatches = ", ".join(
                f"{dispatch.service_id} -> {dispatch.incident_id} "
                f"(ETA {dispatch.estimated_arrival:.1f})"
                for dispatch in plan.assignments
            )
            print(f"Tick {simulation.world.tick_count}: {dispatches}")
        elif plan.unassigned_incident_ids:
            print(
                "Waiting for available services: "
                + ", ".join(plan.unassigned_incident_ids)
            )
            if all(service.available for service in simulation.world.services):
                next_plan = simulation.planner.plan(
                    simulation.world.incidents,
                    simulation.world.services,
                )
                if not next_plan.assignments:
                    raise RuntimeError(
                        "No available service can handle the remaining incidents."
                    )

    print(f"All incidents resolved in {simulation.world.tick_count} ticks.")


if __name__ == "__main__":
    main()