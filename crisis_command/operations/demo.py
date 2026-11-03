"""START DEMO SCENARIO: plays the seeded Richmond timeline through the normal pipeline.

The timeline (`demo_cache/richmond_flood_scenario.json`) says only what arrives and when: a
gauge reading, an audio report, a citizen photo. Each event goes through the same intake,
swarm, solver and approval path as live input; outcomes (severity, routes, the
recommendation) are computed, not scripted. Model outputs come from `meta_api`: recorded
responses in demo mode, labelled sample data for models this account cannot reach.
"""

import json
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any

from ..meta_api import ROOT, SAM, MUSE_WONDER
from ..swarm.board import VISION
from .reports import ReportStatus

SCENARIO_FILE = ROOT / "demo_cache" / "richmond_flood_scenario.json"


def load_timeline(path: Path = SCENARIO_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class DemoRunner:
    def __init__(self, service: Any, timeline: dict[str, Any] | None = None, speed: float = 1.0) -> None:
        self.service = service
        self.timeline = timeline or load_timeline()
        self.speed = speed  # >1 plays faster (tests)
        self._lock = Lock()
        self._stop = Event()
        self._generation = 0
        self.started: float | None = None
        self.done: list[int] = []
        self.errors: list[str] = []

    # ----- control -------------------------------------------------------

    def start(self, record: bool = False) -> None:
        """Reset to the seed, set the opening gauge, then play the timed events.

        Replays recorded model responses (demo mode) so the run is reliable offline;
        `record=True` calls the live model instead and records fresh responses."""
        self.service.hub.mode = "live" if record else "demo"
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._stop.set()
            self._stop = Event()
            self.done, self.errors = [], []
        self.prepare()
        with self._lock:
            self.started = monotonic()
        Thread(target=self._play, args=(generation, self._stop), daemon=True).start()

    def prepare(self) -> None:
        """T+0: the seeded world and the opening gauge reading."""
        self.service.reset()
        self.service.board.set_gauge({**self.timeline["gauge"], "label": self.timeline["gauge_label"]},
                                     "scenario value")
        self.service.propose()

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._stop.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            elapsed = (monotonic() - self.started) * self.speed if self.started else None
            return {
                "running": self.started is not None and len(self.done) < len(self.timeline["events"]),
                "elapsed_s": round(elapsed, 1) if elapsed is not None else None,
                "events": [{"at": e["at"], "label": e["label"], "done": i in self.done}
                           for i, e in enumerate(self.timeline["events"])],
                "errors": list(self.errors),
            }

    def _play(self, generation: int, stop: Event) -> None:
        for index, event in enumerate(self.timeline["events"]):
            wait = event["at"] / self.speed - (monotonic() - self.started)
            if wait > 0 and stop.wait(wait):
                return
            with self._lock:
                if generation != self._generation:
                    return
            try:
                getattr(self, f"_{event['type']}")(event)
            except Exception as error:  # noqa: BLE001 - shown in the UI; the demo continues
                with self._lock:
                    self.errors.append(f"{event['label']}: {error}")
            with self._lock:
                if generation == self._generation:
                    self.done.append(index)

    def play_all_now(self) -> None:
        """Run every event immediately (tests and recording)."""
        for index, event in enumerate(self.timeline["events"]):
            getattr(self, f"_{event['type']}")(event)
            self.done.append(index)

    # ----- events ----------------------------------------------------------

    def _auto_confirm(self, report_id: str, actor: str) -> None:
        report = next(r for r in self.service.reports if r.id == report_id)
        if report.status == ReportStatus.READY:
            self.service.confirm_report(report_id, actor=actor)

    def _audio_report(self, event: dict[str, Any]) -> None:
        """A 911 call: Listener transcribes, the report goes through normal intake."""
        board, hub = self.service.board, self.service.hub
        transcript = hub.transcribe(event["audio_id"])
        if transcript.value is None:
            board.log("Listener Agent", transcript.label, f"No transcript for {event['audio_id']}: {transcript.note}")
            return
        text = transcript.value["text"]
        board.transcripts.append({"audio_id": event["audio_id"], "text": text, "source": transcript.label,
                                  "note": transcript.note})
        board.log("Listener Agent", transcript.label, f"Transcribed {event['audio_id']}: \"{text[:90]}...\"")
        report = self.service.submit_report(text, event["source"])
        if report.extraction_error:
            board.log("Triage Agent", "intake", f"{report.id} could not be read: {report.extraction_error}")
            return
        board.log("Triage Agent", report.extracted_by or "intake",
                  f"{report.id} read as {report.fields.kind} at {report.fields.location}, "
                  f"{report.fields.people} people ({report.status.value})")
        self._auto_confirm(report.id, "auto-apply (complete report)")

    def _photo_report(self, event: dict[str, Any]) -> None:
        """A citizen photo: Vision segments water, estimates depth; the coordinator applies
        an impassable estimate as a road closure, and the swarm re-votes."""
        board, hub = self.service.board, self.service.hub
        mask, estimate = hub.segment(event["image_id"]), hub.assess_water(event["image_id"])
        if mask.value is None or estimate.value is None:
            board.log(VISION, mask.label, f"No imagery result for {event['image_id']}")
            return
        board.imagery.append({"image_id": event["image_id"], "place": event["place"], "road_id": event["road_id"],
                              "mask": mask.value, "mask_source": mask.label, "estimate": estimate.value,
                              "estimate_source": estimate.label, "note": f"{mask.note}; {estimate.note}"})
        board.log(VISION, mask.label, f"Water detected in photo {event['image_id']} at {event['place']}")
        board.log(VISION, estimate.label, f"Est. {estimate.value['depth_in']} in, "
                  f"{'impassable' if not estimate.value['passable'] else 'passable'} "
                  f"({round(estimate.value['confidence'] * 100)}% sample confidence)")
        board.set_flood_cover(event["place"], {"percent": mask.value["properties"]["flooded_percent"],
                                               "place": event["place"], "source": f"{SAM} · {mask.source}"},
                              VISION, mask.label)
        if not estimate.value["passable"]:
            report = self.service.submit_report(
                f"Vision Agent: {event['road_id']} impassable in citizen photo {event['image_id']} "
                f"(est. {estimate.value['depth_in']} in of water).",
                f"Vision Agent ({SAM} + {MUSE_WONDER}, sample imagery)",
                fields={"report_type": "road_status", "road_id": event["road_id"], "road_closed": True,
                        "certainty": "confirmed"},
                entered_by=f"Vision Agent ({mask.label}, {estimate.label})",
            )
            self.service.confirm_report(report.id, actor="coordinator (imagery evidence)")
        else:
            self.service.propose()

    def _gauge(self, event: dict[str, Any]) -> None:
        self.service.board.set_gauge({"value": event["value"], "trend": event["trend"],
                                      "site": self.timeline["gauge"]["site"],
                                      "label": self.timeline["gauge_label"]}, "scenario value")
        self.service.propose()
