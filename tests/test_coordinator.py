import unittest

from crisis_command.agents import AuthoritativeStateAgent
from crisis_command.coordinator import Coordinator, UnserviceableIncidentsError
from crisis_command.main import build_demo_world
from crisis_command.model import Coordinate, Incident, Severity
from crisis_command.simulation import World


class CoordinatorTests(unittest.TestCase):
    def test_coordinator_uses_authoritative_state_agent_as_planning_head(self) -> None:
        coordinator = Coordinator(build_demo_world())

        self.assertIsInstance(coordinator.state_agent, AuthoritativeStateAgent)
        self.assertIs(coordinator.planner.state_agent, coordinator.state_agent)

    def test_coordinator_runs_the_existing_simulation(self) -> None:
        coordinator = Coordinator(build_demo_world())

        plan = coordinator.step()

        self.assertTrue(plan.assignments)
        self.assertEqual(coordinator.world.tick_count, 1)

    def test_coordinator_reports_incident_without_capable_service(self) -> None:
        incident = Incident(
            "unserviceable",
            "fire",
            Coordinate(0, 0),
            Severity.CRITICAL,
            frozenset({"fire"}),
        )
        coordinator = Coordinator(World(incidents=[incident]))

        with self.assertRaises(UnserviceableIncidentsError) as raised:
            coordinator.step()

        self.assertEqual(raised.exception.incident_ids, ("unserviceable",))


if __name__ == "__main__":
    unittest.main()
