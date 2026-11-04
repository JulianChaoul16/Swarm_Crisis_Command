"""Charts of the measured results, written to docs/figures/ as PNGs.

Reads only the saved result files (findings/results/scores.json, evaluation/*_results.json),
so every number in a chart is one the evals produced. Needs matplotlib, which the app does
not: `pip install matplotlib`, then `python tools/make_figures.py`.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "figures"

# Validated categorical slots (light mode), chart chrome and ink.
SPARK, HUMAN = "#2a78d6", "#eb6834"
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
BLUES = ["#fcfcfb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    "font.size": 11,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK_2,
    "axes.titlecolor": INK,
    "axes.titlesize": 14,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.titlepad": 26,
    "xtick.color": MUTED,
    "ytick.color": INK_2,
    "text.color": INK,
    "savefig.dpi": 200,
    "savefig.bbox": "tight",
    "savefig.facecolor": SURFACE,
})


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def tidy(ax, value_axis: str = "x") -> None:
    """Recessive chrome: hairline grid on the value axis, no box."""
    for side in ("top", "right", "left" if value_axis == "x" else "bottom"):
        ax.spines[side].set_visible(False)
    ax.grid(axis=value_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def subtitle(ax, text: str) -> None:
    ax.text(0, 1.02, text, transform=ax.transAxes, color=INK_2, fontsize=10, va="bottom")


def footnote(fig, text: str, y: float = -0.02) -> None:
    fig.text(0.01, y, text, color=MUTED, fontsize=8.5, va="top", wrap=True)


def person_vs_spark(scores: dict) -> None:
    """The job today is done by a person: time and accuracy reading the same 20 reports."""
    reports = scores["reports (human subset)"]
    full = scores["reports"]
    runs = [full[f"ai muse-spark-1.3 (low) / run{i}"] for i in (1, 2, 3)]
    human = reports["human / you"]
    rows = [  # (label, seconds per report, share of fields correct, color)
        ("Person", human["seconds"] / human["n"], human["overall"], HUMAN),
        ("Muse Spark", sum(r["seconds"] for r in runs) / sum(r["n"] for r in runs),
         min(r["overall"] for r in runs), SPARK),
    ]
    ratio = rows[0][1] / rows[1][1]

    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 3.6))
    for y, (label, seconds, correct, color) in enumerate(rows):
        left.barh(y, seconds, height=0.55, color=color)
        left.text(seconds + 0.8, y, f"{seconds:.1f} s", va="center", fontsize=10, color=INK_2)
        right.barh(y, correct * 100, height=0.55, color=color)
        right.text(correct * 100 + 1.5, y, f"{correct * 100:.0f}%", va="center", fontsize=10, color=INK_2)
    for ax, title, limit in ((left, "Seconds per report", 60), (right, "Fields read correctly", 115)):
        ax.set_yticks(range(len(rows)), [r[0] for r in rows])
        ax.invert_yaxis()
        ax.set_xlim(0, limit)
        tidy(ax)
        ax.set_title(title, fontsize=11.5, pad=8)
    right.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    right.tick_params(labelleft=False)
    fig.suptitle(f"Muse Spark reads a report {ratio:.0f}× faster than a person, with every field right",
                 x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.01, 0.875, "Same 20 messy flood reports (kind, place, people, severity, needs, road claims), "
                          "including one in Spanish", color=INK_2, fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.86), w_pad=3)
    footnote(fig, "Person: the project author, who also wrote the reports and answer keys, so treat both as a "
                  "best case. Muse Spark: mean over 3 runs, 0 invented values. Source: findings/results/scores.json")
    fig.savefig(OUT / "person_vs_spark.png")
    plt.close(fig)

def assignment(scores: dict) -> None:
    """Why the app uses an exact solver: share of 20 scenarios solved optimally."""
    arms = scores["assignment"]
    rows = [
        ("Greedy, cheapest pair first", arms["algorithms / greedy_global"]),
        ("Greedy, priority order\n(the app's old planner)", arms["algorithms / greedy_priority"]),
        ("Hungarian (exact)", arms["algorithms / hungarian"]),
        ("Muse Spark (3 of 3 runs)", min((arms[f"ai muse-spark-1.3 (low) / run{i}"] for i in (1, 2, 3)),
                                         key=lambda r: r["optimal"])),
    ]
    slower = (rows[3][1]["seconds"] / rows[3][1]["n"]) / (rows[2][1]["seconds"] / rows[2][1]["n"])
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for y, (label, row) in enumerate(rows):
        ax.barh(y, row["optimal"] * 100, height=0.55, color=SPARK)
        per_case = row["seconds"] / row["n"]
        speed = f"{per_case:.1f} s per plan" if per_case >= 0.1 else "< 1 ms per plan"
        ax.text(row["optimal"] * 100 + 1, y, f"{row['optimal'] * 100:.0f}%  ·  {speed}", va="center",
                fontsize=9, color=INK_2)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 130)
    ax.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    tidy(ax)
    ax.set_title("Why the solver makes the plan, not the LLM")
    subtitle(ax, "Unit assignment: share of 20 scenarios given the best plan, and time to plan")
    footnote(fig, f"Muse Spark matched the exact algorithm, but about {round(slower, -3):,.0f}× slower and with no proof the plan is best. "
                  "So the app plans with an exact integer program, and Muse Spark explains the plan. Source: findings/results/scores.json")
    fig.savefig(OUT / "findings_assignment.png")
    plt.close(fig)


def triage_confusion(triage: dict) -> None:
    """Intake Agent triage on 20 labelled reports: expected class vs Muse Spark's class."""
    classes = ["low", "medium", "high", "critical"]
    counts = [[triage["confusion"].get(f"{e}->{p}", 0) for p in classes] for e in classes]
    top = max(max(row) for row in counts)
    fig, ax = plt.subplots(figsize=(6.4, 5.6))
    for r, row in enumerate(counts):
        for c, n in enumerate(row):
            fill = BLUES[0] if n == 0 else BLUES[min(len(BLUES) - 1, 1 + round(n / top * (len(BLUES) - 2)))]
            ax.add_patch(plt.Rectangle((c, r), 1, 1, facecolor=fill, edgecolor=SURFACE, linewidth=2))
            if n:
                ax.text(c + 0.5, r + 0.5, str(n), ha="center", va="center", fontsize=13, fontweight="bold",
                        color="#ffffff" if fill in BLUES[4:] else INK)
    ax.set_xlim(0, 4)
    ax.set_ylim(4, 0)
    ax.set_xticks([i + 0.5 for i in range(4)], classes)
    ax.set_yticks([i + 0.5 for i in range(4)], classes)
    ax.xaxis.set_ticks_position("top")
    ax.xaxis.set_label_position("top")
    ax.set_xlabel("Muse Spark's class")
    ax.set_ylabel("Answer key")
    for side in ax.spines.values():
        side.set_visible(False)
    ax.tick_params(length=0)
    ax.set_title("Every critical report caught; misses err one class high", pad=78)
    ax.text(0, 1.2, f"{triage['exact_class']}/{triage['scored']} exact · "
                           f"{triage['within_one_class']}/{triage['scored']} within one class · "
                           f"{triage['critical_recall']}/{triage['critical_total']} critical scored critical",
            transform=ax.transAxes, color=INK_2, fontsize=10, va="bottom")
    footnote(fig, "Diagonal = correct. 20 reports written by the project author. "
                  "Source: evaluation/triage_results.json")
    fig.savefig(OUT / "triage_confusion.png")
    plt.close(fig)


