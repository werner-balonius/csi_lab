#!/usr/bin/env python3
"""Copy text-only Pilot evaluations into the repository.

Local evaluation folders also contain participant images. This script copies
the written quality decisions while removing image-link sections.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "docs/experiments/20260811_pilot/manifest.csv"
DEFAULT_OUTPUT = REPO_ROOT / "docs/experiments/20260811_pilot/evaluations"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def text_only(source: Path) -> str:
    lines = source.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## 图像"):
            lines = lines[:index]
            break
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    args = parse_args()
    with args.manifest.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in rows:
        source = args.results_root / row["directory"] / "evaluation/EVALUATION.md"
        if not source.is_file():
            raise SystemExit(f"missing evaluation: {source}")
        destination = args.output_dir / f"{row['trial']}.md"
        destination.write_text(text_only(source), encoding="utf-8")
        grouped[row["class"]].append(row)

    index = [
        "# 逐 Trial 文字质量报告",
        "",
        "这里保存本地评估报告的文字部分，不包含 RGB-D 人物截图。",
        "",
        "| 类别 | Trial | 判定 |",
        "|---|---|---|",
    ]
    for label in sorted(grouped):
        for row in grouped[label]:
            verdict = "有效" if row["valid"] == "yes" else "排除"
            index.append(
                f"| `{label}` | [`{row['trial']}`](./{row['trial']}.md) | {verdict} |"
            )
    (args.output_dir / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} text-only evaluations to {args.output_dir}")


if __name__ == "__main__":
    main()
