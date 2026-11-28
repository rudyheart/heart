"""Run a supplied referee CLI in both seats and retain original evidence."""
import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from time import monotonic

DEFAULT_CLI = ["{python}", "-m", "engine", "{p0}", "{p1}", "--seed", "{seed}"]
# Fixed before inspecting any official matches. Seed is an input to the supplied referee.
TRAIN = (41, 53, 67, 79, 97, 113, 127, 139, 151, 167)
HOLDOUT = (2027, 2063, 2089, 2111, 2137, 2179, 2203, 2221, 2267, 2293,
           2311, 2333, 2357, 2381, 2411, 2437, 2473, 2503, 2531, 2557)
METRICS = ("score", "area", "stars", "scraps", "illegal")


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def schedule(old, new, base, bot002, seeds):
    if old == new:
        raise ValueError("old and new bot IDs must differ")
    opponents = list(dict.fromkeys((bot002, base)))
    if old in opponents or new in opponents:
        raise ValueError("baseline bot ID collides with a team121 version")
    pairs = [(old, bot) for bot in opponents] + [(new, bot) for bot in opponents] + [(new, old)]
    for a, b in pairs:
        for seed in seeds:
            for seat in (0, 1):
                yield a, b, seed, seat, ([a, b] if seat == 0 else [b, a])


def pointer(data, path):
    if path == "":
        return data
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"JSON pointer required: {path!r}")
    for key in path[1:].split("/"):
        key = key.replace("~1", "/").replace("~0", "~")
        data = data[int(key)] if isinstance(data, list) else data[key]
    return data


def number(v, field, integral=False, nonnegative=True):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"invalid {field}: {v!r}")
    if (integral and int(v) != v) or (nonnegative and v < 0):
        raise ValueError(f"invalid {field}: {v!r}")
    return v


def normalize(doc, mapping):
    """Field paths are configured from real referee output; no score-based winner inference."""
    winner = pointer(doc, mapping["winner"])
    winner = mapping.get("winner_values", {}).get(str(winner), winner)
    if not ((type(winner) is int and winner in (0, 1)) or winner == "draw"):
        raise ValueError("unrecognised referee winner")
    turns = number(pointer(doc, mapping["turns"]), "turns", integral=True)
    factor = {"ms": 1.0, "us": .001, "ns": .000001, "s": 1000.0}[mapping["time_unit"]]
    sides = []
    for seat in (0, 1):
        def pick(name):
            return pointer(doc, mapping[name].replace("{seat}", str(seat)))
        stats = {key: number(pick(key), key, integral=True, nonnegative=key != "score") for key in METRICS}
        if stats["stars"] > stats["area"]:
            raise ValueError("stars exceed territory area")
        if "times" in mapping:
            samples = pick("times")
            if not isinstance(samples, list) or not samples:
                raise ValueError("no decision samples")
            samples = [number(v, "decision duration") * factor for v in samples]
            stats.update(n=len(samples), sum_ms=sum(samples), max_ms=max(samples),
                         over_200=sum(t >= 200 for t in samples))
        else:
            n = number(pick("count"), "decision count", integral=True)
            total = number(pick("time_sum"), "decision sum") * factor
            peak = number(pick("time_max"), "decision max") * factor
            if not n or total + .000001 < peak or total > n * peak + .000001:
                raise ValueError("inconsistent timing summary")
            over = number(pick("over_200"), "over_200", integral=True) if "over_200" in mapping else None
            if over is not None and (over > n or bool(over) != (peak >= 200)):
                raise ValueError("inconsistent time limit count")
            stats.update(n=n, sum_ms=total, max_ms=peak, over_200=over)
        sides.append(stats)
    return dict(winner=winner, turns=turns, sides=sides)


def result_doc(path):
    contents = path.read_text(encoding="utf-8-sig")
    try:
        return json.loads(contents)
    except json.JSONDecodeError:
        # The final nonempty line may be JSON following engine log lines.
        return json.loads(contents.rstrip().splitlines()[-1])


