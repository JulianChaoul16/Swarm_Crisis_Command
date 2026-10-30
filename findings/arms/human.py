"""Human arm: a timed terminal quiz over a fixed subset of the cases.

    python -m findings.arms.human

Answers save after every item to results/human.json, so you can stop with Ctrl+C and rerun to
resume where you left off. Do not open cases/answers/ before or during the quiz.

Assignment and replanning show a cost table (severity weight x ETA over the true shortest
routes), so those tasks test the matching decision, not route finding; routing is tested on
its own. The AI arm gets the same tables.
"""

from datetime import datetime, timezone
from time import perf_counter

from findings.common import CASES, RESULTS, load, load_town, save, slots
from findings.views import cost_table, road_list

OUTPUT = RESULTS / "human.json"
VARIANT = "you"

# Fixed subset, sized for roughly 40 minutes. Other arms are also scored on exactly this subset.
SUBSET = {
    "reports": "all",
    "conflicts": "all",
    "routing": ["S01", "S02"],
    "triage": ["S01", "S02", "S03", "S04", "S05", "S06"],
    "assignment": ["S01", "S03", "S05", "S07"],
    "replanning": ["S02", "S04"],
}

KINDS = ["flood_rescue", "evacuation", "fire", "medical", "road_status", "property_damage", "other"]
SEVERITIES = ["critical", "high", "medium", "low"]
NEEDS = {"b": "boat", "m": "medic", "f": "fire", "t": "transport"}


class Quit(Exception):
    pass


def ask(prompt: str, parse, help_text: str | None = None):
    """Prompt until `parse` accepts the input. '?' reprints help, 'q' saves and quits."""
    while True:
        raw = input(prompt).strip()
        if raw.lower() == "q":
            raise Quit
        if raw == "?" and help_text:
            print(help_text)
            continue
        try:
            return parse(raw)
        except (ValueError, IndexError, KeyError) as error:
            print(f"  Not understood ({error}). Type ? for the list, q to save and quit.")


def numbered(items: list[str], columns: int = 3) -> str:
    cells = [f"{index + 1:>2}. {item}" for index, item in enumerate(items)]
    width = max(len(cell) for cell in cells) + 2
    rows = [cells[i:i + columns] for i in range(0, len(cells), columns)]
    return "\n".join("".join(cell.ljust(width) for cell in row) for row in rows)


def pick(items: list[str], allow_blank: bool):
    def parse(raw: str):
        if not raw:
            if allow_blank:
                return None
            raise ValueError("an answer is required")
        index = int(raw) - 1
        if not 0 <= index < len(items):
            raise ValueError(f"choose 1-{len(items)}")
        return items[index]
    return parse


def confirm() -> bool:
    return ask("  Keep this answer? [Enter = yes, n = redo]: ",
               lambda raw: raw.lower() != "n")


