"""Inventory a user-downloaded AI-Hub OCR directory without reading image pixels."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--sample-json", type=int, default=20)
    args = parser.parse_args()
    if not args.root.is_dir():
        raise SystemExit("AI-Hub 자료 폴더를 찾을 수 없습니다.")
    extensions = Counter()
    json_shapes = Counter()
    json_count = 0
    for path in args.root.rglob("*"):
        if not path.is_file():
            continue
        extensions[path.suffix.lower() or "(none)"] += 1
        if path.suffix.lower() == ".json" and json_count < args.sample_json:
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
                shape = tuple(sorted(value)) if isinstance(value, dict) else (type(value).__name__,)
                json_shapes[str(shape)] += 1
                json_count += 1
            except (OSError, UnicodeError, json.JSONDecodeError):
                json_shapes["unreadable"] += 1
    print(json.dumps({"extensions": dict(extensions), "sampled_json_shapes": dict(json_shapes)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
