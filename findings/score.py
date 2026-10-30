"""Score every arm in results/ against the answer keys.

    python -m findings.score

Reads results/*.json (each {"arm", "outputs": {task: {variant: ...}}, "seconds"?}) and writes
results/scores.json and results/summary.md. A missing answer counts as wrong. Assignment and
replanning costs use the oracle's brute-force distances, never an arm's own numbers.
"""

from statistics import mean

from findings.common import (
    ANSWERS,
    CASES,
    RESULTS,
    SEVERITY_RANK,
    churn,
    evaluate_plan,
    load,
    load_town,
    path_length,
    save,
)
from findings.oracle import all_routes, distance_table

REPORT_FIELDS = ["kind", "place", "people", "severity", "needs", "road_claims"]
TOLERANCE = 1e-6


def _claims(value) -> frozenset:
    return frozenset((claim.get("road_id"), claim.get("status")) for claim in value or [])


def score_reports(outputs: dict, key: dict) -> dict:
    correct = {field: 0 for field in REPORT_FIELDS}
    severity_near = invented = 0
    for report_id, expected in key.items():
        got = outputs.get(report_id) or {}
        for field in REPORT_FIELDS:
            if field == "needs":
                match = set(got.get(field) or []) == set(expected[field])
            elif field == "road_claims":
                match = _claims(got.get(field)) == _claims(expected[field])
            else:
                match = got.get(field) == expected[field]
            correct[field] += match
        if got.get("severity") in SEVERITY_RANK and abs(
            SEVERITY_RANK[got["severity"]] - SEVERITY_RANK[expected["severity"]]
        ) <= 1:
            severity_near += 1
        invented += sum(got.get(field) is not None and expected[field] is None
                        for field in ("place", "people"))
        invented += len(_claims(got.get("road_claims")) - _claims(expected["road_claims"]))
    count = len(key)
    fields = {field: correct[field] / count for field in REPORT_FIELDS}
    return {
        "n": count,
        "overall": mean(fields.values()),
        "fields": fields,
        "severity_within_one": severity_near / count,
        "invented_values": invented,
    }


def score_conflicts(outputs: dict, key: dict) -> dict:
    right = sum(outputs.get(set_id) == status for set_id, status in key.items())
    return {"n": len(key), "accuracy": right / len(key)}


def score_routing(outputs: dict, scenarios: dict, key: dict, town: dict) -> dict:
    optimal = invalid = 0
    gaps = []
    total = 0
    for scenario_id, answers in key.items():
        closed = scenarios[scenario_id]["closed_roads"]
        queries = {query["id"]: query for query in scenarios[scenario_id]["routing_queries"]}
        for query_id, expected in answers["routing"].items():
            total += 1
            path = (outputs.get(scenario_id) or {}).get(query_id, "missing")
            if expected["distance"] is None:
                optimal += path is None
                invalid += path is not None
                continue
            query = queries[query_id]
            if not isinstance(path, list) or not path or path[0] != query["from"] or path[-1] != query["to"]:
                invalid += 1
                continue
            length = path_length(town, closed, path)
            if length is None:
                invalid += 1
                continue
            gap = length / expected["distance"] - 1 if expected["distance"] else 0.0
            gaps.append(gap)
            optimal += gap <= TOLERANCE
    return {
        "n": total,
        "optimal": optimal / total,
        "invalid": invalid / total,
        "mean_extra_distance": mean(gaps) if gaps else None,
    }


def _pair_disagreement(order: list, expected: list) -> float:
    position = {item: index for index, item in enumerate(order)}
    pairs = [(a, b) for index, a in enumerate(expected) for b in expected[index + 1:]]
    if any(item not in position for item in expected):
        return 1.0
    return sum(position[a] > position[b] for a, b in pairs) / len(pairs)


def score_triage(outputs: dict, key: dict) -> dict:
    exact, disagreement = [], []
    for scenario_id, answers in key.items():
        order = outputs.get(scenario_id) or []
        exact.append(order == answers["triage"])
        disagreement.append(_pair_disagreement(order, answers["triage"]))
    return {"n": len(key), "exact": mean(exact), "pair_disagreement": mean(disagreement)}


def score_plans(outputs: dict, scenarios: dict, key: dict, task: str, distances: dict) -> dict:
    gaps, violations, churns = [], 0, []
    optimal = 0
    ids = [scenario_id for scenario_id in key if task in key[scenario_id]]
    for scenario_id in ids:
        scenario = scenarios[scenario_id]
        plan = outputs.get(scenario_id)
        result = evaluate_plan(scenario, plan, distances[(scenario_id, task)])
        best = key[scenario_id][task]["objective"]
        gap = result["cost"] / best - 1
        gaps.append(gap)
        optimal += gap <= TOLERANCE and not result["violations"]
        violations += len(result["violations"]) + (plan is None)
        if task == "replanning":
            churns.append(churn(scenario["disruption"]["prior_plan"], plan))
    scored = {
        "n": len(ids),
        "optimal": optimal / len(ids),
        "mean_gap": mean(gaps),
        "worst_gap": max(gaps),
        "rule_violations": violations,
    }
    if task == "replanning":
        scored["mean_churn"] = mean(churns)
        scored["optimal_churn"] = mean(
            churn(scenarios[sid]["disruption"]["prior_plan"], key[sid][task]["plan"]) for sid in ids
        )
    return scored


