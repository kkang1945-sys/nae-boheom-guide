"""Build a metadata-only report; raw public text is never copied into the report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from insurance_app.public_corpus import build_report, load_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("corpus/public_sources.json"))
    parser.add_argument("--corpus-dir", type=Path, default=Path("data/public_corpus"))
    parser.add_argument("--output", type=Path, default=Path("data/public_corpus/report.json"))
    args = parser.parse_args()
    report = build_report(load_manifest(args.manifest), args.corpus_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.output}: {len(report['sources'])} sources")


if __name__ == "__main__":
    main()
