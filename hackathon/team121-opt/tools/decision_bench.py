"""Run actual Strategy.act on fixed *synthetic snapshots*, without a referee."""
import argparse
import csv
import importlib.util
import math
from pathlib import Path
import platform
import random
import statistics
import sys
from time import perf_counter_ns
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASES = ("opening", "edge", "open_trail", "cut", "sealed_return", "last_turn", "restart")
COMMANDS = set("URDL")


class FixtureMap:
    def __init__(self, terrain, boosts, centres):
        self.terrain = terrain
        self.boosts = boosts
        self.calib = centres

    def move_steps(self, x, y):
        return 1 + ((x, y) in self.boosts)


def fixture(seed, seat, case):
    rng = random.Random(seed)
    n = 41
    terrain = [["." for _ in range(n)] for _ in range(n)]
    owner = [[-1] * n for _ in range(n)]
    contour = [[-1] * n for _ in range(n)]
    centres = [(5, 35), (35, 5)]
    for y in range(n):
        for x in range(n):
            r = rng.random()
            dx = x - 19
            dy = y - 24
            if r < (0.035 if seed % 2 else 0.095):
                terrain[y][x] = "#"
            elif r < 0.21 or dx * dx + dy * dy < 11:
                terrain[y][x] = "*"
    for side, (cx, cy) in enumerate(centres):
        for y in range(cy - 1, cy + 2):
            for x in range(cx - 1, cx + 2):
                terrain[y][x] = "."
                owner[y][x] = side
    # Author in seat-0 coordinates, reflect the entire fixture for seat 1.
    me = NS(x=5, y=35, dir="U", alive=True, restart_in=0)
    enemy = NS(x=35, y=5, dir="D", alive=True, restart_in=0)
    turn = 1
    if case in ("edge", "open_trail", "cut", "last_turn"):
        for y in range(22, 36):
            for x in range(0, 21):
                terrain[y][x] = "."
                owner[y][x] = 0
        me.x, me.y, me.dir, turn = 19, 22, "U", 370
    if case == "open_trail":
        me.y = 17
        for y in range(17, 22):
            contour[y][19] = 0
            terrain[y][19] = "."
        enemy.x, enemy.y, enemy.dir = 27, 19, "L"
    if case == "cut":
        enemy.x, enemy.y, enemy.dir = 24, 19, "R"
        for x in range(19, 25):
            contour[19][x] = 1
            terrain[19][x] = "."
    if case == "sealed_return":
        # Deliberately unreachable friendly territory stresses bounded search.
        for y in range(n):
            terrain[y][10] = "#"
        me.x, me.y, me.dir = 22, 28, "R"
        contour[28][21] = contour[28][22] = 0
        turn = 205
    if case == "last_turn":
        turn = 600
    if case == "restart":
        me.alive, me.restart_in, turn = False, 3, 510
    terrain[me.y][me.x] = terrain[enemy.y][enemy.x] = "."
    boosts = {(x + i, y + j) for x, y in [(8, 24), (19, 18), (26, 9)]
              for i in range(3) for j in range(3)}
    if seat == 1:
        terrain = [row[::-1] for row in terrain[::-1]]
        owner = [[1 - v if v >= 0 else v for v in row[::-1]] for row in owner[::-1]]
        contour = [[1 - v if v >= 0 else v for v in row[::-1]] for row in contour[::-1]]
        boosts = {(40 - x, 40 - y) for x, y in boosts}
        for b in (me, enemy):
            b.x, b.y = 40 - b.x, 40 - b.y
            b.dir = dict(U="D", D="U", L="R", R="L")[b.dir]
    game_map = FixtureMap(terrain, boosts, centres)
    return NS(seat=seat, map=game_map), NS(me=me, enemy=enemy, owner=owner, contour=contour,
                                          map=game_map, game=None, turn=turn)


def strategy_cls(label):
    if label != "v2":
        from bots.team121_agent.config import Config
        from bots.team121_agent.strategy import Strategy
        options = {"candidate": {}, "three_legs": {"allow_fourth_leg": False},
                   "no_intercept": {"allow_intercept": False}}
        return lambda init: Strategy(init, Config(**options[label]))
    path = ROOT / "baselines/v2/bots/team121_agent/strategy.py"
    spec = importlib.util.spec_from_file_location("team121_reference_v2", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.Strategy


def summarize(rows):
    for label in dict.fromkeys(r["version"] for r in rows):
        group = [r for r in rows if r["version"] == label]
        times = sorted(r["ms"] for r in group)
        print(f"{label}: n={len(times)}, mean={statistics.mean(times):.2f}, "
              f"p95={times[math.ceil(.95*len(times))-1]:.2f}, max={max(times):.2f} ms, "
              f"over_200={sum(t >= 200 for t in times)}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--versions", nargs="+", choices=["v2", "candidate", "three_legs", "no_intercept"], default=["v2", "candidate"])
    p.add_argument("--seeds", nargs="+", type=int, default=[1009, 1031, 1061, 1091])
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    rows = []
    classes = {v: strategy_cls(v) for v in args.versions}
    for seed in args.seeds:
        for seat in (0, 1):
            for case in CASES:
                for version, cls in classes.items():
                    init, state = fixture(seed, seat, case)
                    bot = cls(init)
                    start = perf_counter_ns()
                    cmd = bot.act(state)
                    ms = (perf_counter_ns() - start) / 1e6
                    metrics = bot._last_metrics
                    rows.append(dict(version=version, seed=seed, seat=seat, case=case,
                                     command=cmd, ms=round(ms, 4), legal=cmd in COMMANDS,
                                     exception=metrics.get("exception", ""), phase=metrics.get("phase", ""),
                                     projected_gain=metrics.get("projected_gain", ""),
                                     python=platform.python_version()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    summarize(rows)
    if any(not r["legal"] or r["exception"] for r in rows):
        raise SystemExit("invalid command or reported strategy exception")


if __name__ == "__main__":
    main()
