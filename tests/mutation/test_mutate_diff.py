#!/usr/bin/env python3
"""dev-flow-implementation/scripts/mutate-diff.py のテスト。一時的な git リポジトリで実行して結果を確かめる。"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "dev-flow-implementation/scripts/mutate-diff.py"

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout


def new_repo(tmp):
    repo = tmp / "repo"
    repo.mkdir()
    sh(repo, "git", "init", "-q")
    sh(repo, "git", "config", "user.email", "t@example.com")
    sh(repo, "git", "config", "user.name", "t")
    (repo / "app").mkdir()
    (repo / "app/__init__.py").write_text("")
    (repo / "app/calc.py").write_text("def base():\n    return 1\n")
    (repo / "README.md").write_text("x\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-qm", "base")
    return repo, sh(repo, "git", "rev-parse", "HEAD").strip()


def run(repo, base, *extra, result_file=None):
    args = [sys.executable, str(SCRIPT), "--worktree", str(repo), "--base", base,
            "--test-cmd", f"{sys.executable} tests/check.py", *extra]
    if result_file:
        args += ["--result-file", str(result_file)]
    return subprocess.run(args, capture_output=True, text=True)


def check_masks():
    """複数行の文字列・コメントの中を変異の対象にしない（実プロジェクトで docstring の数字ばかり変異した不具合）"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("mutate_diff", SCRIPT)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)

    def ops(path, source, lineno):
        mask = md.code_mask(path, source)
        line = source.split("\n")[lineno - 1]
        return sorted({m["operator"] for m in md.mutants_for_line(path, lineno, line, mask[lineno - 1])})

    py = 'def f(a):\n    """docstring\n    12 個 a < b and True\n    """\n    x = f"{a + 1} < 2"  # 3 >= 4\n    return a >= 18\n'
    check("py docstring", ops("m.py", py, 3), [])
    check("py f-string の式の中はコード・文字の部分と行末コメントは外す", ops("m.py", py, 5), ["arith-add", "int"])
    check("py code", ops("m.py", py, 6), ["cmp-ge", "int"])
    check("py 構文エラーでも落ちない", ops("m.py", "def f(:\n    return a >= 1\n", 2), ["cmp-ge", "int"])
    js = "const a = 1;\n/* block\n   n > 2 && true\n*/\nconst t = `line\n  ${x} + 3`;\nif (a >= 2) { this.#p = !ok; } // 5 < 6\n"
    check("js block comment", ops("m.js", js, 3), [])
    check("js template literal の 2 行目", ops("m.js", js, 6), [])
    check("js code と # の private フィールド", ops("m.js", js, 7), ["bang", "cmp-ge", "int"])
    sh = 'if [ "$n" -gt 3 ]; then  # 4 > 5\n  echo "1 + 2"\nfi\n'
    check("sh の # コメントと文字列", (ops("m.sh", sh, 1), ops("m.sh", sh, 2)), (["int"], []))