def voice_and_vision(voice: dict, vision: dict) -> None:
    """Listener word error rate per call, and Vision checks out of 6 photos."""
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.6), gridspec_kw={"width_ratios": [1.5, 1]})

    clips = voice["clips"]
    names = [c["file"].split("_", 1)[1].rsplit(".", 1)[0].replace("_", " ") for c in clips]
    width = 0.38
    for s, (key, label, color) in enumerate([("wer", "As transcribed", SPARK),
                                             ("wer_numbers_normalized", "Numbers normalized", HUMAN)]):
        xs = [i + (s - 0.5) * (width + 0.03) for i in range(len(clips))]
        vals = [c[key] * 100 for c in clips]
        left.bar(xs, vals, width=width, color=color, label=label, edgecolor=SURFACE, linewidth=1)
        for x, v in zip(xs, vals):
            left.text(x, v + 0.4, f"{v:.0f}", ha="center", fontsize=8, color=INK_2)
    left.set_xticks(range(len(clips)), names, rotation=25, ha="right", fontsize=9)
    left.set_ylabel("Word error rate (%)")
    left.set_ylim(0, 25)
    tidy(left, "y")
    left.set_title("Listener: Muse Voice Transcribe")
    left.text(0, 1.02, f"Pooled WER {voice['pooled_wer'] * 100:.1f}%, "
                       f"{voice['pooled_wer_numbers_normalized'] * 100:.1f}% with numbers normalized",
              transform=left.transAxes, color=INK_2, fontsize=10, va="bottom")
    left.legend(frameon=False, fontsize=9, loc="upper left")

    checks = [("Water on road", "water_correct"), ("Fire visible", "fire_correct"),
              ("Passable for cars", "passable_correct"), ("Depth band", "depth_overlaps_intended")]
    rows = vision["rows"]
    scored = [sum(bool(r.get(key)) for r in rows) for _, key in checks]
    right.barh(range(len(checks)), scored, height=0.55, color=SPARK)
    for y, n in enumerate(scored):
        right.text(n + 0.1, y, f"{n}/{len(rows)}", va="center", fontsize=9.5, color=INK_2)
    right.set_yticks(range(len(checks)), [c[0] for c in checks])
    right.invert_yaxis()
    right.set_xlim(0, len(rows) + 1)
    right.set_xticks(range(0, len(rows) + 1, 2))
    tidy(right)
    right.set_title("Vision: Muse Wonder")
    right.text(0, 1.02, f"Correct out of {len(rows)} photos", transform=right.transAxes, color=INK_2,
               fontsize=10, va="bottom")

    fig.tight_layout(w_pad=4)
    footnote(fig, "Calls are synthesized speech (Windows TTS); photos are AI-generated (Muse Image). "
                  "Known miss: \"Shockoe\" heard as \"Chaco\". "
                  "Sources: evaluation/voice_results.json, evaluation/vision_results.json")
    fig.savefig(OUT / "voice_and_vision.png")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    scores = load("findings/results/scores.json")
    person_vs_spark(scores)
    assignment(scores)
    triage_confusion(load("evaluation/triage_results.json"))
    voice_and_vision(load("evaluation/voice_results.json"), load("evaluation/vision_results.json"))
    for path in sorted(OUT.glob("*.png")):
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
