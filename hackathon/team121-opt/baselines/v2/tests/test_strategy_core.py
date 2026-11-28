import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "team121_strategy", ROOT / "bots" / "team121_agent" / "strategy.py"
)
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class FakeMap:
    def __init__(self, terrain, boosts=None, calib=None):
        self.terrain = terrain
        self.boosts = set(boosts or [])
        self.calib = calib or []

    def move_steps(self, x, y):
        return 2 if (x, y) in self.boosts else 1


class FakeBeam:
    def __init__(self, x, y, direction, seat, alive=True, restart_in=0):
        self.x = x
        self.y = y
        self.dir = direction
        self.seat = seat
        self.alive = alive
        self.restart_in = restart_in


class FakeInit:
    def __init__(self, seat, map_obj):
        self.seat = seat
        self.map = map_obj


class FakeState:
    def __init__(self, me, enemy, owner, contour, map_obj, game=None):
        self.me = me
        self.enemy = enemy
        self.owner = owner
        self.contour = contour
        self.map = map_obj
        self.game = game


def make_board():
    terrain = [["." for _ in range(41)] for _ in range(41)]
    owner = [[-1 for _ in range(41)] for _ in range(41)]
    contour = [[-1 for _ in range(41)] for _ in range(41)]
    # A broad friendly rectangle; its lower edge is a legal base for a capture.
    for y in range(15, 21):
        for x in range(8, 24):
            owner[y][x] = 0
    me = FakeBeam(10, 20, "D", 0)
    enemy = FakeBeam(39, 2, "U", 1, alive=False, restart_in=5)
    map_obj = FakeMap(terrain)
    return terrain, owner, contour, me, enemy, map_obj


class StrategyCoreTests(unittest.TestCase):
    def test_direction_transition_respects_no_u_turn(self):
        terrain, owner, contour, me, enemy, map_obj = make_board()
        snap = mod.Snapshot(0, 1, me, enemy, owner, contour, terrain, map_obj, None)
        # Asking to move opposite the current heading is ignored by the game.
        nxt = mod._apply_action(snap, 10, 20, "D", "U")
        self.assertEqual(nxt, (10, 21, "D"))

    def test_boost_is_taken_from_start_cell(self):
        terrain, owner, contour, me, enemy, map_obj = make_board()
        map_obj.boosts.add((10, 20))
        snap = mod.Snapshot(0, 1, me, enemy, owner, contour, terrain, map_obj, None)
        nxt = mod._apply_action(snap, 10, 20, "D", "D")
        self.assertEqual(nxt, (10, 22, "D"))

    def test_rectangle_planner_finds_closed_capture(self):
        terrain, owner, contour, me, enemy, map_obj = make_board()
        snap = mod.Snapshot(0, 1, me, enemy, owner, contour, terrain, map_obj, None)
        start = (10, 20, "D")
        parent = {start: None}
        depths = {start: 0}
        result = mod._build_rectangle_candidates(snap, parent, depths, max_width=8, max_depth=7)
        self.assertIsNotNone(result)
        utility, commands, expected, gain, turns, enemy_eta = result
        self.assertGreater(gain, 5)
        self.assertGreater(utility, 1.35)
        self.assertEqual(len(commands), len(expected))
        self.assertGreater(turns, 0)

    def test_strategy_always_returns_a_legal_command(self):
        terrain, owner, contour, me, enemy, map_obj = make_board()
        init = FakeInit(0, map_obj)
        state = FakeState(me, enemy, owner, contour, map_obj)
        strategy = mod.Strategy(init)
        self.assertIn(strategy.act(state), ("U", "D", "L", "R"))


    def test_exact_flood_fill_captures_enclosed_cells(self):
        n = 41
        terrain = [["." for _ in range(n)] for _ in range(n)]
        owner = [[-1 for _ in range(n)] for _ in range(n)]
        contour = [[-1 for _ in range(n)] for _ in range(n)]
        # A narrow home strip; the trail closes a 2-cell pocket against it.
        for x in range(12, 15):
            owner[15][x] = 0
        me = FakeBeam(12, 15, "U", 0)
        enemy = FakeBeam(39, 2, "D", 1, alive=False, restart_in=5)
        map_obj = FakeMap(terrain)
        snap = mod.Snapshot(0, 1, me, enemy, owner, contour, terrain, map_obj, None)
        route = [(12, 15), (12, 14), (12, 13), (12, 12),
                 (13, 12), (14, 12), (14, 13), (14, 14), (14, 15)]
        capture = mod._compute_capture_set(snap, route[1:])
        self.assertIn((13, 13), capture)
        self.assertIn((13, 14), capture)
        self.assertIn((12, 14), capture)
        self.assertNotIn((12, 15), capture)

    def test_enemy_eta_uses_only_real_boost_zones(self):
        terrain = [["." for _ in range(41)] for _ in range(41)]
        owner = [[-1 for _ in range(41)] for _ in range(41)]
        contour = [[-1 for _ in range(41)] for _ in range(41)]
        me = FakeBeam(2, 2, "D", 0)
        boosted_enemy = FakeBeam(12, 10, "R", 1)
        map_obj = FakeMap(terrain, boosts={(12, 10)})
        snap = mod.Snapshot(0, 1, me, boosted_enemy, owner, contour, terrain, map_obj, None)
        dist = mod._enemy_turn_distance_map(snap)
        self.assertEqual(dist[10][14], 1)

        normal_enemy = FakeBeam(12, 12, "R", 1)
        map_obj2 = FakeMap(terrain)
        snap2 = mod.Snapshot(0, 1, me, normal_enemy, owner, contour, terrain, map_obj2, None)
        dist2 = mod._enemy_turn_distance_map(snap2)
        self.assertEqual(dist2[12][14], 2)

    def test_multishape_loop_planner_finds_candidate(self):
        terrain, owner, contour, me, enemy, map_obj = make_board()
        snap = mod.Snapshot(0, 1, me, enemy, owner, contour, terrain, map_obj, None)
        parent, depths = mod._bfs_inside_territory(snap, max_depth=8)
        result = mod._build_loop_candidates(snap, parent, depths)
        self.assertIsNotNone(result)
        utility, commands, expected, gain, turns, enemy_eta = result
        self.assertGreater(gain, 3.0)
        self.assertGreater(utility, 0.0)
        self.assertEqual(len(commands), len(expected))
        self.assertGreater(turns, 0)
        self.assertTrue(all(c in ("U", "D", "L", "R") for c in commands))


    def test_short_enemy_trail_interception_is_detected(self):
        terrain, owner, contour, me, enemy, map_obj = make_board()
        # Enemy has an exposed trail two cells east of our head.
        contour[20][12] = 1
        enemy = FakeBeam(30, 10, "L", 1, alive=True)
        snap = mod.Snapshot(0, 1, me, enemy, owner, contour, terrain, map_obj, None)
        route = mod._find_enemy_cut_route(snap, max_turns=6)
        self.assertEqual(route[:2], ["R", "R"])
        self.assertLessEqual(len(route), 6)


if __name__ == "__main__":
    unittest.main()
