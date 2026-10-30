"""Text views of a scenario, shown identically to the human quiz and the AI agent."""

from findings.common import SEVERITY_WEIGHT, UNFILLED_MINUTES, open_adjacency, slots
from findings.oracle import all_routes, distance_table


def road_list(town: dict, closed) -> str:
    """Each place with its open roads, numbered so a person can type routes quickly."""
    places = [place["id"] for place in town["places"]]
    number = {place: index + 1 for index, place in enumerate(places)}
    adjacency = open_adjacency(town, closed)
    lines = []
    for place in places:
        links = ", ".join(f"{number[n]} {n} ({length:.2f})"
                          for n, length, _ in sorted(adjacency[place]))
        lines.append(f"  {number[place]:>2} {place:<18} -> {links or '(no open roads)'}")
    return "\n".join(lines)


def cost_table(town: dict, scenario: dict, closed) -> str:
    """Slot x unit cost (severity weight x ETA over true shortest routes); '-' = not allowed."""
    distances = distance_table(all_routes(town, closed))
    units = scenario["units"]
    header = "  slot      need       sev   unfilled | " + " ".join(
        f"{index + 1}:{unit['id']:<9}" for index, unit in enumerate(units))
    lines = [header, "  " + "-" * (len(header) - 2)]
    for slot in slots(scenario):
        weight = SEVERITY_WEIGHT[slot["severity"]]
        cells = []
        for unit in units:
            distance = distances[(unit["location"], slot["place"])]
            if slot["need"] not in unit["capabilities"] or distance is None:
                cells.append(f"{'-':<11}")
            else:
                cells.append(f"{weight * distance / unit['speed']:<11.1f}")
        lines.append(f"  {slot['slot']:<9} {slot['need']:<10} {slot['severity'][:4]:<5} "
                     f"{weight * UNFILLED_MINUTES:>8} | " + " ".join(cells))
    return "\n".join(lines)
