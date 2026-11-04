"""START DEMO SCENARIO: plays the seeded Richmond timeline through the normal pipeline.

The timeline (`demo_cache/richmond_flood_scenario.json`) says only what arrives and when: a
gauge reading, an audio report, a citizen photo. Each event goes through the same intake,
swarm, solver and approval path as live input; outcomes (severity, routes, the
recommendation) are computed, not scripted. Model outputs come from `meta_api`: recorded
responses in demo mode, labelled sample data for models this account cannot reach.
"""

import json
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from ..meta_api import ROOT
from ..swarm.board import VISION
from .reports import ReportStatus

SCENARIO_FILE = ROOT / "demo_cache" / "richmond_flood_scenario.json"


def load_timeline(path: Path = SCENARIO_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class DemoRunner:
    """Presenter-controlled: START resets to the seed; each step() plays the next event.

    Stage-2 events (new reports after the first dispatch) wait for an operator approval so
    the story stays in order."""

    def __init__(self, service: Any, timeline: dict[str, Any] | None = None) -> None:
        self.service = service
        self.timeline = timeline or load_timeline()
        self._lock = Lock()
        self._generation = 0
        self.started = False
        self.busy = False
        self.done: list[int] = []
        self.errors: list[str] = []
        self.approvals = 0
        service.approval_listeners.append(self._approved)

    # ----- control -------------------------------------------------------

    def start(self, record: bool = False) -> None:
        """Reset to the seed and the opening gauge reading; nothing plays until step().
        Replays recorded model responses (demo mode) so the run is reliable offline;
        `record=True` calls the live model instead and records fresh responses."""
        self.service.hub.mode = "live" if record else "demo"
        self.service.hub.overwrite = record
        with self._lock:
            self._generation += 1
            self.done, self.errors, self.approvals, self.busy = [], [], 0, False
        self.prepare()
        with self._lock:
            self.started = True

    def prepare(self) -> None:
        """T+0: the seeded world and the opening gauge reading."""
        self.service.reset()
        self.service.board.set_gauge({**self.timeline["gauge"], "label": self.timeline["gauge_label"]},
                                     "scenario value")
        self.service.propose()

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self.started, self.busy = False, False

    def _next(self) -> tuple[int | None, str | None]:
        """The next event to play, or why none can play now. Call under the lock."""
        events = self.timeline["events"]
        pending = [i for i in range(len(events)) if i not in self.done]
        if not self.started:
            return None, "Press START first."
        if self.busy:
            return None, "Still playing the last step."
        if not pending:
            return None, "All steps played."
        if events[pending[0]].get("stage", 1) == 2 and not self.approvals:
            return None, "Approve the recommended dispatch first."
        return pending[0], None

    def step(self, wait: bool = False) -> bool:
        """Play the next event in the background (or inline with wait=True)."""
        with self._lock:
            index, _ = self._next()
            if index is None:
                return False
            self.busy = True
            generation = self._generation
        if wait:
            self._run(index, generation)
        else:
            Thread(target=self._run, args=(index, generation), daemon=True).start()
        return True

    def _run(self, index: int, generation: int) -> None:
        event = self.timeline["events"][index]
        try:
            getattr(self, f"_{event['type']}")(event)
        except Exception as error:  # noqa: BLE001 - shown in the UI; the demo continues
            with self._lock:
                self.errors.append(f"{event['label']}: {error}")
        with self._lock:
            if generation == self._generation:
                self.done.append(index)
                self.busy = False

    def _approved(self, proposal: Any) -> None:
        with self._lock:
            if self.started:
                self.approvals += 1
                # The scripted story is recorded without rerouting; once it is over, later
                # reports (e.g. from a phone) may move committed units. A restart resets this.
                if len(self.done) == len(self.timeline["events"]) and self.approvals >= 2:
                    self.service.planner.reroute = True

    def _awaiting_approval(self) -> bool:
        plan = self.service.current_plan
        return bool(plan and plan.status.value == "proposed" and plan.plan.assignments)

    def _guide(self, finished: bool) -> dict[str, Any]:
        """What the presenter is looking at now, and what the next step is."""
        events = self.timeline["events"]
        stage1 = [i for i, e in enumerate(events) if e.get("stage", 1) == 1]
        pending = [i for i in range(len(events)) if i not in self.done]
        upcoming = events[pending[0]]["label"] if pending else None
        if finished:
            return {"now": self.timeline["final"], "your_turn": False, "next": None}
        if self.busy:
            playing = events[pending[0]]["label"] if pending else ""
            return {"now": f"Playing: {playing}...", "your_turn": False, "next": None}
        if all(i in self.done for i in stage1) and not self.approvals:
            return {"now": self.timeline["outro"], "your_turn": True, "next": upcoming}
        if not pending:
            return {"now": self.timeline["outro_2"], "your_turn": self._awaiting_approval(), "next": None}
        current = self.timeline["intro"] if not self.done else events[self.done[-1]]["explain"]
        if self.approvals and self.done and self.done[-1] in stage1:
            current = self.timeline["approved"]
        return {"now": current, "your_turn": False, "next": upcoming}

    def status(self) -> dict[str, Any]:
        with self._lock:
            # Finished once every event has played and the last recommendation was approved.
            finished = (self.started and len(self.done) == len(self.timeline["events"])
                        and self.approvals >= 2 and not self._awaiting_approval())
            _, blocked = self._next()
            return {
                "running": self.started and not finished,
                "finished": finished,
                "busy": self.busy,
                "step": len(self.done),
                "total": len(self.timeline["events"]),
                "can_step": blocked is None,
                "blocked": blocked,
                "events": [{"stage": e.get("stage", 1), "label": e["label"], "done": i in self.done}
                           for i, e in enumerate(self.timeline["events"])],
                "approvals": self.approvals,
                "guide": self._guide(finished) if self.started else None,
                "errors": list(self.errors),
            }

    def play_all_now(self, approve: bool = False) -> None:
        """Run every event immediately (tests and recording); optionally approve at each
        point the presenter would."""
        events = self.timeline["events"]
        for index, event in enumerate(events):
            if approve and event.get("stage", 1) == 2 and self._awaiting_approval():
                self._approve_current()
            getattr(self, f"_{event['type']}")(event)
            self.done.append(index)
        if approve and self._awaiting_approval():
            self._approve_current()

    def _approve_current(self) -> None:
        plan = self.service.current_plan
        self.service.approve(plan.id, plan.world_version, actor="operator (recording run)")

    # ----- events ----------------------------------------------------------

    def _auto_confirm(self, report_id: str, actor: str) -> None:
        report = next(r for r in self.service.reports if r.id == report_id)
        if report.status == ReportStatus.READY:
            self.service.confirm_report(report_id, actor=actor)

    def _audio_report(self, event: dict[str, Any]) -> None:
        """A 911 call: Listener transcribes, the report goes through normal intake."""
        board, hub = self.service.board, self.service.hub
        board.log("Listener Agent", "Muse Voice Transcribe", f"Listening to {event['audio_id']} ({event['label']})")
        transcript = hub.transcribe_file(ROOT / event["audio_file"])
        if transcript.value is None:
            board.log("Listener Agent", transcript.label, f"No transcript for {event['audio_id']}: {transcript.note}")
            return
        text = transcript.value["text"]
        board.transcripts.append({"audio_id": event["audio_id"], "text": text, "source": transcript.label,
                                  "note": "Synthesized test call (Windows text-to-speech), transcribed by Muse Voice Transcribe.",
                                  "audio_url": f"/api/media/audio/{Path(event['audio_file']).name}"})
        board.log("Listener Agent", transcript.label, f"Transcribed {event['audio_id']}: \"{text[:90]}...\"")
        report = self.service.submit_report(text, event["source"])
        if report.extraction_error:
            board.log("Intake Agent", "intake", f"{report.id} could not be read: {report.extraction_error}")
            return
        board.log("Intake Agent", report.extracted_by or "intake",
                  f"{report.id} read as {report.fields.kind} at {report.fields.location}, "
                  f"{report.fields.people} people ({report.status.value})")
        self._auto_confirm(report.id, "auto-apply (complete report)")

    def _photo_report(self, event: dict[str, Any]) -> None:
        """A citizen photo: SAM 3.1 finds the flooded street and vehicles, Muse Wonder judges
        depth and passability; the coordinator applies an impassable reading as a road
        closure, and the swarm re-votes."""
        board, hub = self.service.board, self.service.hub
        image = ROOT / event["image_file"]
        prompts = event["prompts"]
        boxes, estimate = hub.segment(image, list(prompts.values())), hub.assess_water(image)
        if boxes.value is None or estimate.value is None:
            board.log(VISION, boxes.label if boxes.value is None else estimate.label,
                      f"No imagery result for {event['image_id']}: {boxes.note if boxes.value is None else estimate.note}")
            return
        width, height = boxes.value["size"]
        water = boxes.value["objects"].get(prompts["water"], [])
        vehicles = boxes.value["objects"].get(prompts.get("vehicles", ""), [])
        percent = _covered_percent(water, width, height)
        west, south, east, north = event["footprint"]

        def to_map(box: list[int]) -> dict[str, Any]:
            x1, y1, x2, y2 = box
            lng = lambda x: round(west + (east - west) * x / width, 6)  # noqa: E731
            lat = lambda y: round(north - (north - south) * y / height, 6)  # noqa: E731
            ring = [[lng(x1), lat(y1)], [lng(x2), lat(y1)], [lng(x2), lat(y2)], [lng(x1), lat(y2)], [lng(x1), lat(y1)]]
            return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {"label": prompts["water"]}}

        value = estimate.value
        board.imagery.append({
            "image_id": event["image_id"], "place": event["place"], "road_id": event["road_id"],
            "image_url": f"/api/media/{image.parent.name}/{image.name}", "size": [width, height],
            "objects": boxes.value["objects"], "boxes_source": boxes.label,
            "map_features": [to_map(box) for box in water],
            "estimate": value, "estimate_source": estimate.label,
            "note": (f"{boxes.note}. The photo is an AI-generated test image; its position on the map is an "
                     f"assumed footprint around {event['place']}, not georeferenced."),
        })
        board.log(VISION, boxes.label, f"'{prompts['water']}': {len(water)} regions, about {percent}% of the photo; "
                  f"{len(vehicles)} vehicles found")
        board.log(VISION, estimate.label, f"Est. {value['depth_in_low']}-{value['depth_in_high']} in, "
                  f"{'passable' if value['passable_for_cars'] else 'impassable'} for cars "
                  f"({round(value['confidence'] * 100)}% model confidence): {value['reason']}")
        board.set_flood_cover(event["place"], {"percent": percent, "place": event["place"],
                                               "source": boxes.label}, VISION, boxes.label)
        if not value["passable_for_cars"] and not value["water_on_road"]:
            board.log(VISION, estimate.label, "Model fields disagree (no water on road, yet impassable); "
                      "treated as impassable, the safer reading")
        if not value["passable_for_cars"]:
            report = self.service.submit_report(
                f"Vision Agent: {event['road_id']} impassable in citizen photo {event['image_id']} "
                f"(est. {value['depth_in_low']}-{value['depth_in_high']} in of water; {value['reason']}).",
                f"Vision Agent ({boxes.label}, {estimate.label})",
                fields={"report_type": "road_status", "road_id": event["road_id"], "road_closed": True,
                        "certainty": "confirmed"},
                entered_by=f"Vision Agent ({boxes.label}, {estimate.label})",
            )
            self.service.confirm_report(report.id, actor="coordinator (imagery evidence)")
        else:
            self.service.propose()

    def _unit_report(self, event: dict[str, Any]) -> None:
        """A field unit reports its incident resolved (scripted scenario message)."""
        self.service.resolve_incident(event["incident_id"], f"{event['unit_name']} (field unit, scripted scenario message)",
                                      event["text"])

    def _gauge(self, event: dict[str, Any]) -> None:
        self.service.board.set_gauge({"value": event["value"], "trend": event["trend"],
                                      "scenario_hour": event.get("scenario_hour"),
                                      "site": self.timeline["gauge"]["site"],
                                      "label": self.timeline["gauge_label"]}, "scenario value")
        self.service.propose()


def _covered_percent(boxes: list[list[int]], width: int, height: int, cells: int = 80) -> int:
    """Share of the photo inside the union of the boxes, on an 80 x 80 grid (approximate)."""
    if not boxes or not width or not height:
        return 0
    covered = 0
    for row in range(cells):
        y = (row + 0.5) * height / cells
        for col in range(cells):
            x = (col + 0.5) * width / cells
            covered += any(x1 <= x <= x2 and y1 <= y <= y2 for x1, y1, x2, y2 in boxes)
    return round(100 * covered / cells / cells)
