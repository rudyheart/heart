import contextlib
from collections import deque
import io
import random
import time
from unittest.mock import patch
from types import SimpleNamespace as NS
import unittest

from bots.team121_agent.board import Board, Budget, Geometry, INF, map_signature
from bots.team121_agent.probe import interruptible
from bots.team121_agent.search import Route, enemy_arrivals, ray, route_to
from bots.team121_agent.strategy import Strategy, contact
from tools.decision_bench import FixtureMap, fixture


def setup_board(state):
    budget = Budget(5000)
    geo = Geometry(map_signature(state.map, budget), budget)
    return Board(geo, state, budget), budget


def blank(n=9):
    terrain = [["."] * n for _ in range(n)]
    game_map = FixtureMap(terrain, set(), [])
    return NS(map=game_map, owner=[[-1]*n for _ in range(n)], contour=[[-1]*n for _ in range(n)],
              me=NS(x=3,y=3,dir="U",alive=True,restart_in=0),
              enemy=NS(x=7,y=1,dir="D",alive=True,restart_in=0), turn=10, game=None)


def scalar_score(owner, terrain, calib, side):
    h, w = len(owner), len(owner[0])
    value = edges = 0
    for y in range(h):
        for x in range(w):
            if owner[y][x] != side or terrain[y][x] == "#":
                continue
            value += 3 if terrain[y][x] == "*" else 1
            if (x,y) in calib:
                continue
            for nx, ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
                if not (0 <= nx < w and 0 <= ny < h) or terrain[ny][nx] == "#" or owner[ny][nx] != side:
                    edges += 1
    return value - edges // 3


def scalar_capture(s, side, drawn, calib):
    n = len(s.owner)
    walls = {(x,y) for y in range(n) for x in range(n) if s.owner[y][x] == side or s.contour[y][x] == side} | drawn
    outside = {(x,y) for y in range(n) for x in range(n)
               if (x in (0,n-1) or y in (0,n-1)) and (x,y) not in walls}
    q = deque(outside)
    while q:
        x,y = q.popleft()
        for nx,ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
            pt = nx,ny
            if 0 <= nx < n and 0 <= ny < n and pt not in outside and pt not in walls:
                outside.add(pt)
                q.append(pt)
    return {(x,y) for y in range(n) for x in range(n) if (x,y) not in outside and (x,y) not in calib
            and s.owner[y][x] != side and s.map.terrain[y][x] != "#"}


class ScoreTests(unittest.TestCase):
    def test_flood_and_both_global_edge_roundings(self):
        for seed in range(36):
            rng = random.Random(seed)
            s = blank()
            calib = {(x,y) for y in (3,4,5) for x in (3,4,5)}
            s.map.calib = [(4,4)]
            for y in range(9):
                for x in range(9):
                    s.map.terrain[y][x] = rng.choice("....*#")
                    if s.map.terrain[y][x] != "#":
                        s.owner[y][x] = rng.choice((-1,-1,0,1))
                        s.contour[y][x] = rng.choice((-1,-1,-1,-1,0,1))
            board, budget = setup_board(s)
            g = board.g
            drawn = {(rng.randrange(9),rng.randrange(9)) for _ in range(11)}
            mask = sum(g.bits[g.index(x,y)] for x,y in drawn)
            for side in (0,1):
                cap = scalar_capture(s,side,drawn,calib)
                gold = sum(g.bits[g.index(x,y)] for x,y in cap)
                self.assertEqual(gold, board.capture(side,mask,budget))
                after = [r.copy() for r in s.owner]
                for x,y in cap:
                    after[y][x] = side
                score = lambda owner,i: scalar_score(owner,s.map.terrain,calib,i)
                self.assertEqual(board.scores[side], score(s.owner,side))
                self.assertEqual(board.gain(side,gold), score(after,side) - score(s.owner,side)
                                 - score(after,1-side) + score(s.owner,1-side))

    def test_open_edge_cannot_close_by_itself(self):
        s = blank()
        s.owner[0][0] = 0
        board,budget = setup_board(s)
        g=board.g
        trail = {(0,1),(0,2),(1,2),(2,2)}
        m = sum(g.bits[g.index(x,y)] for x,y in trail)
        self.assertFalse(board.capture(0,m,budget) & g.bits[g.index(1,0)])

    def test_reported_score_distinct_from_marginal_model(self):
        s=blank()
        s.scores = [91,7]
        b,_=setup_board(s)
        self.assertEqual(b.match_scores,(91,7))
        self.assertEqual(b.scores,(0,0))


