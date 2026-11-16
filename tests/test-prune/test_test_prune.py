#!/usr/bin/env python3
"""dev-flow-test-prune/scripts/test-prune.py のテスト。小さな pytest のプロジェクトを作って analyze と verify を流す。

必要: pytest・pytest-cov（CI ではこのテストの前に pip で入れる）。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "dev-flow-test-prune/scripts/test-prune.py"
RUNNER = f"{sys.executable} -m pytest -q -p no:cacheprovider -o addopts= --tb=no"
failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def sh(cwd, cmd, env=None):
    return subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, env=env)


# テストごとのカバレッジは ctrace のコアで集める（sysmon のコアは同じ行を 2 回目以降のテストで記録しない）
CTRACE = {**os.environ, "COVERAGE_CORE": "ctrace"}


APP = '''def is_adult(age):
    return age >= 18


def fee(n):
    if n > 10:
        return n * 2
    return n + 1
'''

TESTS = '''from app.calc import is_adult, fee


def test_tc001_adult_boundary():
    assert is_adult(18) is True
    assert is_adult(17) is False


def test_adult_boundary_again():
    # TC-001 と同じことをもう一度確かめている（取り除き候補になるはず）
    assert is_adult(18) is True
    assert is_adult(17) is False


def test_adult_weak():
    # 同じ行を通るが、確かめていることが弱い（bool は None にならないので、どの変異も殺さない）
    assert is_adult(30) is not None


def test_tc002_fee_small():
    assert fee(3) == 4


def test_tc003_fee_large():
    assert fee(11) == 22


class TestDup:
    """クラスの中身が全部候補なら、クラスごと消す"""

    def test_dup_large(self):
        assert fee(11) == 22


def test_fee_shape_a():
    x = 5
    y = fee(x)
    assert y == 6


def test_fee_shape_b():
    x = 7
    y = fee(x)
    assert y == 8
'''


def make_project(tmp):
    repo = tmp / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "app/__init__.py").write_text("")
    (repo / "app/calc.py").write_text(APP)
    (repo / "tests/__init__.py").write_text("")
    (repo / "tests/test_calc.py").write_text(TESTS)
    (repo / "conftest.py").write_text("")
    sh(repo, "git init -q && git config user.email t@example.com && git config user.name t && git add -A && git commit -qm base")
    p = sh(repo, f"{RUNNER} --cov=app --cov-context=test --cov-report= -o junit_family=xunit1 --junitxml={tmp}/base.xml tests",
           env=CTRACE)
    check("baseline pytest", p.returncode, 0)
    sh(repo, f"{sys.executable} -m coverage json --show-contexts -o {tmp}/cov.json")
    return repo


def check_sysmon_guard(tmp):
    """sysmon のコアで集めたカバレッジ（文脈が欠ける）では、取り除きを決めずに止まる"""
    repo = tmp / "repo"
    sh(repo, f"rm -f .coverage && {RUNNER} --cov=app --cov-context=test --cov-report= tests",
       env={**os.environ, "COVERAGE_CORE": "sysmon"})
    p = sh(repo, f"{sys.executable} {SCRIPT} analyze --worktree . --coverage-json .coverage --junit {tmp}/base.xml "
                 f"--runner '{RUNNER}' --out {tmp}/sysmon.json")
    if sys.version_info >= (3, 12):
        check("sysmon のカバレッジでは止まる", (p.returncode != 0, "COVERAGE_CORE=ctrace" in p.stderr), (True, True))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo = make_project(tmp)
        out = tmp / "prune.json"
        p = sh(repo, f"{sys.executable} {SCRIPT} analyze --worktree . --coverage-json {tmp}/cov.json "
                     f"--junit {tmp}/base.xml --runner '{RUNNER}' --mutants 50 --min-evidence 1 --out {out}")
        check("analyze exit", (p.returncode, p.stderr[-300:]), (0, ""))
        check("analyze summary", p.stdout.startswith("OK test-prune:"), True)
        r = json.loads(out.read_text())
        check("テスト数", (r["tests"], r["test_functions"]), (8, 8))
        removed = {x["test"].split("::")[-1] for x in r["removable_functions"]}
        check("TC と同じことを確かめる重複は取り除き候補", "test_adult_boundary_again" in removed, True)
        check("何も確かめないテストも取り除き候補", "test_adult_weak" in removed, True)
        check("TC-ID のテストは同じ TC が無くなるので残す", removed & {"test_tc001_adult_boundary", "test_tc002_fee_small",
                                                                    "test_tc003_fee_large"}, set())
        check("何も殺さないテストを挙げる", any(t.endswith("test_adult_weak") for t in r["zero_kill_functions"]), True)
        check("parametrize 候補", [sorted(g["tests"]) for g in r["parametrize_candidates"]],
              [["test_fee_shape_a", "test_fee_shape_b"]])
        check("変異を殺した", r["mutants"]["killed"] > 0, True)
        check("TC のテストを TC ごとに 1 つまで減らす参考の候補（自動では消さない）", r["tc_duplicate_candidates"], [])

        # replan: 変異を流さずに基準を変える。根拠の下限を上げると、通った行の変異が少ないテストは残る
        p = sh(repo, f"{sys.executable} {SCRIPT} replan --worktree . --prune {out} --coverage-json .coverage "
                     f"--junit {tmp}/base.xml --min-evidence 999 --out {tmp}/replan.json")
        rr = json.loads((tmp / "replan.json").read_text())
        check("replan exit", p.returncode, 0)
        check("根拠が足りなければ取り除かない", (rr["removable"]["functions"], rr["kept_low_evidence"]), (0, 8))
        check("replan は変異を流し直さない（同じ変異の結果）", rr["mutants"], r["mutants"])
        # .coverage（SQLite）を直接読んでも同じ結果になる（大きなプロジェクトは JSON に書き出せない）
        p = sh(repo, f"{sys.executable} {SCRIPT} analyze --worktree . --coverage-json .coverage "
                     f"--junit {tmp}/base.xml --runner '{RUNNER}' --mutants 50 --min-evidence 1 --out {tmp}/prune-db.json")
        rdb = json.loads((tmp / "prune-db.json").read_text())
        check("SQLite でも同じ取り除き候補", [x["test"] for x in rdb["removable_functions"]],
              [x["test"] for x in r["removable_functions"]])
        check("ファイルを元に戻す", (repo / "app/calc.py").read_text(), APP)
        check("作業ツリーがきれい", sh(repo, "git status --porcelain --untracked-files=no").stdout, "")

        # remove で候補を消してコミットし、verify で殺せる変異が減っていないことを確かめる
        p = sh(repo, f"{sys.executable} {SCRIPT} remove --worktree . --prune {out}")
        check("remove exit", p.returncode, 0)
        src = (repo / "tests/test_calc.py").read_text()
        check("候補だけを消す", ("def test_adult_boundary_again" in src, "def test_adult_weak" in src,
                                  "def test_tc001_adult_boundary" in src), (False, False, True))
        check("中身が全部候補のクラスはクラスごと消す", ("class TestDup" in src,
              any(x["test"].endswith("TestDup::test_dup_large") for x in r["removable_functions"])), (False, True))
        check("消したものを記録", len(json.loads(out.read_text())["removed_functions"]), len(r["removable_functions"]))
        sh(repo, "git commit -qam prune")
        p = sh(repo, f"{sys.executable} {SCRIPT} verify --worktree . --prune {out} --runner '{RUNNER}' --out {tmp}/v.json")
        v = json.loads((tmp / "v.json").read_text())
        check("verify OK", (p.returncode, v["ok"], v["mutants_killed_before"] == v["mutants_killed_after"]), (0, True, True))

        # TC のテストまで消すと、殺せる変異が減ったことを verify が見つける
        src = (repo / "tests/test_calc.py").read_text()
        start = src.index("def test_tc001_adult_boundary")
        end = src.index("\n\n\ndef ", start)
        (repo / "tests/test_calc.py").write_text(src[:start] + src[end + 3:])
        sh(repo, "git commit -qam over-prune")
        p = sh(repo, f"{sys.executable} {SCRIPT} verify --worktree . --prune {out} --runner '{RUNNER}' --out {tmp}/v2.json")
        v2 = json.loads((tmp / "v2.json").read_text())
        check("取り除き過ぎは NG", (p.returncode, v2["ok"], len(v2["lost"]) > 0), (1, False, True))

        check_sysmon_guard(tmp)

        # 未コミットの変更があれば始めない
        (repo / "app/calc.py").write_text("dirty\n")
        p = sh(repo, f"{sys.executable} {SCRIPT} analyze --worktree . --coverage-json {tmp}/cov.json "
                     f"--runner '{RUNNER}' --out {tmp}/x.json")
        check("dirty", (p.returncode != 0, "未コミットの変更" in p.stderr), (True, True))

    if failures:
        print("\n".join(failures))
        sys.exit(1)
    print("test-prune: OK")


if __name__ == "__main__":
    main()
