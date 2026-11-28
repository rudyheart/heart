"""Summarize only completed games from one experimental group, without mixing transports."""
import argparse
import csv
from pathlib import Path
import json
from official_sweep import paired_comparison,summarize


def collect(folder):
    output=[]
    for file in sorted(folder.glob("*/result.json")):
        r=json.loads(file.read_text())
        label=file.parent.name
        if "-s" not in label:
            continue
        subject,opponent,seed,seat=label.rsplit("-",3)
        seat=int(seat[1:])
        if r["names"][seat]!=subject or r["names"][1-seat]!=opponent or int(seed)!=r["seed"]:
            raise ValueError(f"seat mismatch in {file}")
        sides=[]
        for src in r["sides"]:
            sides.append(dict(src,sum_ms=src["sum_us"]/1000,max_ms=src["max_us"]/1000,
                              over_200=src["timeouts"]+src["connections"]))
        w=r["winner"] if r["winner"] is not None else ("double_loss" if r["reason"]=="double_loss" else "draw")
        output.append(dict(r,subject=subject,opponent=opponent,seat=seat,winner=w,status="parsed",sides=sides))
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("folder",type=Path)
    a=p.parse_args()
    records=collect(a.folder)
    if not records or len({r["mode"] for r in records})!=1:
        p.error("the group must contain completed games of a single transport")
    rows=summarize(records)
    for row in rows:
        games=[r for r in records if r["subject"]==row["subject"] and r["opponent"]==row["opponent"]]
        row["maps"]=len({r["map_sha256"] for r in games})
        row["styles"]=",".join(sorted({r["map_style"] for r in games}))
        for label,opponent in (("our",False),("their",True)):
            sides=[g["sides"][1-g["seat"] if opponent else g["seat"]] for g in games]
            for key in ("errors","timeouts","connections","crash"):
                row[f"{label}_{key}"]=sum(s[key] for s in sides)
        row["double_loss"]=sum(g["reason"]=="double_loss" for g in games)
    with (a.folder/"summary.csv").open("w",newline="",encoding="utf8") as f:
        w=csv.DictWriter(f,fieldnames=rows[0])
        w.writeheader();w.writerows(rows)
    (a.folder/"paired.json").write_text(json.dumps(paired_comparison(records,"v2","opt",
        sorted({r["opponent"] for r in records} - {"v2","opt"})),indent=2),encoding="utf8")
    for row in rows:
        print(row["subject"],row["opponent"],row["wins"],row["draws"],row["losses"],
              round(row["score_diff"],2),row["our_max_ms"],row["our_errors"])


if __name__=="__main__":
    main()