class MovementTests(unittest.TestCase):
    def test_boost_nonterminal_first_step_is_in_eta(self):
        s=blank()
        s.map.boosts.add((3,3))
        s.map.terrain[3][5]="#"
        b,budget=setup_board(s)
        g=b.g
        a,end=g.edges[g.index(3,3)][1]
        self.assertEqual((a,end),(g.index(4,3),0))
        eta=enemy_arrivals(b,1,g.index(3,3),1,0,8,budget)
        self.assertEqual(eta[a],1)
        self.assertEqual(eta[g.index(5,3)],INF)
        self.assertIsNone(route_to(b,0,g.index(3,3),1,g.bits[a],1,budget,True))

    def test_boost_validates_own_trail_in_middle(self):
        s=blank()
        s.contour[3][4]=0
        s.map.boosts.add((3,3))
        b,budget=setup_board(s)
        g=b.g
        self.assertFalse(list(ray(b,0,Route(g.index(3,3)),1,2,4)))
        self.assertIsNone(route_to(b,0,g.index(3,3),1,g.bits[g.index(5,3)],1,budget,True))

    def test_intermediate_cut_and_home_are_checked(self):
        s=blank()
        s.map.boosts.add((3,3))
        s.contour[3][4]=1
        b,budget=setup_board(s)
        g=b.g
        route=route_to(b,0,g.index(3,3),1,b.trails[1],1,budget,attack=True)
        self.assertEqual(route.steps,(1,))
        self.assertEqual(route.p,g.index(5,3))
        s.contour[3][4]=-1
        s.owner[3][4]=0
        b,budget=setup_board(s)
        self.assertIsNone(route_to(b,0,g.index(3,3),1,b.owns[0],1,budget))
        s.owner[3][5]=0
        b,budget=setup_board(s)
        self.assertEqual(route_to(b,0,g.index(3,3),1,b.owns[0],1,budget).steps,(1,))

    def test_does_not_wrap_edges_or_reverse(self):
        s=blank()
        b,budget=setup_board(s)
        g=b.g
        self.assertEqual(g.edges[g.index(8,4)][1],(0,0))
        eta=enemy_arrivals(b,1,g.index(4,4),1,0,6,budget)
        self.assertGreater(eta[g.index(3,4)],1)
        self.assertEqual(eta[g.index(5,4)],1)

    def test_contact_each_substep(self):
        self.assertFalse(contact(10,13,(11,12),(12,12)))
        self.assertTrue(contact(10,13,(11,12),(12,11)))
        self.assertFalse(contact(10,13,(11,12),(12,11),1 << 12))
        self.assertTrue(contact(10,11,(11,12),(10,9)))
        self.assertFalse(contact(10,13,(11,11),(12,14)))

    def test_different_geometry_invalidates_cache(self):
        i,s=fixture(2027,0,"last_turn")
        bot=Strategy(i)
        bot.act(s)
        old=bot.geometry
        s.map.boosts.add((2,2))
        s.map.terrain[2][2]="."
        bot.act(s)
        self.assertIsNot(bot.geometry,old)


