"""Re-record the demo's model responses with the live model, approving at each point a
presenter would. Run from the repo root: python tools/record_demo.py"""

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crisis_command.database import connection  # noqa: E402

patch.object(connection, "DB_PATH", Path(tempfile.mkdtemp()) / "record.db").start()
from crisis_command.operations import DEMO_FLOOD_SCENARIO, CommandService  # noqa: E402
from crisis_command.operations.demo import DemoRunner  # noqa: E402

service = CommandService(scenario=DEMO_FLOOD_SCENARIO)
demo = DemoRunner(service)
service.hub.mode, service.hub.overwrite = "live", True
demo.prepare()
demo.play_all_now(approve=True)
fallbacks = [a.text for a in service.board.activity if a.source == "fallback"]
print(f"recorded; calls={len(service.hub.calls)}; approvals={len([p for p in service._plans.values() if p.status.value == 'approved'])}; fallbacks={fallbacks}")
for p in service._plans.values():
    print(p.id, p.status.value, [(d.service_id, d.incident_id) for d in p.plan.assignments], "unmet", p.plan.unassigned_incident_ids)
