"""
Farm Irrigation Network Optimizer
=================================
Run:  python farm_irrigation.py [number_of_fields]      (default 30 fields)

Parts
  1. Pipeline network      -> Minimum Spanning Tree (Kruskal + Prim)
  2. Water routing         -> Dijkstra with an uphill penalty + pipe sizing by flow
  3. Infrastructure        -> greedy pump and valve placement
  4. Irrigation schedule   -> greedy daily scheduler (weather + water limit)
  5. Seasonal water budget -> dynamic programming
  6. Performance analysis  -> scalability experiment

The farm is a graph: node 0 is the water source, the other nodes are fields.
Edges are the pipelines that could be laid between them.
"""
import heapq
import math
import random
import sys
import time

# ----------------------------------------------------------------- settings
CROPS = {                       # need = mm of water per day, value = income per hectare
    "wheat":     {"need": 4.5, "value": 50},
    "rice":      {"need": 8.0, "value": 60},
    "maize":     {"need": 6.0, "value": 45},
    "cotton":    {"need": 7.0, "value": 70},
    "sugarcane": {"need": 9.0, "value": 80},
}
HOURS = 8                       # pumping hours per day
LEAK_PER_METER = 5              # litres lost per metre of pipe per day
UPHILL_PENALTY = 10             # climbing 1 m costs as much as 10 m of flat pipe
PIPES = [("50 mm", 10, 4),      # (name, max flow in litres/second, cost per metre)
         ("90 mm", 30, 8),
         ("160 mm", 90, 16),
         ("250 mm", 250, 30)]
FRICTION = 0.01                 # pressure head lost per metre of pipe (m)
MIN_HEAD = 5                    # minimum pressure needed at a field (m)
SOURCE_HEAD = 15                # head given by the pump at the water source (m)
KWH_PER_M3_M = 0.0045           # energy to lift 1 m3 of water by 1 m (60% efficient pump)
FIELD_CAP = 60                  # soil water capacity (mm)
TRIGGER = 30                    # irrigate when soil water would fall below this (mm)
TARGET = 42                     # smart schedule tops the soil up to this level (mm)
WILT = 15                       # below this the crop is stressed (mm)
SEASON_DAYS = 60


# ------------------------------------------------------------ farm model
class Node:
    def __init__(self, i, x, y, elev, crop=None, area=0.0):
        self.i, self.x, self.y, self.elev = i, x, y, elev
        self.crop, self.area = crop, area
        self.need_mm = CROPS[crop]["need"] if crop else 0
        self.demand = self.need_mm * area * 10000            # litres/day (1 mm on 1 ha = 10,000 L)
        self.worth = CROPS[crop]["value"] * area if crop else 0


def terrain(x, y):
    """Simple rolling land: elevation in metres."""
    return 100 + 10 * math.sin(x / 200) + 8 * math.cos(y / 250)


def make_farm(n_fields, seed=1):
    rnd = random.Random(seed)
    size = 180 * math.sqrt(n_fields)                          # keeps field density the same
    nodes = [Node(0, size / 2, size / 2, terrain(size / 2, size / 2))]   # water source
    for i in range(1, n_fields + 1):
        x, y = rnd.uniform(0, size), rnd.uniform(0, size)
        nodes.append(Node(i, x, y, terrain(x, y) + rnd.uniform(-1, 1),
                          rnd.choice(list(CROPS)), rnd.uniform(0.3, 1.2)))
    return nodes, size


def pipe_length(a, b):
    """3-D length of a pipe between two nodes (follows the ground)."""
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.elev - b.elev) ** 2)


def route_cost(a, b):
    """Effort to push water from a to b: length + penalty for going uphill."""
    return pipe_length(a, b) + UPHILL_PENALTY * max(0, b.elev - a.elev)


# ------------------------------------------- 1. minimum spanning tree
def kruskal(nodes):
    """Sort all possible pipes, add the shortest one that does not make a loop."""
    n = len(nodes)
    edges = sorted((pipe_length(nodes[i], nodes[j]), i, j)
                   for i in range(n) for j in range(i + 1, n))
    boss = list(range(n))                                     # union-find

    def find(x):
        while boss[x] != x:
            boss[x] = boss[boss[x]]
            x = boss[x]
        return x

    tree = []
    for w, i, j in edges:
        ri, rj = find(i), find(j)
        if ri != rj:
            boss[ri] = rj
            tree.append((i, j, w))
            if len(tree) == n - 1:
                break
    return tree