class StrategyTests(unittest.TestCase):
    def test_short_safe_intercept_both_seats(self):
        for seat, cmd in ((0,"U"),(1,"D")):
            init,s=fixture(1009,seat,"cut")
            bot=Strategy(init)
            self.assertEqual(bot.act(s),cmd)
            self.assertEqual(bot._last_metrics["phase"],"intercept")

    def test_opening_and_last_turn(self):
        for seat in (0,1):
            i,s=fixture(2027,seat,"opening")
            bot=Strategy(i)
            self.assertIn(bot.act(s),"URDL")
            self.assertEqual(bot._last_metrics["phase"],"expand")
            self.assertGreater(bot._last_metrics["projected_gain"],0)
            i,s=fixture(2027,seat,"last_turn")
            bot=Strategy(i)
            move=bot.act(s)
            self.assertEqual(bot._last_metrics["phase"],"hold")
            d="URDL".index(move)
            self.assertEqual(s.owner[s.me.y+( -1,0,1,0)[d]][s.me.x+(0,1,0,-1)[d]],seat)

    def test_deadline_returns_geometric_alternative_and_writes_no_output(self):
        i,s=fixture(1009,0,"opening")
        s.map.terrain[s.me.y-1][s.me.x]="#"
        s.map.terrain[s.me.y][s.me.x-1]="#"
        ticks=iter(range(200))
        bot=Strategy(i,clock=lambda: next(ticks)*.5)
        stdout=io.StringIO()
        stderr=io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(bot.act(s),"R")
        self.assertEqual((stdout.getvalue(),stderr.getvalue()),("",""))
        self.assertEqual(bot._last_metrics["phase"],"budget")

    def test_malformed_view_clears_plan_and_records_error(self):
        i,s=fixture(1009,0,"opening")
        s.contour=[]
        bot=Strategy(i)
        self.assertIn(bot.act(s),"URDL")
        self.assertIn("exception",bot._last_metrics)
        self.assertEqual(bot.actions,())
        self.assertIsNone(bot.expected)

    def test_simulate_on_copies_and_only_three_distinct_replies(self):
        i,s=fixture(1009,0,"opening")
        b,budget=setup_board(s)
        moves=[]
        class Copy:
            def __init__(self):
                self.beams=[NS(alive=True),NS(alive=True)]
            def step(self,actions):
                moves.append(tuple(actions))
                self.beams[0].alive = actions[0] == "R"
        class Game:
            def copy(self):
                return Copy()
            def step(self,actions):
                raise AssertionError("mutated live game")
        bot=Strategy(i)
        bot._last_metrics={"sim_calls":0}
        with patch("bots.team121_agent.strategy.probe_window", lambda ms: contextlib.nullcontext(True)):
            out=bot._safe_set(b,Game(),b.g.pos(s.me),b.g.pos(s.enemy),2,True,{0:1,1:1},budget)
        self.assertEqual(out,{1})
        self.assertEqual(len(moves),len(set(moves)))
        self.assertEqual(len(moves),6)

    def test_partial_simulation_excludes_observed_fatal_reply(self):
        i,s=fixture(1009,0,"opening")
        b,budget=setup_board(s)
        class Copy:
            def __init__(self): self.beams=[NS(alive=True),NS(alive=True)]
            def step(self,actions):
                if actions[1]=="L": raise ValueError("unavailable model reply")
                self.beams[0].alive=False
        bot=Strategy(i)
        bot._last_metrics={"sim_calls":0}
        with patch("bots.team121_agent.strategy.probe_window", lambda ms: contextlib.nullcontext(True)):
            out=bot._safe_set(b,NS(copy=Copy),b.g.pos(s.me),b.g.pos(s.enemy),2,True,{0:1,1:1},budget)
        self.assertEqual(out,{1})
        self.assertEqual(bot._last_metrics['sim_error'],'ValueError')

    @unittest.skipUnless(interruptible(), "requires an available main-thread POSIX timer")
    def test_stalled_simulation_interrupted_before_referee_deadline(self):
        init, state = fixture(1009, 0, "opening")
        board, _ = setup_board(state)
        bot = Strategy(init)
        bot._last_metrics = {"sim_calls": 0}
        def stalled():
            time.sleep(1.0)
            self.fail("the probe timer did not interrupt copy()")
        start = time.monotonic()
        allowed = bot._safe_set(board, NS(copy=stalled), board.g.pos(state.me), board.g.pos(state.enemy),
                                2, True, {0: 0, 1: 1}, Budget(95))
        self.assertEqual(allowed, {0})
        self.assertTrue(bot._last_metrics["sim_timeout"])
        self.assertLess(time.monotonic() - start, 0.2)
        self.assertTrue(interruptible())

    def test_uninterruptible_simulator_uses_local_choice(self):
        init, state = fixture(1009, 0, "opening")
        board, budget = setup_board(state)
        bot = Strategy(init)
        def should_not_run():
            self.fail("an unbounded simulator was invoked")
        with patch("bots.team121_agent.strategy.probe_window", lambda ms: contextlib.nullcontext(False)):
            result = bot._safe_set(board, NS(copy=should_not_run), board.g.pos(state.me),
                                   board.g.pos(state.enemy), 2, True, {0: 0, 1: 1}, budget)
        self.assertEqual(result, {0})

    def test_stale_suffix_rejected_and_cooldown_clears(self):
        i,s=fixture(1009,0,"opening")
        bot=Strategy(i)
        bot.act(s)
        bot.actions=(1,1,1)
        bot.expected=(998,1)
        s.me.alive=False
        self.assertEqual(bot.act(s),"U")
        self.assertIsNone(bot.expected)
        self.assertEqual(bot.actions,())

    def test_template_import_form(self):
        import subprocess, sys
        from pathlib import Path
        folder=Path(__file__).resolve().parents[1]/"bots/team121_agent"
        result=subprocess.run([sys.executable,"-c","from strategy import Strategy"],cwd=folder,
                              capture_output=True,check=True)
        self.assertEqual(result.stdout,b"")
