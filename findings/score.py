"""Score every arm in results/ against the answer keys.

    python -m findings.score

Reads results/*.json (each {"arm", "outputs": {task: {variant: ...}}, "seconds"?}) and writes
results/scores.json and results/summary.md. A missing answer counts as wrong. Assignment and
replanning costs use the oracle's brute-force distances, never an arm's own numbers.
"""

import json
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

    keys = {
        "reports": report_key,
        "conflicts": conflict_key,
        "routing": scenario_key,
        "triage": scenario_key,
        "assignment": scenario_key,
        "replanning": scenario_key,
    }
    scorers = {
        "reports": lambda out, key: score_reports(out, key),
        "conflicts": lambda out, key: score_conflicts(out, key),
        "routing": lambda out, key: score_routing(out, scenarios, key, town),
        "triage": lambda out, key: score_triage(out, key),
        "assignment": lambda out, key: score_plans(out, scenarios, key, "assignment", distances),
        "replanning": lambda out, key: score_plans(out, scenarios, key, "replanning", distances),
    }

    results = [load(path) for path in sorted(RESULTS.glob("*.json")) if path.name != "scores.json"]
    # A partial arm (the human quiz) declares the cases it covered; every arm is then also
    # scored on exactly those cases so the comparison is like for like.
    subsets = {task: ids for result in results
               for task, ids in result.get("subset", {}).items() if task in scorers}

    scores: dict = {}
    for result in results:
        for task, variants in result["outputs"].items():
            if task not in scorers:
                continue  # bookkeeping entries such as the quiz's per-query routing log
            for variant, output in variants.items():
                name = f"{result['arm']} / {variant}"
                seconds = result.get("seconds", {}).get(task, {}).get(variant)
                if "subset" not in result:
                    row = scorers[task](output, keys[task])
                    row["seconds"] = seconds
                    scores.setdefault(task, {})[name] = row
                if task in subsets:
                    subset_key = {i: keys[task][i] for i in subsets[task]}
                    row = scorers[task](output, subset_key)
                    row["seconds"] = seconds if "subset" in result else None
                    scores.setdefault(f"{task} (human subset)", {})[name] = row

    consistency = {result["arm"]: run_consistency(result) for result in results
                   if result.get("runs", 1) > 1}
    if consistency:
        scores["consistency"] = consistency

    save(RESULTS / "scores.json", scores)
    (RESULTS / "summary.md").write_text(render(scores), encoding="utf-8")
    print((RESULTS / "summary.md").read_text(encoding="utf-8"))


def _canonical(value) -> str:
    """Comparable form of an answer: set-like fields (needs, road claims) are order-free."""
    if isinstance(value, dict) and "road_claims" in value:
        value = {**value, "needs": sorted(value.get("needs") or []),
                 "road_claims": sorted(_claims(value.get("road_claims")), key=str)}
    return json.dumps(value, sort_keys=True, default=list)


def run_consistency(result: dict) -> dict:
    """Share of items given the identical answer in every run (a missing answer never matches)."""
    table = {}
    for task, variants in result["outputs"].items():
        runs = list(variants.values())
        items = set().union(*runs)
        same = sum(
            all(item in run for run in runs)
            and len({_canonical(run[item]) for run in runs}) == 1
            for item in items
        )
        table[task] = {"identical": same / len(items) if items else None, "n": len(items),
                       "runs": len(runs)}
    return table


def _pct(value) -> str:
    return "—" if value is None else f"{abs(100 * value) if abs(value) < 0.005 else 100 * value:.0f}%"


def _secs(value) -> str:
    return "—" if value is None else f"{value:.4f}"


TITLES = {"reports": "Reports", "conflicts": "Conflicting road reports", "routing": "Routing",
          "triage": "Triage", "assignment": "Assignment", "replanning": "Replanning"}


def render(scores: dict) -> str:
    lines = ["# Findings: scores", "",
             "Generated by `python -m findings.score`. Arms without results are not listed.",
             "\"Human subset\" tables score every arm on only the cases the human quiz covers;",
             "seconds there are shown for the human only, since other arms ran the full set.", ""]

    def table(title, header, rows):
        lines.extend([f"## {title}", "", "| " + " | ".join(header) + " |",
                      "|" + "---|" * len(header)])
        lines.extend("| " + " | ".join(row) + " |" for row in rows)
        lines.append("")

    for section, rows in scores.items():
        if section == "consistency":
            table("Consistency across repeated runs (identical answer every run)",
                  ["Arm", *TITLES.values()],
                  [[arm, *(_pct(tasks[t]["identical"]) if t in tasks else "—" for t in TITLES)]
                   for arm, tasks in rows.items()])
            continue
        task, _, suffix = section.partition(" (")
        title = TITLES[task] + (f" ({suffix}" if suffix else "")
        if task == "reports":
            table(title,
                  ["Arm / variant", "Overall", "Kind", "Place", "People", "Severity",
                   "Severity ±1", "Needs", "Road claims", "Invented values", "n", "Seconds"],
                  [[name, _pct(s["overall"]), *(_pct(s["fields"][f]) for f in REPORT_FIELDS),
                    _pct(s["severity_within_one"]), str(s["invented_values"]), str(s["n"]),
                    _secs(s["seconds"])]
                   for name, s in rows.items()])
        elif task == "conflicts":
            table(title, ["Arm / variant", "Accuracy", "n", "Seconds"],
                  [[name, _pct(s["accuracy"]), str(s["n"]), _secs(s["seconds"])]
                   for name, s in rows.items()])
        elif task == "routing":
            table(title, ["Arm / variant", "Optimal", "Invalid", "Mean extra distance", "n",
                          "Seconds"],
                  [[name, _pct(s["optimal"]), _pct(s["invalid"]),
                    _pct(s["mean_extra_distance"]), str(s["n"]), _secs(s["seconds"])]
                   for name, s in rows.items()])
        elif task == "triage":
            table(title, ["Arm / variant", "Exact order", "Pairs out of order", "n", "Seconds"],
                  [[name, _pct(s["exact"]), _pct(s["pair_disagreement"]), str(s["n"]),
                    _secs(s["seconds"])]
                   for name, s in rows.items()])
        else:
            header = ["Arm / variant", "Optimal", "Mean cost above optimal", "Worst",
                      "Rule violations", "n", "Seconds"]
            if task == "replanning":
                header.insert(5, "Mean churn (optimal plan's churn)")
            body = []
            for name, s in rows.items():
                row = [name, _pct(s["optimal"]), _pct(s["mean_gap"]), _pct(s["worst_gap"]),
                       str(s["rule_violations"]), str(s["n"]), _secs(s["seconds"])]
                if task == "replanning":
                    row.insert(5, f"{s['mean_churn']:.2f} ({s['optimal_churn']:.2f})")
                body.append(row)
            table(title, header, body)
    return "\n".join(lines)


if __name__ == "__main__":
    main()
