"""Triage Agent eval: the Muse Spark triage call on the 20 hand-written test reports.

Input is each report's raw text only. The 0-100 severity score is mapped to the rubric's
severity class with bands fixed before running (they match the Triage rule scores:
critical 80, high 62, medium 42, low 22):

    critical >= 80, high 60-79, medium 40-59, low < 40

Answer keys are `findings/cases/answers/reports.json`, written by the project author with
the rubric in `findings/cases/rubric.md`; the reports are fictional. Responses are
recorded under `evaluation/triage_cache/` so the table can be reproduced offline:

    python -m evaluation.triage_eval            # live (records)
    META_MODE=demo python -m evaluation.triage_eval   # replay the recordings
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from crisis_command.meta_api import hub_from_env

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "findings" / "cases"
OUT = ROOT / "evaluation" / "triage_results.json"
CLASSES = ("low", "medium", "high", "critical")


def band(score: int) -> str:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def main() -> None:
    reports = json.loads((CASES / "reports.json").read_text(encoding="utf-8"))["reports"]
    keys = json.loads((CASES / "answers" / "reports.json").read_text(encoding="utf-8"))
    hub = hub_from_env()
    hub.cache_dir = ROOT / "evaluation" / "triage_cache"

    def run(report):
        result = hub.triage({"text": report["text"]}, lambda: None)
        return report["id"], result

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = dict(pool.map(run, reports))

    rows, sources = [], Counter()
    for report in reports:
        result = results[report["id"]]
        sources[result.source] += 1
        expected = keys[report["id"]]["severity"]
        if result.value is None:
            rows.append({"id": report["id"], "expected": expected, "score": None, "predicted": None,
                         "source": result.source})
            continue
        score = int(result.value["severity_score"])
        rows.append({"id": report["id"], "expected": expected, "score": score, "predicted": band(score),
                     "vulnerable": result.value.get("vulnerable"), "source": result.source})

    scored = [r for r in rows if r["predicted"] is not None]
    exact = sum(r["predicted"] == r["expected"] for r in scored)
    within = sum(abs(CLASSES.index(r["predicted"]) - CLASSES.index(r["expected"])) <= 1 for r in scored)
    critical = [r for r in scored if r["expected"] == "critical"]
    summary = {
        "model": hub.client.model_id,
        "sources": dict(sources),
        "reports": len(rows),
        "scored": len(scored),
        "exact_class": exact,
        "within_one_class": within,
        "critical_recall": sum(r["predicted"] == "critical" for r in critical),
        "critical_total": len(critical),
        "confusion": {f"{e}->{p}": n for (e, p), n in
                      sorted(Counter((r["expected"], r["predicted"]) for r in scored).items())},
        "rows": rows,
    }
    OUT.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"{summary['model']} ({dict(sources)}): exact {exact}/{len(scored)}, within one class "
          f"{within}/{len(scored)}, critical recall {summary['critical_recall']}/{len(critical)}")
    for row in rows:
        mark = "" if row["predicted"] == row["expected"] else "  <-"
        print(f"  {row['id']}: key {row['expected']:8s} score {row['score']!s:>4} -> {row['predicted']}{mark}")


if __name__ == "__main__":
    main()