def summarize(records):
    groups = collections.defaultdict(list)
    for r in records:
        groups[r["subject"], r["opponent"]].append(r)
    out = []
    for (a, b), games in groups.items():
        ok = [r for r in games if r["status"] == "parsed"]
        row = dict(subject=a, opponent=b, scheduled=len(games), parsed=len(ok), missing=len(games)-len(ok))
        if ok:
            n = len(ok)
            wins = sum(r["winner"] == r["seat"] for r in ok)
            draws = sum(r["winner"] == "draw" for r in ok)
            us = [r["sides"][r["seat"]] for r in ok]
            them = [r["sides"][1-r["seat"]] for r in ok]
            row.update(wins=wins, draws=draws, losses=n-wins-draws, win_rate=wins/n,
                       draw_rate=draws/n, loss_rate=(n-wins-draws)/n,
                       complete_600=sum(r["turns"] == 600 for r in ok))
            for prefix, sides in (("our", us), ("their", them)):
                for metric in METRICS:
                    total = sum(s[metric] for s in sides)
                    row[f"{prefix}_{metric}"] = total if metric == "illegal" else total/n
                calls = sum(s["n"] for s in sides)
                row[f"{prefix}_decisions"] = calls
                row[f"{prefix}_mean_ms"] = sum(s["sum_ms"] for s in sides)/calls
                row[f"{prefix}_max_ms"] = max(s["max_ms"] for s in sides)
                row[f"{prefix}_over_200"] = (sum(s["over_200"] for s in sides)
                                               if all(s["over_200"] is not None for s in sides) else None)
            row["score_diff"] = row["our_score"] - row["their_score"]
        out.append(row)
    return out


