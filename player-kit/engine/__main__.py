"""Run the engine command for the Contest or player-kit distribution."""
import argparse
import json
from pathlib import Path
import random

from .maps import draw, generate, load_map
from .protocol import dumps, json_object
from .replay import game_result, read_referee_log, verify_replay
from .rules import RULESET


def generate_maps(args):
    seeds = ([args.seed + index for index in range(args.count)] if args.seed is not None
             else [random.randrange(1, 10 ** 6) for _ in range(args.count)])
    maps = [generate(seed, args.stage) for seed in seeds]
    if args.out:
        with args.out.open('w', encoding='utf-8') as output:
            json.dump(maps, output, ensure_ascii=False, indent=1)
        print('已保存 %d 张地图到 %s：%s' % (len(maps), args.out, ' '.join(board['id'] for board in maps)))
        return
    for board in maps:
        defects = sum(row.count('#') for row in board['terrain'])
        keys = sum(row.count('*') for row in board['terrain'])
        zones = '、'.join(f"{len(zone['cells'])} 格" for zone in board['boost_zones'])
        print(f"{board['id']}  缺陷 {defects} 格  关键图形区 {keys} 格  加速区 {zones}")
        print(draw(board) + '\n')


def verify_recording(args):
    if (args.map_file is None) != (args.map_sha256 is None):
        raise ValueError('核对封存地图时请同时提供 --map-file 和 --map-sha256')
    replay = json_object(args.replay.read_bytes())
    board = None
    if args.map_file is not None:
        sealed = {'file': args.map_file.read_bytes().decode('utf-8'), 'sha256': args.map_sha256}
        board, _ = load_map(sealed, replay['stage'])
    log = read_referee_log(args.log_file) if args.log_file is not None else None
    game = verify_replay(replay, board=board, log=log)
    print(dumps(game_result(game)))


def main(argv=None):
    if (Path(__file__).resolve().parents[1] / 'sdk' / 'submission.py').is_file():
        from .match import main as match_main
        return match_main(argv)

    parser = argparse.ArgumentParser(
        prog='python -m engine', description='曝光领地规则引擎工具',
        epilog='选手本地对战请在 player-kit/ 目录运行 python -m engine P1 P2 [--seed SEED]。',
    )
    commands = parser.add_subparsers(dest='command', required=True)
    maps = commands.add_parser('mapgen', help='生成天梯或测试用随机地图')
    maps.add_argument('--seed', type=int, default=None, help='起始种子；不填则随机')
    maps.add_argument('--count', type=int, default=1)
    maps.add_argument('--stage', default=RULESET, choices=(RULESET,))
    maps.add_argument('--out', type=Path, help='保存为 JSON 数组。不填则在终端绘制')
    maps.set_defaults(handler=generate_maps)
    replay = commands.add_parser('verify-replay', help='独立复核对局回放')
    replay.add_argument('replay', type=Path)
    replay.add_argument('--log-file', type=Path, help='裁判的 log.jsonl，可同时核对公开观测和日志')
    replay.add_argument('--map-file', type=Path, help='公开的封存地图原文件')
    replay.add_argument('--map-sha256', help='赛前公布的地图 SHA-256')
    replay.set_defaults(handler=verify_recording)
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
