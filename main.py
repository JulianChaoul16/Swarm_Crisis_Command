from model import Coordinate, EmergencyService, Incident, IncidentStatus, Severity
from simulation import Simulation, World


def build_demo_world() -> World:
    return World(
        incidents=[
            Incident(
                id="incident-1",
                kind="building fire",
                location=Coordinate(8, 5),
                severity=Severity.CRITICAL,
                required_services=frozenset({"fire", "medical"}),
            ),
            Incident(
                id="incident-2",
                kind="road collision",
                location=Coordinate(2, 4),
                severity=Severity.HIGH,
                required_services=frozenset({"medical", "police"}),
            ),
        ],
        services=[
            EmergencyService(
                id="unit-fire-1",
                name="Fire engine 1",
                location=Coordinate(1, 1),
                capabilities=frozenset({"fire"}),
                speed=2.0,
            ),
            EmergencyService(
                id="unit-medical-1",
                name="Ambulance 1",
                location=Coordinate(1, 2),
                capabilities=frozenset({"medical"}),
                speed=2.5,
            ),
            EmergencyService(
                id="unit-police-1",
                name="Police unit 1",
                location=Coordinate(0, 4),
                capabilities=frozenset({"police"}),
                speed=3.0,
            ),
        ],
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