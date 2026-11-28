"""Run independent matches in isolated processes, with paired seats. Resume completed games."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

from arena import DEFAULT_KIT


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kit",type=Path,default=DEFAULT_KIT)
    p.add_argument("--seeds",nargs="+",type=int,required=True)
    p.add_argument("--pairs",nargs="+",required=True,help="subject/opponent")
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--mode",choices=["engine","http","http-compat"],required=True)
    p.add_argument("--jobs",type=int,default=1)
    args=p.parse_args()
    if len(set(args.seeds))!=len(args.seeds) or len(set(args.pairs))!=len(args.pairs) or args.jobs<1:
        p.error("invalid or duplicated schedule")
    scheduled=[(pair,seed,seat) for pair in args.pairs for seed in args.seeds for seat in (0,1)]
    def run(job):
        pair,seed,seat=job
        a,b=pair.split("/")
        names=(a,b) if seat==0 else (b,a)
        folder=args.out/f"{a}-{b}-{seed}-s{seat}"
        path=folder/"result.json"
        if path.is_file():
            data=json.loads(path.read_text())
            if data["names"]!=list(names) or data["seed"]!=seed or data["mode"]!=args.mode:
                raise ValueError(f"existing result does not match schedule: {path}")
            return
        cmd=[sys.executable,str(Path(__file__).with_name("arena.py")), "--kit",str(args.kit.resolve()),
             "--p0",names[0],"--p1",names[1],"--seed",str(seed),"--mode",args.mode,"--out",str(folder)]
        proc=subprocess.run(cmd,capture_output=True,text=True,timeout=360)
        if proc.returncode:
            raise RuntimeError(proc.stderr[-1200:])
        r=json.loads(path.read_text())
        print(pair,seed,seat,r["turns"],r["sides"][seat]["score"],r["sides"][1-seat]["score"],
              r["sides"][seat]["errors"],flush=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(run,scheduled))


if __name__=="__main__":
    main()
