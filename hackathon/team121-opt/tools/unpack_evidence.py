"""Unpack the recorded series into a new directory after checking every SHA-256."""
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python tools/unpack_evidence.py NEW_DIRECTORY")
    dest = Path(sys.argv[1]).resolve()
    source = Path(__file__).resolve().parents[1] / "results/evidence"
    entries = json.loads((source / "index.json").read_text())
    for entry in entries:
        file = source / entry["file"]
        if hashlib.sha256(file.read_bytes()).hexdigest() != entry["sha256"]:
            raise SystemExit(f"checksum mismatch: {file}")
    dest.mkdir(parents=True, exist_ok=False)
    for entry in entries:
        with ZipFile(source / entry["file"]) as archive:
            for member in archive.infolist():
                if dest not in (dest / member.filename).resolve().parents:
                    raise ValueError("invalid path")
            archive.extractall(dest)
    print(f"unpacked {sum(e['games'] for e in entries)} games into {dest}")


if __name__ == "__main__":
    main()