def main() -> None:
    town = load_town()
    scenarios = {s["id"]: s for s in load(CASES / "scenarios.json")["scenarios"]}
    scenario_key = load(ANSWERS / "scenarios.json")
    report_key = load(ANSWERS / "reports.json")
    conflict_key = load(ANSWERS / "conflicts.json")

    distances = {}
    for scenario_id, scenario in scenarios.items():
        distances[(scenario_id, "assignment")] = distance_table(
            all_routes(town, scenario["closed_roads"]))
        if scenario.get("disruption"):
            distances[(scenario_id, "replanning")] = distance_table(all_routes(
                town, [*scenario["closed_roads"], scenario["disruption"]["closes"]]))

    scorers = {
        "reports": lambda out: score_reports(out, report_key),
        "conflicts": lambda out: score_conflicts(out, conflict_key),
        "routing": lambda out: score_routing(out, scenarios, scenario_key, town),
        "triage": lambda out: score_triage(out, scenario_key),
        "assignment": lambda out: score_plans(out, scenarios, scenario_key, "assignment", distances),
        "replanning": lambda out: score_plans(out, scenarios, scenario_key, "replanning", distances),
    }

    scores: dict = {}
    for path in sorted(RESULTS.glob("*.json")):
        if path.name == "scores.json":
            continue
        result = load(path)
        for task, variants in result["outputs"].items():
            for variant, output in variants.items():
                row = scorers[task](output)
                row["seconds"] = result.get("seconds", {}).get(task, {}).get(variant)
                scores.setdefault(task, {})[f"{result['arm']} / {variant}"] = row

    save(RESULTS / "scores.json", scores)
    (RESULTS / "summary.md").write_text(render(scores), encoding="utf-8")
    print((RESULTS / "summary.md").read_text(encoding="utf-8"))


def _pct(value) -> str:
    return "—" if value is None else f"{abs(100 * value) if abs(value) < 0.005 else 100 * value:.0f}%"


def _secs(value) -> str:
    return "—" if value is None else f"{value:.4f}"


def render(scores: dict) -> str:
    lines = ["# Findings: scores", "",
             "Generated by `python -m findings.score`. Arms without results are not listed.", ""]

    def table(title, header, rows):
        lines.extend([f"## {title}", "", "| " + " | ".join(header) + " |",
                      "|" + "---|" * len(header)])
        lines.extend("| " + " | ".join(row) + " |" for row in rows)
        lines.append("")

    if "reports" in scores:
        table("Reports (n per row shown)",
              ["Arm / variant", "Overall", "Kind", "Place", "People", "Severity",
               "Severity ±1", "Needs", "Road claims", "Invented values", "n", "Seconds"],
              [[name, _pct(s["overall"]), *(_pct(s["fields"][f]) for f in REPORT_FIELDS),
                _pct(s["severity_within_one"]), str(s["invented_values"]), str(s["n"]),
                _secs(s["seconds"])]
               for name, s in scores["reports"].items()])
    if "conflicts" in scores:
        table("Conflicting road reports", ["Arm / variant", "Accuracy", "n", "Seconds"],
              [[name, _pct(s["accuracy"]), str(s["n"]), _secs(s["seconds"])]
               for name, s in scores["conflicts"].items()])
    if "routing" in scores:
        table("Routing", ["Arm / variant", "Optimal", "Invalid", "Mean extra distance", "n",
                          "Seconds"],
              [[name, _pct(s["optimal"]), _pct(s["invalid"]), _pct(s["mean_extra_distance"]),
                str(s["n"]), _secs(s["seconds"])]
               for name, s in scores["routing"].items()])
    if "triage" in scores:
        table("Triage", ["Arm / variant", "Exact order", "Pairs out of order", "n", "Seconds"],
              [[name, _pct(s["exact"]), _pct(s["pair_disagreement"]), str(s["n"]),
                _secs(s["seconds"])]
               for name, s in scores["triage"].items()])
    for task in ("assignment", "replanning"):
        if task not in scores:
            continue
        header = ["Arm / variant", "Optimal", "Mean cost above optimal", "Worst",
                  "Rule violations", "n", "Seconds"]
        if task == "replanning":
            header.insert(5, "Mean churn (optimal plan's churn)")
        rows = []
        for name, s in scores[task].items():
            row = [name, _pct(s["optimal"]), _pct(s["mean_gap"]), _pct(s["worst_gap"]),
                   str(s["rule_violations"]), str(s["n"]), _secs(s["seconds"])]
            if task == "replanning":
                row.insert(5, f"{s['mean_churn']:.2f} ({s['optimal_churn']:.2f})")
            rows.append(row)
        table(task.capitalize(), header, rows)
    return "\n".join(lines)


if __name__ == "__main__":
    main()
