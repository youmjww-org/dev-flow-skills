#!/usr/bin/env python3
"""全体レビューの読み手ごとに、実装のソースを分ける。

使い方: split-source.py [--root DIR] [--max-lines N] [--max-chunks K] [--out PATH]

- git で追跡している実装ファイル（テスト・文書・依存・生成物・lock を除く）を数える
- ディレクトリのまとまりを崩さないよう、パスの順に並べて 1 束が --max-lines 行（既定 9000）を超えない
  ように詰める。1 ファイルで超えるものは単独の束にする
- 束の数が --max-chunks（既定 6）を超えたら、上限に収まるよう 1 束の行数を引き上げて詰め直す
- 結果を JSON で --out（既定 doc/process/reviews/whole/chunks.json）に書き、標準出力には 1 行の要約だけを出す

行数の目安: 2026-11 に photo-incident-triage（4.4 万行）を 5 束（1 束 7〜10 千行）に分けて全文を読ませたところ、
1 束あたり 4〜8 分・約 25 万トークンで、どの束も全行を読み切った。
"""
import argparse
import json
import os
import subprocess
import sys

SOURCE_EXT = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".php", ".rb", ".java", ".kt", ".rs",
    ".cs", ".swift", ".scala", ".sh", ".bash", ".sql", ".tf", ".vue", ".svelte",
}
EXCLUDE_PARTS = {
    "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__", ".next", "coverage",
    "tests", "test", "__tests__", "spec", "e2e", "fixtures", "migrations", "doc", "docs",
}
TEST_MARKERS = ("_test.", ".test.", ".spec.", "test_", "Test.php")


def is_source(path):
    parts = path.split("/")
    if any(p in EXCLUDE_PARTS for p in parts[:-1]):
        return False
    name = parts[-1]
    if any(m in name for m in TEST_MARKERS) or name.endswith((".min.js", ".d.ts", "_pb2.py")):
        return False
    return os.path.splitext(name)[1] in SOURCE_EXT


def count_lines(path):
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def pack(files, max_lines):
    chunks, cur, cur_n = [], [], 0
    for path, n in files:
        if cur and cur_n + n > max_lines:
            chunks.append(cur)
            cur, cur_n = [], 0
        cur.append((path, n))
        cur_n += n
    if cur:
        chunks.append(cur)
    return chunks


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--max-lines", type=int, default=9000)
    ap.add_argument("--max-chunks", type=int, default=6)
    ap.add_argument("--out")
    a = ap.parse_args()
    try:
        listed = subprocess.run(["git", "ls-files"], cwd=a.root, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"NG split-source: git ls-files に失敗しました: {e}")
        return 1
    files = [(p, count_lines(os.path.join(a.root, p))) for p in sorted(listed.split("\n")) if p and is_source(p)]
    files = [(p, n) for p, n in files if n > 0]
    total = sum(n for _, n in files)
    max_lines = a.max_lines
    chunks = pack(files, max_lines)
    while len(chunks) > a.max_chunks:
        max_lines = int(max_lines * 1.2) + 1
        chunks = pack(files, max_lines)
    out = a.out or os.path.join(a.root, "doc/process/reviews/whole/chunks.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    data = {
        "total_files": len(files),
        "total_lines": total,
        "max_lines_per_chunk": max_lines,
        "chunks": [
            {"id": i + 1, "lines": sum(n for _, n in c), "files": [{"path": p, "lines": n} for p, n in c]}
            for i, c in enumerate(chunks)
        ],
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    sizes = "・".join(str(c["lines"]) for c in data["chunks"]) or "0"
    print(f"OK split-source: {os.path.relpath(out, a.root)} ファイル {len(files)} 個・{total} 行を {len(chunks)} 束（{sizes} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
