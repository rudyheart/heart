"""Play with the original Game and original opponents; save auditable observations per game."""
import argparse
import csv
from collections import Counter
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KIT = ROOT.parents[1] / "player-kit"
COMMANDS = ("U","D","L","R")


def load_strategy(path):
    # Official example bots resolve lib/greedy beside strategy.py.
    sys.path.insert(0, str(path))
    name = "_bench_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    old = {n:sys.modules.pop(n) for n in ("lib","greedy") if n in sys.modules}
    try:
        spec = importlib.util.spec_from_file_location(name, path/"strategy.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod.Strategy
    finally:
        for n in ("lib","greedy"):
            sys.modules.pop(n,None)
        sys.modules.update(old)
        sys.path.remove(str(path))


def directories(kit):
    return {"v2":ROOT/"baselines/v2/bots/team121_agent", "opt":ROOT/"bots/team121_agent",
            **{s:kit/"bots"/s for s in ("bot001","bot002","bot003")}}


def summary(game, errors, history, mode, seed, payload, names):
    from engine.protocol import dumps
    sides=[]
    for s in (0,1):
        ticks=[r["us"][s] for r in history]
        statuses=Counter(r["status"][s] for r in history)
        downs=sum(e.get("t")=="down" and e["s"]==s for r in history for e in r.get("events",[]))
        sides.append(dict(score=game.score(s), area=game.owner.count(s),
                          stars=sum(o==s and cell=="*" for o,cell in zip(game.owner,game.map.tiles)),
                          scraps=downs, illegal=statuses["invalid"], errors=errors[s], timeouts=statuses["timeout"],
                          connections=statuses["connection"],crash=statuses["crash"],n=len(ticks),
                          sum_us=sum(ticks),max_us=max(ticks,default=0)))
    return dict(mode=mode, seed=seed, names=names, turns=game.turn, winner=game.winner,
                reason=game.reason,sides=sides, map_style=payload["style"],
                defects=game.map.tiles.count("#"), key_cells=game.map.tiles.count("*"),
                map_sha256=hashlib.sha256(dumps(payload).encode()).hexdigest())


def fast_game(kit, labels, seed, payload):
    """In-process real-engine matches. Before SDK arrival only the state transport is reconstructed."""
    from engine.game import Game
    from engine.protocol import turn_message
    from engine.rules import apply_turn
    # Only used for these in-process matches if the Python SDK is absent. Official
    # opponents import the two type hints; their actual algorithm is loaded unchanged.
    import importlib.util
    if importlib.util.find_spec("sdk") is None:
        annotations=ModuleType("sdk")
        annotations.InitState=annotations.TurnState=SimpleNamespace
        sys.modules["sdk"]=annotations
    g=Game(payload)
    bots=[]
    for s,name in enumerate(labels):
        init=SimpleNamespace(seat=s,map=g.map,max_turns=g.max_turns)
        variants={"wide": {"max_macro": 39, "max_leg": 16, "enemy_horizon": 42},
                  "three_legs": {"allow_fourth_leg": False}, "no_intercept": {"allow_intercept": False},
                  "no_relocate": {"relocate": False}}
        if name in variants:
            sys.path.insert(0,str(ROOT))
            from bots.team121_agent.strategy import Strategy
            from bots.team121_agent.config import Config
            bots.append(Strategy(init,Config(**variants[name])))
        else:
            bots.append(load_strategy(directories(kit)[name])(init))
    history=[]
    errors=[0,0]
    while not g.done:
        msg=turn_message(g)
        cmds,stats,micros=[],[],[]
        beams=[SimpleNamespace(**b) for b in msg["beams"]]
        for s,bot in enumerate(bots):
            state=SimpleNamespace(**{**msg,"beams":beams},me=beams[s],enemy=beams[1-s],seat=s,
                                  game=g.copy(),map=g.map)
            start=time.perf_counter_ns()
            try:
                command=bot.act(state)
                status="ok" if command in COMMANDS else "invalid"
            except Exception:
                command,status=None,"crash"
            elapsed=(time.perf_counter_ns()-start)//1000
            if elapsed>200000:
                command,status=None,"timeout"
            cmds.append(command)
            stats.append(status)
            micros.append(min(elapsed,200000))
        apply_turn(g,errors,stats,micros,[False,False])
        events=[] if g.done else g.step(cmds)
        history.append(dict(cmds=cmds,status=stats,us=micros,events=events))
    return g,errors,history


def http_game(kit, labels, payload, target, native=True):
    from contextlib import ExitStack
    from engine.match import Match,ProcessBot
    from engine.protocol import dumps
    from engine.replay import read_referee_log,verify_replay
    import secrets
    dirs=directories(kit)
    if native:
        from sdk.submission import submission_language
        client_class=ProcessBot
    else:
        class LocalHost(ProcessBot):
            def __init__(self, directory):
                self.directory=directory
                self.proc=None
                self.resources=ExitStack()
            def _command(self):
                return ([sys.executable,str(ROOT/"tools/compat_host.py"),"--kit",str(kit),
                         "--bot",str(self.directory)],kit)
        client_class=LocalHost
    with ExitStack() as resources:
        clients=[]
        for name in labels:
            directory=dirs[name]
            if native:
                submission_language(directory)
            bot=client_class(directory)
            resources.callback(bot.close)
            clients.append(bot)
        with (target/"judge.jsonl").open("w",encoding="utf8") as log:
            g,errors,replay=Match(payload,clients,names=labels,run_id=f"run-{secrets.randbits(64)}.1").play(log=log)
    logs=read_referee_log(target/"judge.jsonl")
    verified=verify_replay(replay, log=logs)
    if verified.metrics()!=g.metrics() or verified.winner!=g.winner:
        raise ValueError("replay mismatch")
    with gzip.open(target/"replay.json.gz","wt",encoding="utf8") as f:
        f.write(dumps(replay))
    with gzip.open(target/"judge.jsonl.gz","wb") as f:
        f.write((target/"judge.jsonl").read_bytes())
    (target/"judge.jsonl").unlink()
    return g,errors,[r for r in logs if "cmds" in r]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kit",type=Path,default=DEFAULT_KIT)
    p.add_argument("--p0",required=True)
    p.add_argument("--p1",required=True)
    p.add_argument("--seed",type=int,required=True)
    p.add_argument("--mode",choices=["engine","http","http-compat"],required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    kit=args.kit.resolve()
    sys.path.insert(0,str(kit))
    from engine.maps import generate
    payload=generate(args.seed)
    labels=[args.p0,args.p1]
    if args.out.exists():
        p.error("choose a fresh game directory")
    args.out.mkdir(parents=True)
    os.environ["ARENA_SEED"]=str(args.seed)
    if args.mode!="engine":
        game,errors,history=http_game(kit,labels,payload,args.out,native=args.mode=="http")
    else:
        game,errors,history=fast_game(kit,labels,args.seed,payload)
        with gzip.open(args.out/"turns.json.gz","wt",encoding="utf8") as f:
            json.dump(history,f,separators=(",",":"))
    result=summary(game,errors,history,args.mode,args.seed,payload,labels)
    (args.out/"result.json").write_text(json.dumps(result,indent=2),encoding="utf8")
    print(json.dumps(result),flush=True)


if __name__=="__main__":
    main()
