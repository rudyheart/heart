import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from tools.official_sweep import HOLDOUT, TRAIN, normalize, paired_comparison, schedule, summarize


ROOT=Path(__file__).resolve().parents[1]
MAPPING=json.loads((ROOT/"tools/mapping.example.json").read_text())


def data():
    return {"winner":1,"turns":37,"players":[
        {"score":40,"area":44,"stars":2,"scraps":0,"illegal":10,"time_us":[1200,800]},
        {"score":-3,"area":5,"stars":0,"scraps":1,"illegal":0,"time_us":[200000,201000]}]}


class SweepTests(unittest.TestCase):
    def test_pairs_same_seed_both_seats_baselines_first_and_splits_disjoint(self):
        games=list(schedule("old","new","ref","bot002",[3,7]))
        self.assertEqual(len(games),20)
        self.assertEqual(games[0],("old","bot002",3,0,["old","bot002"]))
        for a,b in zip(games[::2],games[1::2]):
            self.assertEqual(a[:3],b[:3])
            self.assertEqual(a[-1],b[-1][::-1])
        self.assertFalse(set(TRAIN) & set(HOLDOUT))
        self.assertEqual(len(list(schedule("old","new","bot002","bot002",[3]))),6)

    def test_forfeit_is_scored_by_reported_winner_not_points(self):
        parsed=normalize(data(),MAPPING)
        rows=[dict(subject="old",opponent="bot002",seat=i,status="parsed",**parsed) for i in (0,1)]
        rows.append(dict(subject="old",opponent="bot002",seat=0,status="match_timeout"))
        s=summarize(rows)[0]
        self.assertEqual((s["scheduled"],s["parsed"],s["missing"]),(3,2,1))
        self.assertEqual((s["wins"],s["losses"],s["draws"]),(1,1,0))
        self.assertEqual(s["our_illegal"],10)
        self.assertEqual(s["our_max_ms"],201)
        self.assertEqual(s["our_over_200"],2)
        self.assertEqual(s["our_mean_ms"],100.75)
        self.assertEqual(s["complete_600"],0)

    def test_paired_comparison_excludes_incomplete_seed(self):
        base = normalize(data(), MAPPING)
        records = []
        for v in ("old", "new"):
            for seat in (0, 1):
                records.append(dict(base, subject=v, opponent="bot002", seat=seat, seed=7, status="parsed",
                                    winner=seat if v == "new" else 1-seat))
        records.append(dict(base, subject="new", opponent="bot002", seat=0, seed=8, status="parsed"))
        paired = paired_comparison(records, "old", "new", ["bot002"])[0]
        self.assertEqual(paired["complete_seeds"], 1)
        self.assertEqual(paired["excluded_seeds"], [8])
        self.assertEqual(paired["win_rate_change"], 1)
        self.assertEqual(paired["score_margin_change"], 0)

    def test_missing_evidence_and_draws(self):
        d=data()
        d["winner"]=-1
        self.assertEqual(normalize(d,MAPPING)["winner"],"draw")
        del d["players"][1]["scraps"]
        with self.assertRaises(KeyError):
            normalize(d,MAPPING)
        self.assertNotIn("win_rate",summarize([dict(subject="new",opponent="old",status="parse_error")])[0])

    def test_subprocess_plumbing_with_fixture_output_only(self):
        with TemporaryDirectory() as td:
            temp=Path(td)
            kit=temp/"kit"
            (kit/"engine").mkdir(parents=True)
            (kit/"engine/game.py").write_text("# placeholder for CLI plumbing test only\n")
            out=temp/"out"
            emitter=temp/"emit.py"
            emitter.write_text(f"print({json.dumps(data())!r})",encoding="utf-8")
            cli=temp/"argv.json"
            cli.write_text(json.dumps(["{python}",str(emitter),"{p0}","{p1}","{seed}"]))
            cmd=[sys.executable,str(ROOT/"tools/official_sweep.py"),"--kit",str(kit),"--out",str(out),
                 "--base","bot002","--phase","baseline","--seeds","7","--cli",str(cli),
                 "--mapping",str(ROOT/"tools/mapping.example.json")]
            subprocess.run(cmd,capture_output=True,check=True)
            self.assertEqual(json.loads((out/"summary.json").read_text())[0]["parsed"],2)
            self.assertTrue((out/"0002/stdout.log").is_file())
            proc=subprocess.run(cmd,capture_output=True)
            self.assertNotEqual(proc.returncode,0)
            # A process that exits cleanly without a schema must produce no win count.
            out2=temp/"logs_only"
            cmd[cmd.index(str(out))]=str(out2)
            proc=subprocess.run(cmd[:-2],capture_output=True)
            self.assertEqual(proc.returncode,2)
            self.assertNotIn("wins", json.loads((out2/"summary.json").read_text())[0])
