#!/usr/bin/env python3
"""テストの量を測り、テストの最適化（/dev-flow-test-prune）を勧めるかを決める。

使い方: test-footprint.py [--root DIR] [--junit XML] [--ratio R] [--seconds S]

- git で追跡しているファイルを、実装（split-source.py と同じ基準）とテストに分けて行数を数える
- --junit（test ステージのスイート全体の JUnit XML）があれば、テストの件数と直列の実行時間の合計を足す
- テストと実装の行数の比が --ratio（既定 2.5）を超えるか、実行時間の合計が --seconds（既定 600）を超えたら
  recommend=true。標準出力は JSON 1 行

既定値の根拠: 2026-11 の photo-incident-triage は比 3.8・直列 27 分で、量のわりに重大な不具合を捕まえていなかった。
比 1〜2 倍は手厚いテストの範囲（[opinion]）。
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)/|(^|/)test_[^/]*$|_test\.|\.test\.|\.spec\.|Test\.php$")


def load_split():
    spec = importlib.util.spec_from_file_location("split_source", HERE / "split-source.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--junit")
    ap.add_argument("--ratio", type=float, default=2.5)
    ap.add_argument("--seconds", type=float, default=600)
    a = ap.parse_args()
    split = load_split()
    try:
        listed = subprocess.run(["git", "ls-files"], cwd=a.root, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        print(json.dumps({"error": f"git ls-files に失敗しました: {e}"}, ensure_ascii=False))
        return 1
    src_lines = test_lines = test_files = 0
    for p in filter(None, listed.split("\n")):
        ext = os.path.splitext(p)[1]
        if ext not in split.SOURCE_EXT or "node_modules/" in p or "vendor/" in p or "fixtures/" in p:
            continue
        n = split.count_lines(os.path.join(a.root, p))
        if TEST_PATH.search(p):
            test_lines += n
            test_files += 1
        elif split.is_source(p):
            src_lines += n
    out = {"source_lines": src_lines, "test_lines": test_lines, "test_files": test_files,
           "ratio": round(test_lines / src_lines, 2) if src_lines else None}
    if a.junit and Path(a.junit).exists():
        cases = list(ET.parse(a.junit).iter("testcase"))
        out["tests"] = len(cases)
        out["seconds_serial"] = round(sum(float(c.get("time") or 0) for c in cases), 1)
    reasons = []
    if out["ratio"] is not None and out["ratio"] > a.ratio:
        reasons.append(f"テストが実装の {out['ratio']} 倍（目安 {a.ratio} 倍）")
    if out.get("seconds_serial", 0) > a.seconds:
        reasons.append(f"直列の実行時間が {out['seconds_serial']:.0f} 秒（目安 {a.seconds:.0f} 秒）")
    out["recommend"] = bool(reasons)
    out["reasons"] = reasons
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
