import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from bots.team121_agent.board import Board, Budget, Geometry, map_signature

KIT = Path(__file__).resolve().parents[3] / "player-kit"
sys.path.insert(0, str(KIT))
from engine.game import Game
from engine.maps import generate
from engine.protocol import init_message, parse_reply, turn_message
from engine.rules import apply_turn


class RefereeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = generate(41)

    def test_board_matches_real_flood_and_score_including_calibration(self):
        g = Game(self.payload)
        self.assertEqual(g.metrics()["score"], [9,9])
        geo = Geometry(map_signature(g.map, Budget(1000)), Budget(1000))
        rng = random.Random(19)
        for trial in range(25):
            sim=g.copy()
            cells=[k for k,t in enumerate(g.map.tiles) if t!="#" and g.map.base[k]<0]
            for k in rng.sample(cells,600):
                sim.owner[k]=rng.randrange(2)
            for k in rng.sample(cells,70):
                sim.trail[k]=rng.randrange(2)
            board=Board(geo,NS(game=sim,score=sim.metrics()["score"]),Budget(1000))
            for side in (0,1):
                self.assertEqual(board.scores[side],sim.score(side))
                actual=sim.fill_cells(side)
                want=sum(geo.bits[geo.index(k%41,k//41)] for k in actual)
                self.assertEqual(board.capture(side,0,Budget(1000)),want)
                gain=board.gain(side,want)
                projected=sim.copy()
                for k in actual:
                    projected.owner[k]=side
                self.assertEqual(gain,projected.score(side)-sim.score(side)+sim.score(1-side)-projected.score(1-side))

    def test_boost_can_close_then_start_new_trail(self):
        g=Game(self.payload)
        for y in range(1,40):
            for x in range(1,39):
                if g.map.move_steps(x,y)==2 and all(g.map.tiles[g.idx(x+i,y)]!="#" for i in (0,1,2)):
                    break
            else:
                continue
            break
        beam=g.beams[0]
        beam.x,beam.y,beam.dir=x,y,"R"
        g.trail[g.idx(x,y)]=0
        g.owner[g.idx(x+1,y)]=0
        old=g.copy()
        events=g.step(["R","D"])
        self.assertIn(("close",0),[(e["t"],e.get("s")) for e in events])
        self.assertEqual(g.trail[g.idx(x+2,y)],0)
        self.assertEqual((old.beams[0].x,old.trail[old.idx(x,y)]),(x,0))

    def test_cumulative_malformed_and_valid_reverse(self):
        g=Game(self.payload)
        errors=[0,0]
        for i in range(19):
            status="invalid" if i%2==0 else "ok"
            apply_turn(g,errors,[status,"ok"],[5,9],[False,False])
            if not g.done: g.step([None,"U"])
        self.assertEqual((errors,g.winner,g.reason),([10,0],1,"errors"))
        g=Game(self.payload)
        self.assertEqual(init_message(g,1)["seat"],1)
        self.assertEqual(turn_message(g)["turn"],1)
        self.assertEqual(parse_reply(b'{"turn":1,"cmd":"D"}'),(1,"D"))
        self.assertIsNone(parse_reply(b'{"turn":1,"cmd":"X"}'))
        g.step(["D","U"])
        self.assertEqual([b.dir for b in g.beams],["U","D"])

    def test_respawn_exact_fifth_turn_and_clears_only_trail(self):
        g=Game(self.payload)
        beam=g.beams[0]
        beam.x,beam.y,beam.dir=0,0,"L"
        extra=next(k for k,c in enumerate(g.map.tiles) if c=="*" and g.map.base[k]<0)
        g.owner[extra]=0
        g.trail[g.idx(3,0)]=0
        g.step(["L","D"])
        self.assertEqual((beam.alive,beam.respawn),(False,5))
        self.assertNotIn(0,g.trail)
        self.assertEqual(g.owner[extra],0)
        for remaining in (4,3,2,1):
            g.step(["U","D"])
            self.assertEqual((beam.alive,beam.respawn),(False,remaining))
        g.step(["U","D"])
        self.assertTrue(beam.alive)
        self.assertEqual((beam.x,beam.y),tuple(g.map.calib[0]))
