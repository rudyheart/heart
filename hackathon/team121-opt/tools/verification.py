"""Recompute every recorded in-process result using official maps, Game, rules and original inputs."""
import argparse
import gzip
import json
from pathlib import Path
import sys
from arena import DEFAULT_KIT, summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--kit",type=Path,default=DEFAULT_KIT)
    a=p.parse_args()
    sys.path.insert(0,str(a.kit.resolve()))
    from engine.maps import generate
    from engine.game import Game
    from engine.rules import apply_turn
    from engine.replay import verify_replay
    count=0
    for path in sorted(a.directory.rglob("result.json")):
        orig=json.loads(path.read_text())
        payload=generate(orig["seed"])
        if orig["mode"]=="engine":
            history=json.loads(gzip.decompress((path.parent/"turns.json.gz").read_bytes()))
            game=Game(payload)
            errors=[0,0]
            for row in history:
                if game.done:
                    raise ValueError(f"extra turn in {path}")
                apply_turn(game,errors,row["status"],row["us"],[False,False])
                expected=[] if game.done else game.step(row["cmds"])
                if expected!=row["events"]:
                    raise ValueError(f"event mismatch at {path}, turn {game.turn}")
        else:
            replay=json.loads(gzip.decompress((path.parent/"replay.json.gz").read_bytes()))
            logs=[json.loads(line) for line in gzip.decompress((path.parent/"judge.jsonl.gz").read_bytes()).splitlines()]
            game=verify_replay(replay,log=logs)
            history=[row for row in logs if "cmds" in row]
            errors=next(row["errors"] for row in reversed(logs) if "errors" in row)
        expected=summary(game,errors,history,orig["mode"],orig["seed"],payload,orig["names"])
        if not game.done or expected!=orig:
            raise ValueError(f"result mismatch: {path}")
        count+=1
    if not count:
        raise SystemExit("no completed games")
    print(f"verified {count} archived games")


if __name__=="__main__":
    main()
