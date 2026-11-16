#!/usr/bin/env python3
"""dev-flow-compliance/scripts/split-source.py のテスト。一時的な git リポジトリで実行して結果を確かめる。"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "dev-flow-compliance/scripts/split-source.py"
failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def write(repo, path, lines):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x = 1\n" * lines)


def run(repo, *extra):
    p = subprocess.run([sys.executable, str(SCRIPT), "--root", str(repo), *extra], capture_output=True, text=True)
    return p, json.loads((repo / "doc/process/reviews/whole/chunks.json").read_text()) if p.returncode == 0 else None


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        write(repo, "app/a.py", 400)
        write(repo, "app/b.py", 400)
        write(repo, "app/sub/c.py", 300)
        write(repo, "lib/big.go", 1500)
        write(repo, "lib/d.ts", 100)
        # 対象外: テスト・文書・依存・生成物・未追跡
        write(repo, "tests/test_a.py", 999)
        write(repo, "app/a_test.go", 999)
        write(repo, "web/x.spec.ts", 999)
        write(repo, "node_modules/m/index.js", 999)
        write(repo, "doc/notes.py", 999)
        write(repo, "web/app.min.js", 999)
        (repo / "README.md").write_text("readme\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        write(repo, "app/untracked.py", 999)

        p, d = run(repo, "--max-lines", "1000")
        check("exit", p.returncode, 0)
        check("summary", p.stdout.startswith("OK split-source:"), True)
        paths = [f["path"] for c in d["chunks"] for f in c["files"]]
        check("実装ファイルだけをパスの順に", paths, ["app/a.py", "app/b.py", "app/sub/c.py", "lib/big.go", "lib/d.ts"])
        check("total", (d["total_files"], d["total_lines"]), (5, 2700))
        check("上限で詰める（1 ファイルで超えるものは単独）", [[f["path"] for f in c["files"]] for c in d["chunks"]],
              [["app/a.py", "app/b.py"], ["app/sub/c.py"], ["lib/big.go"], ["lib/d.ts"]])
        check("束の行数", [c["lines"] for c in d["chunks"]], [800, 300, 1500, 100])

        p, d = run(repo, "--max-lines", "1000", "--max-chunks", "2")
        check("束の上限に収める", len(d["chunks"]) <= 2, True)
        check("束を減らしても全ファイルを含む", sum(len(c["files"]) for c in d["chunks"]), 5)
        check("引き上げた上限を記録", d["max_lines_per_chunk"] > 1000, True)

    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run([sys.executable, str(SCRIPT), "--root", tmp], capture_output=True, text=True)
        check("git でなければ NG", (p.returncode, p.stdout.startswith("NG split-source")), (1, True))

    if failures:
        print("\n".join(failures))
        sys.exit(1)
    print("split-source: OK")


if __name__ == "__main__":
    main()
