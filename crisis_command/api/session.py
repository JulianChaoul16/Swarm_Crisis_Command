"""The single command service shared by API requests, the demo timeline runner, and the
incoming-report connectors.

DEMO_ENABLED (in .env, default false) chooses the mode:
- false: live intake. Richmond roads and units with no incidents; reports arrive from the
  /report page, POST /api/intake and any configured database connector.
- true: the seeded Richmond flood and START DEMO SCENARIO.

Model-written text (recommendation explanations, public alerts) attaches in the background
so plans never wait for it.
"""

from ..connectors import ConnectorRunner, connectors_from_env
from ..meta_api import setting
from ..operations import DEMO_FLOOD_SCENARIO, LIVE_RICHMOND_SCENARIO, CommandService
from ..operations.demo import DemoRunner

DEMO_ENABLED = setting("DEMO_ENABLED").strip().lower() in ("1", "true", "yes", "on")

service = CommandService(scenario=DEMO_FLOOD_SCENARIO if DEMO_ENABLED else LIVE_RICHMOND_SCENARIO,
                         background_models=True)
demo = DemoRunner(service) if DEMO_ENABLED else None


def _deliver(report) -> None:
    service.intake_message(report.text, f"{report.source} [{report.external_id}]")


def _connector_error(name: str, error: str) -> None:
    service.board.log("Intake Agent", "connector", f"{name}: {error}")


connectors = ConnectorRunner(
    connectors_from_env(setting), _deliver,
    interval_s=float(setting("CONNECTOR_POLL_SECONDS") or 5), on_error=_connector_error,
)