def main():
    check_masks()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo, base = new_repo(tmp)
        # 変更: is_adult はテストで境界まで確かめる。double はテストが無い（生き残るはず）
        (repo / "app/calc.py").write_text(
            "def base():\n    return 1\n\n"
            "def is_adult(age):\n    return age >= 18  # 18 歳以上\n\n"
            "def double(x):\n    label = \"a + b\"\n    return x + x\n\n"
            "def doc():\n    \"\"\"説明の 1 行目。\n    2 行目に 12 と a < b と True がある\n    \"\"\"\n    return None\n"
        )
        (repo / "tests").mkdir()
        (repo / "tests/check.py").write_text(
            "import sys\nsys.path.insert(0, '.')\nfrom app.calc import is_adult\n"
            "assert is_adult(18) is True\nassert is_adult(17) is False\n"
        )
        (repo / "notes.txt").write_text("a == b\n")  # ソースでないファイルは対象外
        sh(repo, "git", "add", "-A")
        sh(repo, "git", "commit", "-qm", "change")
        original = (repo / "app/calc.py").read_text()
        # 先にテストを流して __pycache__ を作っておく（大きさの同じ変異を同じ秒に書くと、
        # キャッシュの古い中身で動いて結果を取り違えた不具合の再現）
        sh(repo, sys.executable, "tests/check.py")

        result = tmp / "result.json"
        result.write_text(json.dumps({"status": "completed", "result": {"integration": {"failed": 0}}}))
        p = run(repo, base, result_file=result)
        check("exit", p.returncode, 0)
        check("summary line", "独立ミューテーション:" in p.stdout, True)
        data = json.loads(result.read_text())
        r = data["result"]["independent_mutation"]
        check("keeps other fields", data["result"]["integration"], {"failed": 0})
        check("tool", r["tool"], "mutate-diff")
        check("total = killed + survived", r["total"], r["killed"] + r["survived"])
        files = {s["file"] for s in r["survivors"]}
        check("only source files", files <= {"app/calc.py"}, True)
        lines = {(s["line"], s["operator"]) for s in r["survivors"]}
        # 候補は 5 行目の cmp-ge・int と 9 行目の arith-add の 3 件（8 行目は文字列の中だけ）
        check("candidates", r["candidates"], 3)
        check("counts", (r["total"], r["killed"], r["survived"]), (3, 2, 1))
        check("テストの無い double の加算だけが生き残る", lines, {(9, "arith-add")})
        check("文字列の中は変えない", any(s["line"] == 8 for s in r["survivors"]), False)
        check("行末コメントの数字は変えない", any(s["mutated"].endswith("19 歳以上") for s in r["survivors"]), False)
        check("ファイルを元に戻す", (repo / "app/calc.py").read_text(), original)
        check("作業ツリーがきれい", sh(repo, "git", "status", "--porcelain", "--untracked-files=no"), "")

        # 同じコミットなら同じ変異を選ぶ（--max で絞ったとき）
        a = json.loads(run(repo, base, "--max", "2", "--out", str(tmp / "a.json")).returncode == 0
                       and (tmp / "a.json").read_text() or "{}")
        b = json.loads(run(repo, base, "--max", "2", "--out", str(tmp / "b.json")).returncode == 0
                       and (tmp / "b.json").read_text() or "{}")
        check("max", a.get("total"), 2)
        check("deterministic", a.get("survivors"), b.get("survivors"))

        # 変異を入れる前のテストが通らなければ中止する
        (repo / "tests/check.py").write_text("raise SystemExit(1)\n")
        sh(repo, "git", "commit", "-qam", "break tests")
        p = run(repo, base, "--out", str(tmp / "c.json"))
        check("baseline failing exit", p.returncode != 0, True)
        check("baseline failing message", "変異を入れる前のテストが通りません" in p.stderr, True)

        # 未コミットの変更があれば始めない
        (repo / "app/calc.py").write_text("dirty\n")
        p = run(repo, base, "--out", str(tmp / "d.json"))
        check("dirty exit", p.returncode != 0, True)
        check("dirty message", "未コミットの変更" in p.stderr, True)
        sh(repo, "git", "checkout", "--", "app/calc.py")

        # テストだけの変更なら変異なし
        (tmp / "two").mkdir()
        repo2, base2 = new_repo(tmp / "two")
        (repo2 / "tests").mkdir()
        (repo2 / "tests/check.py").write_text("pass\n")
        sh(repo2, "git", "add", "-A")
        sh(repo2, "git", "commit", "-qm", "tests only")
        p = run(repo2, base2, "--out", str(tmp / "e.json"))
        e = json.loads((tmp / "e.json").read_text())
        check("tests only", (p.returncode, e["total"], e["score"]), (0, 0, None))

    if failures:
        print("\n".join(failures))
        sys.exit(1)
    print("mutate-diff: OK")


if __name__ == "__main__":
    main()