def paired_comparison(records, old, new, opponents):
    """Only compare seeds with the four matched old/new and seat 0/1 results."""
    out = []
    for opponent in dict.fromkeys(opponents):
        groups = collections.defaultdict(dict)
        for row in records:
            if row["opponent"] == opponent and row["subject"] in (new, old):
                key = row["subject"], row["seat"]
                bucket = groups[row["seed"]]
                if key in bucket:
                    raise ValueError("duplicate subject, opponent, seed and seat")
                bucket[key] = row
        paired, excluded = [], []
        for seed, bucket in sorted(groups.items()):
            required = [(v, seat) for v in (new, old) for seat in (0, 1)]
            if any(key not in bucket or bucket[key]["status"] != "parsed" for key in required):
                excluded.append(seed)
                continue
            wins, margins = {}, {}
            for version in (old, new):
                wins[version], margins[version] = 0, 0
                for seat in (0, 1):
                    row = bucket[version, seat]
                    wins[version] += int(row["winner"] == seat)
                    margins[version] += row["sides"][seat]["score"] - row["sides"][1-seat]["score"]
            paired.append({"seed": seed, "win_rate_change": (wins[new] - wins[old]) / 2,
                           "score_margin_change": (margins[new] - margins[old]) / 2})
        entry = dict(opponent=opponent, complete_seeds=len(paired), excluded_seeds=excluded, by_seed=paired)
        if paired:
            for key in ("win_rate_change", "score_margin_change"):
                entry[key] = sum(row[key] for row in paired) / len(paired)
        out.append(entry)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kit", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--old", default="team121-v2")
    p.add_argument("--new", default="team121-opt")
    p.add_argument("--bot002", default="bot002")
    p.add_argument("--base", required=True, help="official reference bot ID from the kit")
    p.add_argument("--phase", choices=["baseline", "candidate", "all"], default="all")
    p.add_argument("--split", choices=["train", "holdout"], default="train")
    p.add_argument("--seeds", nargs="+", type=int)
    p.add_argument("--cli", type=Path, help="JSON array of referee arguments")
    p.add_argument("--mapping", type=Path, help="JSON pointers and units for authentic result data")
    p.add_argument("--result-file", help="optional per-game JSON path template, e.g. {run}/replay.json")
    p.add_argument("--timeout", type=int, default=300, help="whole-match process wall-clock limit in seconds")
    p.add_argument("--plan-only", action="store_true", help="print the schedule only")
    args = p.parse_args()
    seeds = args.seeds if args.seeds is not None else (TRAIN if args.split == "train" else HOLDOUT)
    if len(set(seeds)) != len(seeds):
        p.error("duplicate seed")
    pairs = list(schedule(args.old, args.new, args.base, args.bot002, seeds))
    if args.phase != "all":
        subject = args.old if args.phase == "baseline" else args.new
        pairs = [game for game in pairs if game[0] == subject]
    if args.plan_only:
        for subject, opponent, seed, seat, players in pairs:
            print(seed, *players, f"({subject} seat {seat})")
        return
    kit, out = args.kit.resolve(), args.out.resolve()
    if not (kit / "engine" / "game.py").is_file():
        p.error("official engine/game.py missing under --kit")
    if out.exists():
        p.error("--out must be a fresh directory to preserve raw evidence")
    cli = json.loads(args.cli.read_text(encoding="utf-8")) if args.cli else DEFAULT_CLI
    if not isinstance(cli, list) or not cli or not all(isinstance(x, str) for x in cli):
        p.error("--cli must contain an argv array")
    if not all(any("{"+k+"}" in arg for arg in cli) for k in ("p0", "p1", "seed")):
        p.error("--cli must forward {p0}, {p1}, and {seed}")
    mapping = json.loads(args.mapping.read_text(encoding="utf-8")) if args.mapping else None
    out.mkdir(parents=True)
    identities = {str(f.relative_to(kit)):fingerprint(f) for component in ("engine","sdk","bots")
                  for f in sorted((kit/component).rglob("*")) if f.is_file()
                  and f.suffix.lower() in (".py", ".json", ".exe", ".so", ".dll") and "__pycache__" not in f.parts}
    save(out / "manifest.json", dict(kit=str(kit), hashes=identities, cli=cli, split=args.split,
                                     seeds=seeds, schedule=pairs, mapping=mapping, result_file=args.result_file, phase=args.phase))
    records = []
    for idx, (subject, opp, seed, seat, players) in enumerate(pairs, 1):
        run = out / f"{idx:04d}"
        run.mkdir()
        fields = dict(python=sys.executable, p0=players[0], p1=players[1], seed=seed, kit=str(kit), run=str(run))
        cmd = [arg.format_map(fields) for arg in cli]
        row = dict(subject=subject, opponent=opp, seed=seed, seat=seat, argv=cmd, status="pending")
        save(run / "record.json", row)
        source = Path(args.result_file.format_map(fields)) if args.result_file else run / "stdout.log"
        if not source.is_absolute():
            source = kit / source
        if args.result_file and source.exists():
            p.error(f"existing output could be stale: {source}; use a unique path containing {{run}}")
        start = monotonic()
        with (run/"stdout.log").open("wb") as stdout, (run/"stderr.log").open("wb") as stderr:
            try:
                proc = subprocess.run(cmd, cwd=kit, stdout=stdout, stderr=stderr, timeout=args.timeout, check=False)
                row["exit_code"] = proc.returncode
                row["status"] = "exited_unverified" if not proc.returncode else "process_failure"
            except subprocess.TimeoutExpired:
                row["status"] = "match_timeout"
            except OSError as exc:
                row.update(status="launch_error", error=str(exc))
        row["wall_s"] = monotonic() - start
        if row["status"] == "exited_unverified" and mapping is not None:
            try:
                row.update(normalize(result_doc(source), mapping))
                row["source_sha256"] = fingerprint(source)
                if args.result_file and source.parent != run:
                    (run/"referee-result.json").write_bytes(source.read_bytes())
                row["status"] = "parsed"
            except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                row.update(status="parse_error", error=str(exc))
        save(run/"record.json", row)
        records.append(row)
    summary = summarize(records)
    save(out/"summary.json", summary)
    save(out/"paired.json", paired_comparison(records, args.old, args.new, (args.bot002, args.base)))
    keys = dict.fromkeys(key for row in summary for key in row)
    with (out/"summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if any(r["status"] != "parsed" for r in records):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