def prim(nodes):
    """Grow the tree from the source, always adding the closest unconnected field."""
    n = len(nodes)
    dist, link, used = [math.inf] * n, [0] * n, [False] * n
    dist[0], tree = 0, []
    for _ in range(n):
        u = min((v for v in range(n) if not used[v]), key=lambda v: dist[v])
        used[u] = True
        if u != 0:
            tree.append((link[u], u, dist[u]))
        for v in range(n):
            if not used[v]:
                d = pipe_length(nodes[u], nodes[v])
                if d < dist[v]:
                    dist[v], link[v] = d, u
    return tree


# ------------------------------------------- 2. water routing
def candidate_graph(nodes, mst, k=5):
    """Realistic pipe options: each node links to its k nearest nodes (+ the MST edges)."""
    n = len(nodes)
    adj = [set() for _ in range(n)]
    for i in range(n):
        near = heapq.nsmallest(k + 1, range(n), key=lambda j: pipe_length(nodes[i], nodes[j]))
        for j in near:
            if j != i:
                adj[i].add(j)
                adj[j].add(i)
    for i, j, _ in mst:
        adj[i].add(j)
        adj[j].add(i)
    return adj


def dijkstra(nodes, adj):
    """Cheapest route from the source to every field (uphill is expensive)."""
    n = len(nodes)
    dist, parent = [math.inf] * n, [-1] * n
    dist[0], heap = 0, [(0, 0)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        for v in adj[u]:
            nd = d + route_cost(nodes[u], nodes[v])
            if nd < dist[v]:
                dist[v], parent[v] = nd, u
                heapq.heappush(heap, (nd, v))
    return [(parent[v], v, pipe_length(nodes[parent[v]], nodes[v])) for v in range(1, n)]


def root_tree(n, tree):
    """Turn a list of edges into parent[] and a source-first visiting order."""
    adj = [[] for _ in range(n)]
    for i, j, _ in tree:
        adj[i].append(j)
        adj[j].append(i)
    parent, order, seen = [-1] * n, [0], {0}
    for u in order:                                           # order grows as we loop (BFS)
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                parent[v] = u
                order.append(v)
    return parent, order


def analyse(nodes, tree):
    """Pipe lengths, flows, pipe sizes, cost and leakage of one network design."""
    n = len(nodes)
    parent, order = root_tree(n, tree)
    seg = [0.0] * n                      # length of the pipe that feeds each node
    path = [0.0] * n                     # pipe length from the source to each node
    effort = [0.0] * n                   # routing effort from the source to each node
    down = [nd.demand for nd in nodes]   # litres/day flowing through the pipe feeding each node
    for v in order[1:]:
        u = parent[v]
        seg[v] = pipe_length(nodes[u], nodes[v])
        path[v] = path[u] + seg[v]
        effort[v] = effort[u] + route_cost(nodes[u], nodes[v])
    for v in reversed(order[1:]):        # children are added to their parent
        down[parent[v]] += down[v]

    cost, size, too_small = 0, [0] * n, 0
    for v in order[1:]:                  # smallest pipe that can carry the flow
        flow = down[v] / (HOURS * 3600)  # litres/second
        size[v] = next((k for k, p in enumerate(PIPES) if p[1] >= flow), len(PIPES) - 1)
        too_small += flow > PIPES[-1][1]
        cost += seg[v] * PIPES[size[v]][2]

    length = sum(seg)
    leak = length * LEAK_PER_METER
    return {"length": length, "cost": cost, "leak": leak, "demand": down[0],
            "efficiency": 100 * down[0] / (down[0] + leak),
            "avg_path": sum(path[1:]) / (n - 1), "avg_effort": sum(effort[1:]) / (n - 1),
            "parent": parent, "order": order, "down": down, "path": path,
            "size": size, "too_small": too_small}


# ------------------------------------------- 3. pumps and valves
def place_infrastructure(nodes, info):
    """Walk down the pipe tree; add a booster pump only where pressure would get too low."""
    parent, order, down = info["parent"], info["order"], info["down"]

    # Baseline: ONE big pump at the source, strong enough for the hardest field
    need = max(MIN_HEAD + nodes[v].elev - nodes[0].elev + FRICTION * info["path"][v] for v in order[1:])
    naive_energy = need * info["demand"] / 1000 * KWH_PER_M3_M

    head = [0.0] * len(nodes)
    head[0] = min(SOURCE_HEAD, need)                          # small farms need no more than 'need'
    pumps = {0: head[0]}                                      # node -> head added (m)
    for v in order[1:]:
        u = parent[v]
        drop = FRICTION * pipe_length(nodes[u], nodes[v]) + (nodes[v].elev - nodes[u].elev)
        if head[u] - drop < MIN_HEAD:                         # not enough pressure -> booster at u
            extra = MIN_HEAD + drop - head[u] + 2             # +2 m safety margin
            pumps[u] = pumps.get(u, 0) + extra
            head[u] += extra
        head[v] = head[u] - drop

    energy = sum(h * down[u] / 1000 * KWH_PER_M3_M for u, h in pumps.items())
    if energy > naive_energy:                                 # boosters do not pay off -> one pump
        pumps, energy = {0: need}, naive_energy

    children = [0] * len(nodes)
    for v in order[1:]:
        children[parent[v]] += 1
    branch_valves = sum(1 for c in children if c >= 2)        # valve where the pipe splits
    return {"pumps": pumps, "energy": energy, "naive_energy": naive_energy,
            "naive_head": need, "valves": (len(nodes) - 1) + branch_valves}


# ------------------------------------------- 4. irrigation scheduling
def make_weather(days, seed=2):
    """Rain in mm for each day (about 1 rainy day in 5)."""
    rnd = random.Random(seed)
    return [rnd.uniform(5, 25) if rnd.random() < 0.2 else 0 for _ in range(days)]


def simulate(fields, rain, daily_limit, smart):
    """Simulate a season. smart=True uses soil moisture + rain forecast, else a fixed rota."""
    soil = {f.i: 40.0 for f in fields}
    stress = {f.i: 0 for f in fields}
    used = waste = 0.0
    for day, r in enumerate(rain):
        budget = daily_limit                                  # litres we may use today
        applied = {f.i: 0.0 for f in fields}
        if smart:
            todo = sorted(fields, key=lambda f: soil[f.i])    # driest field first
        else:
            todo = [f for f in fields if (day + f.i) % 2 == 0]  # every field every 2nd day
        for f in todo:
            if smart:
                expected = soil[f.i] - f.need_mm + r          # forecast: soil water after today
                if expected >= TRIGGER:
                    continue                                  # enough water, skip
                mm = TARGET - expected                        # top up, do not flood
            else:
                mm = 2 * f.need_mm
            mm = min(mm, budget / (f.area * 10000))           # cannot use more than what is left
            budget -= mm * f.area * 10000
            used += mm * f.area * 10000
            applied[f.i] = mm
        for f in fields:                                      # end of day: update the soil
            new = soil[f.i] + applied[f.i] + r - f.need_mm
            waste += max(0, new - FIELD_CAP) * f.area * 10000     # overflow = wasted water
            soil[f.i] = max(0, min(FIELD_CAP, new))
            stress[f.i] += soil[f.i] < WILT
    # each stress day costs 3% of the yield; weight fields by income
    total = sum(f.worth for f in fields)
    yield_pct = 100 * sum(f.worth * max(0, 1 - 0.03 * stress[f.i]) for f in fields) / total
    return {"used": used, "waste": waste, "stress": sum(stress.values()), "yield": yield_pct}


# ------------------------------------------- 5. dynamic programming
def allocate_dp(fields, units, budget):
    """Share a limited season water budget between fields to get the most income.
    Field yield grows with diminishing returns: 1 - (1 - u/N)^2.
    best[i][w] = best income using the first i fields and w units of water."""
    n = len(fields)
    best = [[0.0] * (budget + 1) for _ in range(n + 1)]
    pick = [[0] * (budget + 1) for _ in range(n + 1)]
    for i, f in enumerate(fields, 1):
        for w in range(budget + 1):
            for u in range(min(units[i - 1], w) + 1):
                v = best[i - 1][w - u] + income(f, u, units[i - 1])
                if v > best[i][w]:
                    best[i][w], pick[i][w] = v, u
    alloc, w = [0] * n, budget                                # trace back the choices
    for i in range(n, 0, -1):
        alloc[i - 1] = pick[i][w]
        w -= pick[i][w]
    return best[n][budget], alloc


def income(f, u, need_units):
    return f.worth * (1 - (1 - min(u, need_units) / need_units) ** 2)


# ------------------------------------------- map
def plot_farm(nodes, size, info, pumps, filename):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib not installed - map skipped)")
        return
    fig, ax = plt.subplots(figsize=(9, 8))
    grid = [[terrain(size * c / 60, size * r / 60) for c in range(61)] for r in range(61)]
    ax.imshow(grid, extent=(0, size, 0, size), origin="lower", cmap="YlGn", alpha=0.45)
    for v in info["order"][1:]:                               # pipes: thicker = bigger pipe
        u = info["parent"][v]
        ax.plot([nodes[u].x, nodes[v].x], [nodes[u].y, nodes[v].y], color="#1f77b4",
                lw=1 + 1.5 * info["size"][v], solid_capstyle="round", zorder=2)
    colours = dict(zip(CROPS, ["#e6b800", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b"]))
    for c, col in colours.items():
        pts = [f for f in nodes[1:] if f.crop == c]
        ax.scatter([f.x for f in pts], [f.y for f in pts], s=[60 + 150 * f.area for f in pts],
                   color=col, edgecolor="black", label=c, zorder=3)
    ax.scatter(nodes[0].x, nodes[0].y, marker="*", s=450, color="#d62728", edgecolor="black",
               label="water source", zorder=4)
    boost = [u for u in pumps if u != 0]
    ax.scatter([nodes[u].x for u in boost], [nodes[u].y for u in boost], marker="^", s=130,
               color="black", label="booster pump", zorder=5)
    ax.set_title("Optimised irrigation network (minimum spanning tree)\n"
                 "line thickness = pipe size, background = elevation")
    ax.set_xlabel("metres")
    ax.set_ylabel("metres")
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8)
    fig.tight_layout()
    fig.savefig(filename, dpi=130)
    print(f"  Map saved to {filename}")


# ------------------------------------------- helpers + main program
def timed(fn, *args):
    t = time.perf_counter()
    result = fn(*args)
    return result, (time.perf_counter() - t) * 1000          # milliseconds


def title(text):
    print("\n" + "=" * 66 + f"\n{text}\n" + "=" * 66)


def main():
    n_fields = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    nodes, size = make_farm(n_fields)
    fields = nodes[1:]
    print(f"Farm: {n_fields} fields, {size:.0f} m x {size:.0f} m, "
          f"total demand {sum(f.demand for f in fields) / 1000:,.0f} m3/day")

    # ---- Part 1
    title("1. PIPELINE NETWORK  (Minimum Spanning Tree)")
    kr, t_kr = timed(kruskal, nodes)
    pr, t_pr = timed(prim, nodes)
    direct = [(0, i, pipe_length(nodes[0], nodes[i])) for i in range(1, len(nodes))]
    mst, base = analyse(nodes, kr), analyse(nodes, direct)
    assert abs(sum(w for *_, w in kr) - sum(w for *_, w in pr)) < 1e-6, "Kruskal and Prim differ!"
    print(f"Kruskal and Prim give the same tree length ({mst['length']:,.0f} m).")
    print(f"{'':28}{'Direct pipes':>14}{'MST':>12}{'Saving':>10}")
    for label, key, unit in [("Pipe length (m)", "length", ""), ("Pipe cost", "cost", ""),
                             ("Water lost (L/day)", "leak", "")]:
        a, b = base[key], mst[key]
        print(f"{label:28}{a:>14,.0f}{b:>12,.0f}{100 * (a - b) / a:>9.1f}%")
    print(f"{'Water efficiency':28}{base['efficiency']:>13.2f}%{mst['efficiency']:>11.2f}%")
    print("(Direct pipes = a separate pipe from the source to every field.)")

    # ---- Part 2
    title("2. WATER ROUTING  (Dijkstra with elevation)")
    spt = analyse(nodes, dijkstra(nodes, candidate_graph(nodes, kr)))
    print(f"{'':32}{'MST':>12}{'Shortest paths':>16}")
    print(f"{'Total pipe length (m)':32}{mst['length']:>12,.0f}{spt['length']:>16,.0f}")
    print(f"{'Avg source->field path (m)':32}{mst['avg_path']:>12,.0f}{spt['avg_path']:>16,.0f}")
    print(f"{'Avg routing effort':32}{mst['avg_effort']:>12,.0f}{spt['avg_effort']:>16,.0f}")
    print(f"{'Pipe cost':32}{mst['cost']:>12,.0f}{spt['cost']:>16,.0f}")
    print("MST = least pipe (cheapest, least leakage). Shortest paths = shorter, flatter")
    print("routes to each field but more pipe. We build the MST and use Dijkstra to")
    print("check the routes.")
    sizes = mst["size"][1:]                                   # one pipe per field (skip the source)
    print("Pipe sizes chosen by flow:",
          ", ".join(f"{sizes.count(k)} x {p[0]}" for k, p in enumerate(PIPES)))
    print(f"Source flow: {mst['demand'] / (HOURS * 3600):.1f} L/s;"
          f" pipes over capacity: {mst['too_small']}")

    # ---- Part 3
    title("3. INFRASTRUCTURE  (pumps and valves)")
    infra = place_infrastructure(nodes, mst)
    boosters = [u for u in infra["pumps"] if u != 0]
    print(f"Source pump head: {infra['pumps'][0]:.0f} m, booster pumps needed: {len(boosters)}"
          f" (at fields {boosters})")
    print(f"Valves: {infra['valves']} (one per field + one at each pipe split)")
    print(f"Energy, one big pump ({infra['naive_head']:.0f} m head): {infra['naive_energy']:.1f} kWh/day")
    print(f"Energy, smart pump placement:        {infra['energy']:.1f} kWh/day "
          f"({100 * (1 - infra['energy'] / infra['naive_energy']):.1f}% less)")

    # ---- Part 4
    title(f"4. IRRIGATION SCHEDULE  ({SEASON_DAYS}-day season)")
    rain = make_weather(SEASON_DAYS)
    limit = 0.8 * sum(f.demand for f in fields)               # water source gives 80% of demand
    fixed, smart = simulate(fields, rain, limit, False), simulate(fields, rain, limit, True)
    print(f"Rainy days: {sum(r > 0 for r in rain)}, total rain {sum(rain):.0f} mm, "
          f"daily water limit {limit / 1000:,.0f} m3")
    print(f"{'':28}{'Fixed rota':>14}{'Smart':>12}")
    print(f"{'Water pumped (m3)':28}{fixed['used'] / 1000:>14,.0f}{smart['used'] / 1000:>12,.0f}")
    print(f"{'Water wasted (m3)':28}{fixed['waste'] / 1000:>14,.0f}{smart['waste'] / 1000:>12,.0f}")
    print(f"{'Crop stress days':28}{fixed['stress']:>14}{smart['stress']:>12}")
    print(f"{'Expected yield':28}{fixed['yield']:>13.1f}%{smart['yield']:>11.1f}%")
    print(f"Smart schedule uses {100 * (1 - smart['used'] / fixed['used']):.0f}% less water "
          f"and gives {smart['yield'] - fixed['yield']:+.1f} points of yield.")

    # ---- Part 5
    title("5. SEASONAL WATER BUDGET  (Dynamic Programming)")
    units = [max(1, round(f.demand * SEASON_DAYS / 100000)) for f in fields]   # 1 unit = 100 m3
    budget = round(0.6 * sum(units))
    dp_income, alloc = allocate_dp(fields, units, budget)
    share = [int(budget * u / sum(units)) for u in units]     # baseline: split by need
    share_income = sum(income(f, s, u) for f, s, u in zip(fields, share, units))
    print(f"Water needed for the season: {sum(units)} units, available: {budget} units (60%).")
    print(f"Income, split by need:   {share_income:,.1f}")
    print(f"Income, DP allocation:   {dp_income:,.1f}  ({100 * (dp_income / share_income - 1):+.1f}%)")
    print("DP gives more water to valuable crops that respond well to it.")

    # ---- Part 6
    title("6. PERFORMANCE ANALYSIS  (scalability)")
    print(f"{'Fields':>7}{'Edges':>9}{'Kruskal ms':>12}{'Prim ms':>10}{'Dijkstra ms':>13}"
          f"{'Schedule ms':>13}{'MST km':>8}{'Direct km':>11}{'Saved':>8}")
    for n in [10, 25, 50, 100, 200, 400]:
        nd, _ = make_farm(n)
        k_tree, t1 = timed(kruskal, nd)
        _, t2 = timed(prim, nd)
        adj = candidate_graph(nd, k_tree)
        _, t3 = timed(dijkstra, nd, adj)
        lim = 0.8 * sum(f.demand for f in nd[1:])
        _, t4 = timed(simulate, nd[1:], make_weather(SEASON_DAYS), lim, True)
        a = analyse(nd, k_tree)["length"]
        b = analyse(nd, [(0, i, pipe_length(nd[0], nd[i])) for i in range(1, len(nd))])["length"]
        print(f"{n:>7}{len(nd) * (len(nd) - 1) // 2:>9,}{t1:>12.1f}{t2:>10.1f}{t3:>13.1f}"
              f"{t4:>13.1f}{a / 1000:>8.1f}{b / 1000:>11.1f}{100 * (b - a) / b:>7.0f}%")
    print("Kruskal grows as E log E, Prim (array version) as V^2, Dijkstra as E log V,")
    print("the scheduler as days x fields.")

    print()
    plot_farm(nodes, size, mst, infra["pumps"], "farm_network.png")


if __name__ == "__main__":
    main()
