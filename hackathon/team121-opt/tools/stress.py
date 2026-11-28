"""Exercise two persistent Strategy instances for 600 state callbacks each; no referee."""
import argparse
import csv
from pathlib import Path
from time import perf_counter_ns
from decision_bench import CASES, COMMANDS, fixture, strategy_cls, summarize


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--seed", type=int, default=8093)
    args=p.parse_args()
    rows=[]
    for seat in (0,1):
        fixtures=[fixture(args.seed + i,seat,name) for i,name in enumerate(CASES)]
        bot=strategy_cls("candidate")(fixtures[0][0])
        for turn in range(600):
            idx = turn % len(fixtures)
            init,state = fixtures[idx]
            state.turn=turn+1
            start=perf_counter_ns()
            cmd=bot.act(state)
            ms=(perf_counter_ns()-start)/1e6
            m=bot._last_metrics
            rows.append(dict(version="candidate",seat=seat,turn=turn,seed=args.seed+idx,case=CASES[idx],
                             command=cmd,legal=cmd in COMMANDS,ms=round(ms,4),
                             phase=m["phase"],exception=m.get("exception","")))
        bot.end({})
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=rows[0])
        w.writeheader()
        w.writerows(rows)
    summarize(rows)
    if any(not row["legal"] or row["exception"] or row["ms"] >= 200 for row in rows):
        raise SystemExit("stress threshold failed")


if __name__ == "__main__":
    main()