class Session:
    def __init__(self) -> None:
        self.town = load_town()
        self.places = [place["id"] for place in self.town["places"]]
        self.roads = [road["id"] for road in self.town["roads"]]
        self.state = load(OUTPUT) if OUTPUT.exists() else {
            "arm": "human",
            "tester": "project author",
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "subset": {},
            "outputs": {},
            "seconds": {},
            "item_seconds": {},
        }

    def done(self, task: str) -> dict:
        return self.state["outputs"].setdefault(task, {}).setdefault(VARIANT, {})

    def record(self, task: str, item_id: str, answer, seconds: float) -> None:
        self.done(task)[item_id] = answer
        self.state["item_seconds"].setdefault(task, {})[item_id] = round(seconds, 1)
        self.state["seconds"].setdefault(task, {})[VARIANT] = round(
            sum(self.state["item_seconds"][task].values()), 1)
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(OUTPUT, self.state)

    def section(self, task: str, ids: list[str], title: str, intro: str) -> list[str]:
        self.state["subset"][task] = ids
        save(OUTPUT, self.state)
        remaining = [item for item in ids if item not in self.done(task)]
        if remaining:
            print(f"\n{'=' * 78}\n{title}  ({len(ids) - len(remaining)}/{len(ids)} done)\n"
                  f"{'=' * 78}\n{intro}")
        return remaining

    # --- Task 1 ---------------------------------------------------------------------

    def reports(self) -> None:
        reports = load(CASES / "reports.json")["reports"]
        lists = (f"Kinds:\n{numbered(KINDS, 4)}\n\nPlaces:\n{numbered(self.places)}\n\n"
                 f"Roads:\n{numbered(self.roads)}")
        todo = self.section(
            "reports", [r["id"] for r in reports], "TASK 1: REPORTS",
            "Fill in each field as the rubric says. Blank = null / none. Type ? for the lists.\n"
            "Needs: letters b=boat m=medic f=fire t=transport, e.g. 'bt'.\n"
            "Road claims: road number + o/c, comma-separated, e.g. '9o, 10c'.\n\n" + lists)
        for report in reports:
            if report["id"] not in todo:
                continue
            while True:
                print(f"\n--- {report['id']}  [{report['source']}, {report['received']}]\n"
                      f"    {report['text']}\n")
                started = perf_counter()
                answer = {
                    "kind": ask("  kind (number): ", pick(KINDS, False), lists),
                    "place": ask("  place (number, blank = none): ", pick(self.places, True),
                                 lists),
                    "people": ask("  people (number, blank = unknown): ",
                                  lambda raw: int(raw) if raw else None),
                    "severity": ask("  severity 1=critical 2=high 3=medium 4=low: ",
                                    pick(SEVERITIES, False)),
                    "needs": ask("  needs (letters, blank = none): ", self._needs),
                    "road_claims": ask("  road claims (e.g. 9o, 10c; blank = none): ",
                                       self._claims, lists),
                }
                if confirm():
                    break
            self.record("reports", report["id"], answer, perf_counter() - started)

    @staticmethod
    def _needs(raw: str) -> list[str]:
        letters = raw.replace(",", "").replace(" ", "").lower()
        return sorted({NEEDS[letter] for letter in letters})

    def _claims(self, raw: str) -> list[dict]:
        claims = []
        for part in filter(None, (piece.strip() for piece in raw.split(","))):
            number, status = part[:-1].strip(), part[-1].lower()
            if status not in "oc":
                raise ValueError("end each claim with o or c")
            claims.append({"road_id": pick(self.roads, False)(number),
                           "status": "open" if status == "o" else "closed"})
        return claims

    # --- Task 2 ---------------------------------------------------------------------

    def conflicts(self) -> None:
        sets = load(CASES / "conflicts.json")["sets"]
        todo = self.section(
            "conflicts", [s["id"] for s in sets], "TASK 2: CONFLICTING ROAD REPORTS",
            "Rules: drop claims >20 min older than the newest; ignore hedged claims; highest "
            "tier wins\n(official > responder > public); newest within that tier; a tie at "
            "that time that disagrees = unknown.")
        choices = ["open", "closed", "unknown"]
        for claim_set in sets:
            if claim_set["id"] not in todo:
                continue
            print(f"\n--- {claim_set['id']}: status of {claim_set['road_id']}?")
            for claim in claim_set["claims"]:
                print(f"    {claim['time']}  {claim['source']:<9} {claim['text']}")
            started = perf_counter()
            answer = ask("  1=open 2=closed 3=unknown: ", pick(choices, False))
            self.record("conflicts", claim_set["id"], answer, perf_counter() - started)

    # --- Tasks 3-6 ------------------------------------------------------------------

    def road_list(self, closed) -> str:
        return road_list(self.town, closed)

    def routing(self, scenarios: dict) -> None:
        ids = [f"{sid}/{q['id']}" for sid in SUBSET["routing"]
               for q in scenarios[sid]["routing_queries"]]
        todo = self.section(
            "routing_items", ids, "TASK 3: ROUTING",
            "Shortest total km over OPEN roads. Type the place numbers in order, start to end,\n"
            "e.g. '14 9 8 6'. Type 'none' if unreachable.")
        if not todo:
            return
        for sid in SUBSET["routing"]:
            scenario = scenarios[sid]
            closed = scenario["closed_roads"]
            print(f"\n--- Scenario {sid}. Closed roads: {', '.join(closed) or 'none'}\n"
                  f"Open roads from each place (km):\n{self.road_list(closed)}")
            for query in scenario["routing_queries"]:
                item = f"{sid}/{query['id']}"
                if item not in todo:
                    continue
                print(f"\n  {item}: from {self.places.index(query['from']) + 1} {query['from']}"
                      f"  to {self.places.index(query['to']) + 1} {query['to']}")
                started = perf_counter()
                path = ask("  path: ", self._path, self.road_list(closed))
                self.record("routing_items", item, path, perf_counter() - started)
        routing = {}
        for item, path in self.done("routing_items").items():
            sid, qid = item.split("/")
            routing.setdefault(sid, {})[qid] = path
        self.state["outputs"]["routing"] = {VARIANT: routing}
        self.state["subset"]["routing"] = SUBSET["routing"]
        self.state["seconds"].setdefault("routing", {})[VARIANT] = \
            self.state["seconds"]["routing_items"][VARIANT]
        save(OUTPUT, self.state)

    def _path(self, raw: str):
        if raw.lower() == "none":
            return None
        return [pick(self.places, False)(token) for token in raw.replace(",", " ").split()]

    def triage(self, scenarios: dict) -> None:
        todo = self.section(
            "triage", SUBSET["triage"], "TASK 4: TRIAGE",
            "Order: severity (critical first), then more people, then earlier minute, then id.\n"
            "Type incident numbers in order, e.g. '3 1 2'.")
        for sid in todo:
            incidents = scenarios[sid]["incidents"]
            print(f"\n--- {sid}")
            for incident in incidents:
                print(f"    {incident['id']}: {incident['severity']:<8} people {incident['people']:>2}"
                      f"  reported minute {incident['reported_minute']}")
            ids = [incident["id"] for incident in incidents]
            started = perf_counter()
            order = ask("  order: ", lambda raw: self._order(raw, ids))
            self.record("triage", sid, order, perf_counter() - started)

    @staticmethod
    def _order(raw: str, ids: list[str]) -> list[str]:
        order = [f"inc-{token}" for token in raw.replace(",", " ").split()]
        if sorted(order) != sorted(ids):
            raise ValueError(f"list each of {len(ids)} incidents once")
        return order

    def cost_table(self, scenario: dict, closed) -> str:
        return cost_table(self.town, scenario, closed)

    def _plan(self, scenario: dict, closed, task: str, sid: str, prior=None) -> None:
        table = self.cost_table(scenario, closed)
        print(table)
        if prior is not None:
            print("  Prior plan: " + (", ".join(f"{e['slot']}={e['unit']}" for e in prior)
                                      or "(nothing assigned)"))
        units = [unit["id"] for unit in scenario["units"]]
        while True:
            started = perf_counter()
            plan = []
            for slot in slots(scenario):
                unit = ask(f"  {slot['slot']} ({slot['need']}) unit number, blank = unfilled: ",
                           pick(units, True), table)
                if unit:
                    plan.append({"slot": slot["slot"], "unit": unit})
            print("  Plan: " + (", ".join(f"{e['slot']}={e['unit']}" for e in plan) or "(empty)"))
            if confirm():
                break
        self.record(task, sid, plan, perf_counter() - started)

    def assignment(self, scenarios: dict) -> None:
        todo = self.section(
            "assignment", SUBSET["assignment"], "TASK 5: ASSIGNMENT",
            "Each cell = cost (severity weight x ETA minutes); '-' = can't (wrong capability or\n"
            "unreachable). Each unit at most once. Minimize the total; an unfilled slot costs\n"
            "its 'unfilled' value.")
        for sid in todo:
            print(f"\n--- {sid}")
            self._plan(scenarios[sid], scenarios[sid]["closed_roads"], "assignment", sid)

    def replanning(self, scenarios: dict) -> None:
        todo = self.section(
            "replanning", SUBSET["replanning"], "TASK 6: REPLANNING",
            "A road just closed. The table already reflects it. Give the new lowest-cost plan.")
        for sid in todo:
            scenario = scenarios[sid]
            closes = scenario["disruption"]["closes"]
            print(f"\n--- {sid}: {closes} just closed")
            self._plan(scenario, [*scenario["closed_roads"], closes], "replanning", sid,
                       scenario["disruption"]["prior_plan"])

    def run(self) -> None:
        scenarios = {s["id"]: s for s in load(CASES / "scenarios.json")["scenarios"]}
        print("Findings quiz. Keep cases/rubric.md open in another window; don't open "
              "cases/answers/.\nAnswers save after each item. Type q at any prompt to stop; "
              "rerun to resume.\nTimer runs per item, from when it's shown until you confirm.")
        self.reports()
        self.conflicts()
        self.routing(scenarios)
        self.triage(scenarios)
        self.assignment(scenarios)
        self.replanning(scenarios)
        self.state["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(OUTPUT, self.state)
        print(f"\nDone. Saved to {OUTPUT}.\nScore everything with: python -m findings.score")


def main() -> None:
    try:
        Session().run()
    except (Quit, KeyboardInterrupt, EOFError):
        print(f"\nSaved progress to {OUTPUT}. Rerun to resume.")


if __name__ == "__main__":
    main()
