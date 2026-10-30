"""AI arm: the same cases, rubric and views as the human quiz, answered by a model.

    python -m findings.arms.ai_agent [--effort low] [--runs 3] [--tasks reports,conflicts]
                                     [--limit N] [--workers 6]

Reads META_API_KEY, META_MODEL and META_BASE_URL (a Responses endpoint) from the environment,
then from .env at the repo root, then from crisis_command/agents/.env. The key is never logged.
Requests set store=false. Every answer is constrained to a strict JSON schema.

The model works unaided: no tools, no code execution. Assignment and replanning get the same
cost table the human saw. A failed or truncated call is recorded as a missing answer, which
scores as wrong; nothing is retried into a different answer or filled in.
Writes results/ai_<model>_<effort>.json with one variant per run (run1, run2, ...).
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from findings.common import CASES, RESULTS, ROOT, load, load_town, save, slots
from findings.views import cost_table, road_list

TASKS = ["reports", "conflicts", "routing", "triage", "assignment", "replanning"]
KINDS = ["flood_rescue", "evacuation", "fire", "medical", "road_status", "property_damage", "other"]
SEVERITIES = ["critical", "high", "medium", "low"]
NEEDS = ["boat", "medic", "fire", "transport"]
MAX_OUTPUT_TOKENS = 16000
RETRIES = 3


# --- Model access ----------------------------------------------------------------------

def _read_env_file(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def model_settings() -> dict[str, str]:
    repo = ROOT.parent
    found = {**_read_env_file(repo / "crisis_command" / "agents" / ".env"),
             **_read_env_file(repo / ".env")}
    settings = {name: os.environ.get(name) or found.get(name, "")
                for name in ("META_API_KEY", "META_MODEL", "META_BASE_URL")}
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise SystemExit(f"Missing {', '.join(missing)} (see .env.example).")
    return settings


class ResponsesClient:
    def __init__(self, settings: dict[str, str], effort: str) -> None:
        self._key = settings["META_API_KEY"]
        self.model = settings["META_MODEL"]
        self.url = settings["META_BASE_URL"]
        self.effort = effort

    def ask(self, instructions: str, prompt: str, name: str, schema: dict) -> dict:
        """One structured call. Returns {"answer" | "error", "seconds", "usage"}."""
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": prompt,
            "reasoning": {"effort": self.effort},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
            "text": {"format": {"type": "json_schema", "name": name, "schema": schema,
                                "strict": True}},
        }
        started = time.perf_counter()
        for attempt in range(RETRIES + 1):
            request = urllib.request.Request(
                self.url, data=json.dumps(body).encode(), method="POST",
                headers={"Authorization": f"Bearer {self._key}",
                         "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    data = json.loads(response.read())
                break
            except urllib.error.HTTPError as error:
                # Only transport-level failures are retried; the same request is resent.
                if error.code in (429, 500, 502, 503, 504) and attempt < RETRIES:
                    time.sleep(2 ** attempt * 5)
                    continue
                return {"error": f"HTTP {error.code}: {error.read().decode()[:300]}",
                        "seconds": time.perf_counter() - started, "usage": None}
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt < RETRIES:
                    time.sleep(2 ** attempt * 5)
                    continue
                return {"error": f"network: {error}", "seconds": time.perf_counter() - started,
                        "usage": None}
        seconds = time.perf_counter() - started
        usage = data.get("usage")
        if data.get("status") != "completed":
            return {"error": f"status {data.get('status')}: {data.get('incomplete_details')}",
                    "seconds": seconds, "usage": usage}
        texts = [part["text"] for item in data.get("output", []) if item.get("type") == "message"
                 for part in item.get("content") or [] if part.get("type") == "output_text"]
        try:
            return {"answer": json.loads("".join(texts)), "seconds": seconds, "usage": usage}
        except json.JSONDecodeError:
            return {"error": "output was not valid JSON", "seconds": seconds, "usage": usage}


# --- Prompts and schemas ---------------------------------------------------------------

def _obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def _enum(values: list, nullable: bool = False) -> dict:
    if nullable:
        return {"type": ["string", "null"], "enum": [*values, None]}
    return {"type": "string", "enum": values}


class Tasks:
    """Builds (item_id, prompt, schema name, schema) jobs and converts answers to arm output."""

    def __init__(self, town: dict) -> None:
        self.town = town
        self.places = [place["id"] for place in town["places"]]
        self.roads = [road["id"] for road in town["roads"]]
        self.rubric = (CASES / "rubric.md").read_text(encoding="utf-8")
        gazetteer = {
            "places": [{"id": p["id"], "name": p["name"], "aliases": p["aliases"]}
                       for p in town["places"]],
            "roads": [{"id": r["id"], "between": [r["a"], r["b"]], "aliases": r["aliases"]}
                      for r in town["roads"]],
        }
        self.instructions = (
            "You are a dispatcher's assistant in a flood exercise. Follow the rubric exactly. "
            "Answer only with JSON matching the schema.\n\n" + self.rubric +
            "\n\n# Town gazetteer\n" + json.dumps(gazetteer, indent=1))

    def reports(self, limit):
        schema = _obj({
            "kind": _enum(KINDS),
            "place": _enum(self.places, nullable=True),
            "people": {"type": ["integer", "null"]},
            "severity": _enum(SEVERITIES),
            "needs": {"type": "array", "items": _enum(NEEDS)},
            "road_claims": {"type": "array", "items": _obj({
                "road_id": _enum(self.roads), "status": _enum(["open", "closed"])})},
        })
        for report in load(CASES / "reports.json")["reports"][:limit]:
            prompt = (f"Task 1 (Reports). Source: {report['source']}, received "
                      f"{report['received']}.\nReport: {report['text']}")
            yield report["id"], prompt, "report", schema

    @staticmethod
    def convert_reports(answers: dict) -> dict:
        return answers

    def conflicts(self, limit):
        schema = _obj({"status": _enum(["open", "closed", "unknown"])})
        for claim_set in load(CASES / "conflicts.json")["sets"][:limit]:
            claims = "\n".join(f"- {c['time']} {c['source']}: {c['text']}"
                               for c in claim_set["claims"])
            prompt = (f"Task 2 (Conflicting road reports). What is the status of "
                      f"{claim_set['road_id']}?\nClaims:\n{claims}")
            yield claim_set["id"], prompt, "conflict", schema

    @staticmethod
    def convert_conflicts(answers: dict) -> dict:
        return {item: answer["status"] for item, answer in answers.items()}

    def routing(self, scenarios, limit):
        for scenario in scenarios[:limit]:
            ids = [query["id"] for query in scenario["routing_queries"]]
            schema = _obj({"answers": {"type": "array", "items": _obj({
                "query_id": _enum(ids),
                "reachable": {"type": "boolean"},
                "path": {"type": "array", "items": _enum(self.places)},
            })}})
            queries = "\n".join(f"- {q['id']}: from {q['from']} to {q['to']}"
                                for q in scenario["routing_queries"])
            prompt = (f"Task 3 (Routing). Closed roads: "
                      f"{', '.join(scenario['closed_roads']) or 'none'}.\n"
                      f"Open roads from each place (number, place id, length in km):\n"
                      f"{road_list(self.town, scenario['closed_roads'])}\n\n"
                      f"Queries:\n{queries}\nFor each query give the full path of place ids "
                      f"from start to end, or reachable=false with an empty path.")
            yield scenario["id"], prompt, "routes", schema

    @staticmethod
    def convert_routing(answers: dict) -> dict:
        return {
            item: {entry["query_id"]: (entry["path"] if entry["reachable"] else None)
                   for entry in answer["answers"]}
            for item, answer in answers.items()
        }

    def triage(self, scenarios, limit):
        for scenario in scenarios[:limit]:
            ids = [incident["id"] for incident in scenario["incidents"]]
            schema = _obj({"order": {"type": "array", "items": _enum(ids)}})
            lines = "\n".join(f"- {i['id']}: severity {i['severity']}, people {i['people']}, "
                              f"reported minute {i['reported_minute']}"
                              for i in scenario["incidents"])
            yield scenario["id"], f"Task 4 (Triage). Incidents:\n{lines}", "triage", schema

    @staticmethod
    def convert_triage(answers: dict) -> dict:
        return {item: answer["order"] for item, answer in answers.items()}

    def _plan_schema(self, scenario: dict) -> dict:
        return _obj({"plan": {"type": "array", "items": _obj({
            "slot": _enum([slot["slot"] for slot in slots(scenario)]),
            "unit": _enum([unit["id"] for unit in scenario["units"]]),
        })}})

    def assignment(self, scenarios, limit):
        for scenario in scenarios[:limit]:
            prompt = ("Task 5 (Assignment). Each cell is the cost (severity weight x ETA "
                      "minutes) of sending that unit to that slot; '-' means not allowed "
                      "(wrong capability or unreachable). Each unit at most once. An unfilled "
                      "slot costs its 'unfilled' value. Minimize the total cost; leave unfilled "
                      "slots out of the plan.\n\n" +
                      cost_table(self.town, scenario, scenario["closed_roads"]))
            yield scenario["id"], prompt, "plan", self._plan_schema(scenario)

    def replanning(self, scenarios, limit):
        for scenario in [s for s in scenarios if s.get("disruption")][:limit]:
            closed = [*scenario["closed_roads"], scenario["disruption"]["closes"]]
            prior = ", ".join(f"{e['slot']}={e['unit']}"
                              for e in scenario["disruption"]["prior_plan"]) or "nothing"
            prompt = (f"Task 6 (Replanning). {scenario['disruption']['closes']} has just "
                      f"closed; the table already reflects it. Prior plan: {prior}.\n"
                      "Each cell is the cost (severity weight x ETA minutes); '-' means not "
                      "allowed. Each unit at most once. An unfilled slot costs its 'unfilled' "
                      "value. Give the new lowest-cost plan; leave unfilled slots out.\n\n" +
                      cost_table(self.town, scenario, closed))
            yield scenario["id"], prompt, "plan", self._plan_schema(scenario)

    @staticmethod
    def convert_plans(answers: dict) -> dict:
        return {item: answer["plan"] for item, answer in answers.items()}


# --- Run -------------------------------------------------------------------------------

def run(effort: str, runs: int, tasks: list[str], limit: int | None, workers: int) -> Path:
    client = ResponsesClient(model_settings(), effort)
    town = load_town()
    scenarios = load(CASES / "scenarios.json")["scenarios"]
    builder = Tasks(town)
    jobs_by_task = {
        "reports": (builder.reports(limit), builder.convert_reports),
        "conflicts": (builder.conflicts(limit), builder.convert_conflicts),
        "routing": (builder.routing(scenarios, limit), builder.convert_routing),
        "triage": (builder.triage(scenarios, limit), builder.convert_triage),
        "assignment": (builder.assignment(scenarios, limit), builder.convert_plans),
        "replanning": (builder.replanning(scenarios, limit), builder.convert_plans),
    }
    jobs = [(task, run_number, item, prompt, name, schema)
            for task in tasks
            for item, prompt, name, schema in list(jobs_by_task[task][0])
            for run_number in range(1, runs + 1)]
    print(f"{len(jobs)} calls to {client.model} (effort {effort}), {workers} at a time")

    done = 0

    def call(job):
        nonlocal done
        task, run_number, item, prompt, name, schema = job
        result = client.ask(builder.instructions, prompt, name, schema)
        done += 1
        if "error" in result or done % 10 == 0:
            print(f"  {done}/{len(jobs)}" + (f"  {task} {item} run{run_number}: "
                                            f"{result['error']}" if "error" in result else ""))
        return job, result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        finished = list(pool.map(call, jobs))

    outputs: dict = {}
    seconds: dict = {}
    calls: list[dict] = []
    for (task, run_number, item, _, _, _), result in finished:
        variant = f"run{run_number}"
        answers = outputs.setdefault(task, {}).setdefault(variant, {})
        if "answer" in result:
            answers[item] = result["answer"]
        seconds.setdefault(task, {}).setdefault(variant, 0.0)
        seconds[task][variant] += result["seconds"]
        usage = result.get("usage") or {}
        calls.append({
            "task": task, "run": run_number, "item": item,
            "seconds": round(result["seconds"], 2),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
            "error": result.get("error"),
        })
    for task in outputs:
        converter = jobs_by_task[task][1]
        outputs[task] = {variant: converter(answers) for variant, answers in outputs[task].items()}

    path = RESULTS / f"ai_{client.model}_{effort}.json"
    save(path, {
        "arm": f"ai {client.model} ({effort})",
        "model": client.model,
        "reasoning_effort": effort,
        "tools": "none",
        "runs": runs,
        "limit": limit,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "outputs": outputs,
        "seconds": seconds,
        "calls": calls,
    })
    failed = sum(1 for call_row in calls if call_row["error"])
    print(f"Wrote {path} ({failed} failed calls). Score with: python -m findings.score")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--effort", default="low",
                        choices=["minimal", "low", "medium", "high", "xhigh"])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--tasks", default=",".join(TASKS))
    parser.add_argument("--limit", type=int, default=None, help="first N items per task")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    tasks = [task.strip() for task in args.tasks.split(",") if task.strip()]
    unknown = set(tasks) - set(TASKS)
    if unknown:
        raise SystemExit(f"Unknown tasks: {', '.join(sorted(unknown))}")
    run(args.effort, args.runs, tasks, args.limit, args.workers)


if __name__ == "__main__":
    main()
