"""Reproduce the eight v2 file identities from the pinned repository fragments."""
import base64
import hashlib
from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent
ARCHIVE_PREFIX = "exposure-territory-engine/"
FILES = {".gitignore", "README.md", "SOURCE_REVIEW.md", "STRATEGY_DESIGN.md",
         "bots/team121_agent/__init__.py", "bots/team121_agent/strategy.py",
         "tests/test_strategy_core.py", "tools/run_sweep.ps1"}


def read_entries():
    def part(folder, idx):
        return (BASE / folder / f"part{idx:02}.b64").read_bytes()
    # The two directories contain overlapping fragments from the same ZIP.
    long6 = part("v2_payload", 6)
    last = bytearray(part("v2zip_parts", 7))
    last[3792] = long6[4000 + 3792]  # take the intact byte of the test entry header
    chunks = [part("v2_payload", 1), *(part("v2zip_parts", i) for i in range(2, 6)),
              long6[:4000], last]
    encoded = bytearray(b"".join(chunks))
    if encoded[6833] != ord("2"):
        raise ValueError("fragments differ from the pinned source")
    # Search of the first diverging DEFLATE block produced one length+CRC match.
    encoded[6833] = ord("y")
    data = base64.b64decode(encoded + b"===")
    cursor = 0
    while data[cursor:cursor+4] == b"PK\x03\x04":
        h = struct.unpack_from("<4s5H3L2H", data, cursor)
        method, crc, compressed, length, namesize, extra = h[3], h[6], h[7], h[8], h[9], h[10]
        start = cursor + 30
        name = data[start:start+namesize].decode("utf-8")
        start += namesize + extra
        raw = data[start:start+compressed]
        body = zlib.decompress(raw, -15) if method == 8 else raw
        if len(body) != length or zlib.crc32(body) != crc:
            raise ValueError(f"ZIP length/CRC mismatch in {name}")
        cursor = start + compressed
        if not name.endswith("/"):
            if not name.startswith(ARCHIVE_PREFIX):
                raise ValueError("unexpected path")
            yield name[len(ARCHIVE_PREFIX):], body, crc


def main():
    seen = set()
    for name, data, crc in read_entries():
        if name not in FILES or name in seen:
            raise ValueError("entry not present in original file inventory")
        if data != (ROOT / "baselines/v2" / name).read_bytes():
            raise SystemExit(f"baseline differs: {name}")
        print(f"{crc:08x}  {hashlib.sha256(data).hexdigest()}  {name}")
        seen.add(name)
    if seen != FILES:
        raise SystemExit("missing v2 entries")


if __name__ == "__main__":
    main()
