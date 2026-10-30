# Farm Irrigation Network Optimizer

Algorithms to design and run an efficient farm irrigation network: shortest pipeline network, terrain-aware routing, pump placement, weather-aware scheduling and water budgeting. Written in plain Python with no dependencies (matplotlib is optional, only for the map).

## Run it

```
python farm_irrigation.py          # 30 fields (default)
python farm_irrigation.py 60       # any number of fields
```

It prints a report for all six parts and saves `farm_network.png`.

## How the farm is modelled

The farm is a graph. **Node 0 is the water source**, every other node is a field with a crop, an area (ha), an elevation and a daily water demand. **Edges are possible pipelines**, with length measured along the ground (3-D distance). The farm is generated with a fixed random seed so results are repeatable.

## Algorithms

| Part | Goal | Technique | Complexity |
|---|---|---|---|
| 1. Network | Connect all nodes with the least pipe | Minimum spanning tree: **Kruskal** (union-find) and **Prim** (checked against each other) | E log E / V² |
| 2. Routing | Best route source → field, uphill costs extra; size each pipe by its flow | **Dijkstra** on a nearest-neighbour graph, then flow summed up the tree to pick pipe diameters | E log V |
| 3. Infrastructure | Fewer pumps, less energy, no low-pressure fields | **Greedy**: walk down the tree, add a booster only where pressure would drop below the minimum; valves at every field and split | V |
| 4. Scheduling | When and how much to irrigate | **Greedy** daily scheduler: soil moisture + rain forecast + daily water limit, driest field first | days × fields |
| 5. Water budget | Share a limited season supply for the best income | **Dynamic programming** (resource allocation, diminishing returns) | fields × budget × need |
| 6. Analysis | Scalability | Timing and savings for 10 to 400 fields | |

## Results (30 fields, default run)

| Metric | Baseline | Optimised | Change |
|---|---|---|---|
| Pipe length | 10,575 m (separate pipe to each field) | 3,860 m (MST) | **-63.5%** |
| Pipe cost | 42,299 | 20,798 | **-50.8%** |
| Water lost to leaks | 52,874 L/day | 19,300 L/day | **-63.5%** |
| Pump energy | 186.4 kWh/day (one big pump) | 144.0 kWh/day (booster pumps) | **-22.7%** |
| Water pumped in a 60-day season | 79,939 m³ (fixed rota) | 70,497 m³ (smart schedule) | **-12%** |
| Water wasted (overflow) | 19,244 m³ | 31 m³ | **-99.8%** |
| Expected yield | 80.3% | 85.0% | **+4.6 points** |
| Season income with 60% water (DP) | 1,249 (split by need) | 1,277 (DP) | **+2.2%** |

**Scalability.** Everything is fast: at 400 fields (80,200 possible pipes) Kruskal takes about 60 ms, Prim 30 ms, Dijkstra 3 ms and a 60-day schedule 48 ms. The MST saving over direct pipes grows with farm size (50% at 10 fields, 91% at 400).

**Trade-off found in Part 2.** The MST uses the least pipe (cheapest, least leakage), but the Dijkstra shortest-path tree gives water shorter, flatter routes to each field (average 404 m vs 525 m) at the price of 62% more pipe. The project builds the MST and uses Dijkstra as a check on routes.

## Assumptions and limits

These are simple models for study, not an engineering design.

- Farm data (positions, crops, terrain) is simulated. To use real data, change `make_farm()` to read your own field coordinates, elevations, crops and areas.
- Leakage is proportional to pipe length. Pressure and friction use simple constants (see the settings at the top of the file).
- The yield model is simplified (each day of water stress costs 3% of yield) and the rain forecast is assumed perfect.
- Pump placement is greedy, so it is not guaranteed optimal. On bigger farms it can suggest many small boosters (24 at 60 fields), and a real design would merge some of them.
- The scheduler's results depend on the water limit set in the code (80% of demand).

## Other languages

The project is in Python for simplicity. The code uses only arrays, sorting, heaps and loops, so it ports directly to C++, C or Java.
