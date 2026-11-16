#!/usr/bin/env python3
"""テストの最適化: ほかのテストで代わりが利くテストを、カバレッジと変異の殺し方から洗い出す（pytest 用）。

使い方:
  test-prune.py analyze --worktree DIR --coverage-json COV.json [--junit BASE.xml] --runner CMD
                        [--mutants N] [--protect REGEX] [--workers K] --out PRUNE.json
  test-prune.py verify  --worktree DIR --prune PRUNE.json --runner CMD [--workers K] --out VERIFY.json

analyze:
  1. COV.json（`coverage json --show-contexts`。pytest-cov の `--cov-context=test` で集めたもの）から、
     テストごとに通った行と、行ごとに通ったテストを作る
  2. テストに通られた実装の行から、mutate-diff.py と同じ演算子で変異を N 件（既定 150）選ぶ（ファイルの間で偏らない
     よう、ファイルごとに順番に取る。乱数の種は HEAD で決まる）
  3. 変異ごとに、その行を通るテストだけを流し（JUnit XML で）、どのテストが変異を殺したかを記録する
  4. テスト関数（パラメータの行はまとめる）ごとに、通った行の集合と殺した変異の集合を作り、実行時間の長い順に、
     「取り除いても、残りのテストで行も変異もすべて覆える」ものを取り除き候補にする。--protect に一致する
     テスト（既定は TC-ID を名前に持つもの）は、同じ TC-ID のテストが 1 つも残らなくなる取り除き方をしない
  5. 準備と検証の形が同じで、値だけが違うテスト関数（同じファイル）を、parametrize にまとめる候補として挙げる
verify:
  analyze が選んだ変異をもう一度流し、取り除いた後のテストで殺せる変異が減っていないかを確かめる

どちらも、変異を入れたファイルは必ず元に戻す（途中で止めても戻す）。標準出力は 1 行の要約だけ。

限界: 同じ行を通るテストでも、確かめていること（assert）は違いうる。変異は標本なので、殺した変異の集合が
同じでも、標本の外の不具合を片方だけが捕まえることはある。だから結果は「候補」で、取り除くかどうかは
人間がレビューして決める（取り除く PR は hook が自動マージしない）。
"""
import argparse
import ast
import importlib.util
import json
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
MUTATE_DIFF = HERE.parents[2] / "dev-flow-implementation/scripts/mutate-diff.py"
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*\.py$|_test\.py$")
DEFAULT_PROTECT = r"(?i)(?<![a-z0-9])tc[-_]?\d{2,4}"  # test_tc001_… の「_」の後ろにも一致させる


def load_mutate_diff():
    spec = importlib.util.spec_from_file_location("mutate_diff", MUTATE_DIFF)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


def unit_of(nodeid):
    """パラメータの行（[...]）を除いたテスト関数の ID"""
    return re.sub(r"\[.*\]$", "", nodeid)


def tc_ids(name, protect):
    return {m.group(0).lower().replace("-", "").replace("_", "") for m in re.finditer(protect, name)}


# ── カバレッジ ───────────────────────────────────────────────────────────
# テストごとの「通った行」は、ファイルごとの整数のビット集合（bit n = n 行目）で持つ。行の組を集合に展開すると、
# 1.8 万件のテストで 20GB を超えた（2026-11 の photo-incident-triage で確認）。.coverage（SQLite）の
# numbits はこの形のままなので、そのまま読む

def _context_test(c):
    """pytest-cov の文脈「tests/x.py::test_a|run」をテストの nodeid にする（空はテストの外）"""
    return c.rsplit("|", 1)[0] if c else None


def load_coverage(path, worktree="."):
    """{nodeid: {file: 通った行のビット集合}}。setup / teardown の文脈も同じテストに数える。テストのファイルと
    worktree の外のファイルは除く。path は .coverage（SQLite）か、coverage json --show-contexts の出力"""
    worktree = str(worktree)
    masks = defaultdict(dict)

    def add(nodeid, f, mask):
        if not nodeid or TEST_FILE.search(f) or f.startswith(".."):
            return
        d = masks[nodeid]
        d[f] = d.get(f, 0) | mask

    if str(path).endswith(".json"):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for f, info in data.get("files", {}).items():
            for line, ctxs in (info.get("contexts") or {}).items():
                for c in ctxs:
                    add(_context_test(c), f, 1 << int(line))
        return masks
    import sqlite3
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    files = {i: (os.path.relpath(p, worktree) if os.path.isabs(p) else p) for i, p in con.execute("select id, path from file")}
    ctxs = {i: _context_test(c) for i, c in con.execute("select id, context from context")}
    rows = con.execute("select file_id, context_id, numbits from line_bits")
    n = 0
    for fid, cid, numbits in rows:
        add(ctxs.get(cid), files[fid], int.from_bytes(numbits, "little"))
        n += 1
    if n == 0:  # 分岐（arc）で測ったデータ
        for fid, cid, a, b in con.execute("select file_id, context_id, fromno, tono from arc"):
            m = (1 << a if a > 0 else 0) | (1 << b if b > 0 else 0)
            if m:
                add(ctxs.get(cid), files[fid], m)
    return masks


def covered_lines(masks):
    """{file: いずれかのテストが通った行のビット集合}"""
    out = defaultdict(int)
    for d in masks.values():
        for f, m in d.items():
            out[f] |= m
    return out


def bits(mask):
    n = 0
    while mask:
        if mask & 1:
            yield n
        mask >>= 1
        n += 1


def junit_cases(path):
    """JUnit XML（junit_family=xunit1）の testcase を {nodeid: (秒, failed)} にする"""
    out = {}
    if not path or not Path(path).exists():
        return out
    for tc in ET.parse(path).iter("testcase"):
        f = tc.get("file") or ""
        classname = tc.get("classname") or ""
        name = tc.get("name") or ""
        mod = f[:-3].replace("/", ".") if f.endswith(".py") else ""
        cls = classname[len(mod) + 1:] if mod and classname.startswith(mod + ".") else ""
        nodeid = "::".join(x for x in (f, cls.replace(".", "::"), name) if x)
        failed = any(ch.tag in ("failure", "error") for ch in tc)
        out[nodeid] = (float(tc.get("time") or 0), failed)
    return out


# ── 変異を入れて流す ───────────────────────────────────────────────────────

class Interrupted(Exception):
    pass


def on_signal(signum, frame):
    raise Interrupted(f"signal {signum}")


def run_selected(runner, worktree, nodeids, workers, timeout):
    """nodeids だけを流し、失敗した nodeid の集合を返す（timeout なら None）"""
    # テストが読み取り専用のディレクトリを残すと、pytest の一時ディレクトリの片付けに失敗して後の実行まで
    # エラーになる（photo-incident-triage で 600 件を超えた）。実行ごとに新しい basetemp を使い、片付けの失敗は無視する
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        args = Path(tmp) / "args.txt"
        args.write_text("\n".join(sorted(nodeids)) + "\n", encoding="utf-8")
        xml = Path(tmp) / "r.xml"
        par = f" -n {workers}" if workers > 1 and len(nodeids) > 8 else ""
        cmd = f"{runner}{par} -o junit_family=xunit1 --junitxml={xml} --basetemp={Path(tmp) / 'bt'} @{args}"
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": str(Path(tmp) / "pyc")}
        p = subprocess.Popen(cmd, shell=True, cwd=worktree, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, env=env)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
            return None
        cases = junit_cases(xml)
        failed = {n for n, (_, bad) in cases.items() if bad} & set(nodeids)
        if rc in (2, 3) or (rc == 1 and not failed):
            # 収集の失敗・中断（構文エラーになる変異など）: 流したテスト全部が落ちたものとみなす。
            # 4（使い方の誤り・テストが見つからない）と 5（テストが 0 件）は殺したことにしない
            failed = set(nodeids)
        return failed


class Mutator:
    """変異を書き、必ず元に戻す。更新時刻を 1 秒ずつ進めてキャッシュの取り違えを防ぐ（mutate-diff と同じ）"""

    def __init__(self, worktree):
        self.wt = worktree
        self.originals, self.mtimes = {}, {}
        self.clock = int(time.time()) + 1

    def _write(self, path, text):
        path.write_text(text, encoding="utf-8")
        self.clock += 1
        os.utime(path, (self.clock, self.clock))

    def apply(self, m):
        f = self.wt / m["file"]
        original = self.originals.setdefault(m["file"], f.read_text(encoding="utf-8"))
        self.mtimes.setdefault(m["file"], f.stat().st_mtime_ns)
        lines = original.split("\n")
        lines[m["line"] - 1] = m["_text"]
        self._write(f, "\n".join(lines))

    def revert(self, m):
        self._write(self.wt / m["file"], self.originals[m["file"]])

    def restore_all(self):
        for rel, original in self.originals.items():
            (self.wt / rel).write_text(original, encoding="utf-8")
            os.utime(self.wt / rel, ns=(self.mtimes[rel], self.mtimes[rel]))


def git(wt, *args):
    return subprocess.run(["git", *args], cwd=wt, capture_output=True, text=True, check=True).stdout


def run_mutants(wt, runner, mutants, covering, durations, workers, log=None, flaky=frozenset()):
    """{mutant の index: 殺したテストの集合（timeout は "timeout"）}。flaky（変異なしでも落ちたテスト）は数えない"""
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    mut = Mutator(wt)
    kills, note = {}, None
    try:
        for i, m in enumerate(mutants):
            tests = covering(m)
            if not tests:
                continue
            budget = sum(durations.get(t, 1.0) for t in tests)
            timeout = budget / max(1, min(workers, len(tests))) * 3 + 60
            mut.apply(m)
            try:
                failed = run_selected(runner, wt, tests, workers, timeout)
            finally:
                mut.revert(m)
            kills[i] = "timeout" if failed is None else sorted((failed & set(tests)) - flaky)
            if log:
                log(i, m, kills[i])
    except Interrupted as e:
        note = f"途中で止めました（{e}）。試した分だけを数えています"
    finally:
        mut.restore_all()
    dirty = git(wt, "status", "--porcelain", "--untracked-files=no").strip()
    if dirty:
        sys.exit(f"元に戻せていないファイルがあります（git checkout で戻してください）:\n{dirty}")
    return kills, note


def choose_mutants(md, wt, covered, n, seed):
    cands = defaultdict(list)
    for f in sorted(covered):
        lines = list(bits(covered[f]))
        p = wt / f
        if not p.exists():
            continue
        source = p.read_text(encoding="utf-8")
        text = source.split("\n")
        mask = md.code_mask(f, source)
        for ln in lines:
            if 1 <= ln <= len(text):
                cands[f].extend(md.mutants_for_line(f, ln, text[ln - 1], mask[ln - 1]))
    rng = random.Random(seed)
    for f in cands:
        rng.shuffle(cands[f])
    files = sorted(cands)
    rng.shuffle(files)
    chosen, k = [], 0
    while len(chosen) < n and any(cands[f] for f in files):
        f = files[k % len(files)]
        if cands[f]:
            chosen.append(cands[f].pop())
        k += 1
    total = sum(len(v) for v in cands.values()) + len(chosen)
    chosen.sort(key=lambda m: (m["file"], m["line"], m["operator"]))
    return chosen, total


# ── 取り除き候補の選び方 ───────────────────────────────────────────────────

def plan_removals(units, protect, keep_always=frozenset()):
    """units: {unit: {"masks": {file: ビット集合}, "kills": set, "seconds": float}}。残すテストを選び、残りを
    取り除き候補にする。まず TC-ID ごとに 1 つ（いちばん速いもの）を残し、次に実行時間の短い順に、まだ残した
    テストで覆えていない行か変異を 1 つでも足すものを残す。速いテストを優先して残すので、遅い重複が候補になる"""
    covered, killed, keep = defaultdict(int), set(), set()

    def take(u):
        keep.add(u)
        for f, m in units[u]["masks"].items():
            covered[f] |= m
        killed.update(units[u]["kills"])

    by_tc = defaultdict(list)
    for u in units:
        for t in tc_ids(u, protect):
            by_tc[t].append(u)
    order = lambda u: (units[u]["seconds"], u)  # noqa: E731
    for u in sorted(keep_always & set(units)):
        take(u)
    for t in sorted(by_tc):
        if not keep.intersection(by_tc[t]):
            take(min(by_tc[t], key=order))
    for u in sorted(units, key=order):
        if u in keep:
            continue
        d = units[u]
        if d["kills"] - killed or any(m & ~covered[f] for f, m in d["masks"].items()):
            take(u)
    return sorted((u for u in units if u not in keep), key=lambda u: (-units[u]["seconds"], u))


class _Shape(ast.NodeTransformer):
    def visit_Constant(self, node):
        return ast.copy_location(ast.Constant(value="_"), node)

    def visit_FunctionDef(self, node):
        node = self.generic_visit(node)
        node.name = "_"
        node.decorator_list = [d for d in node.decorator_list
                               if "parametrize" not in ast.unparse(d)]
        return node

    visit_AsyncFunctionDef = visit_FunctionDef


def parametrize_candidates(wt, test_files, min_statements=3):
    """同じファイルの中で、定数を除くと同じ形のテスト関数の組"""
    groups = []
    for rel in sorted(test_files):
        p = wt / rel
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        shapes = defaultdict(list)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                if len(node.body) < min_statements:
                    continue
                shape = ast.dump(_Shape().visit(ast.parse(ast.unparse(node)).body[0]))
                shapes[shape].append(node.name)
        for names in shapes.values():
            if len(names) >= 2:
                groups.append({"file": rel, "tests": names})
    return groups


# ── サブコマンド ───────────────────────────────────────────────────────────

def cmd_analyze(a):
    wt = a.worktree.resolve()
    if git(wt, "status", "--porcelain", "--untracked-files=no").strip():
        sys.exit("追跡しているファイルに未コミットの変更があります。きれいな状態で始めてください")
    head = git(wt, "rev-parse", "HEAD").strip()
    by_test = load_coverage(a.coverage_json, wt)
    if not by_test:
        sys.exit("カバレッジにテストの文脈がありません（pytest-cov の --cov-context=test で集めてください）")
    base = junit_cases(a.junit)
    durations = {n: s for n, (s, _) in base.items()}
    flaky = frozenset(n for n, (_, bad) in base.items() if bad)
    md = load_mutate_diff()
    mutants, total_cands = choose_mutants(md, wt, covered_lines(by_test), a.mutants, head)
    cover_cache = {}

    def covering(m):
        key = (m["file"], m["line"])
        if key not in cover_cache:
            bit = 1 << m["line"]
            cover_cache[key] = {t for t, d in by_test.items() if d.get(m["file"], 0) & bit}
        return cover_cache[key]

    log = None
    if a.progress:
        def log(i, m, k):
            print(f"[{i + 1}/{len(mutants)}] {m['file']}:{m['line']} {m['operator']} テスト {len(covering(m))} 件"
                  f" → {'timeout' if k == 'timeout' else len(k)} 件が殺した", file=sys.stderr, flush=True)
    kills, note = run_mutants(wt, a.runner, mutants, covering, durations, a.workers, log, flaky)

    units = defaultdict(lambda: {"masks": {}, "kills": set(), "seconds": 0.0, "nodeids": set()})
    for t, d in by_test.items():
        u = units[unit_of(t)]
        for f, m in d.items():
            u["masks"][f] = u["masks"].get(f, 0) | m
        u["seconds"] += durations.get(t, 0.0)
        u["nodeids"].add(t)
    for i, ks in kills.items():
        if ks == "timeout":
            continue
        for t in ks:
            units[unit_of(t)]["kills"].add(i)
    # 変異なしで落ちたテストは、殺した変異を数えられない（数えないと弱いテストに見える）ので取り除かない
    unstable = frozenset(unit_of(t) for t in flaky)
    removed = plan_removals(units, a.protect, unstable)
    tried = [i for i in kills]
    killed = [i for i in tried if kills[i] == "timeout" or kills[i]]
    test_files = {t.split("::", 1)[0] for t in by_test}
    total_seconds = sum(u["seconds"] for u in units.values())
    removed_seconds = sum(units[u]["seconds"] for u in removed)
    zero_kill = sorted(u for u, d in units.items()
                       if u not in unstable and not d["kills"]
                       and any(covering(mutants[i]) & d["nodeids"] for i in tried))
    report = {
        "tool": "test-prune", "head": head, "runner": a.runner, "protect": a.protect,
        "tests": len(by_test), "test_functions": len(units), "test_files": len(test_files),
        "seconds_total": round(total_seconds, 1),
        "mutants": {"candidates": total_cands, "tried": len(tried), "killed": len(killed),
                    "survived": len(tried) - len(killed),
                    "timeout": sum(1 for i in tried if kills[i] == "timeout")},
        "removable": {"functions": len(removed),
                      "nodeids": sum(len(units[u]["nodeids"]) for u in removed),
                      "seconds": round(removed_seconds, 1),
                      "share_of_functions": round(len(removed) / len(units), 3) if units else 0,
                      "share_of_seconds": round(removed_seconds / total_seconds, 3) if total_seconds else 0},
        "removable_functions": [{"test": u, "seconds": round(units[u]["seconds"], 2),
                                 "params": len(units[u]["nodeids"])} for u in removed],
        "zero_kill_functions": zero_kill,
        "parametrize_candidates": parametrize_candidates(wt, test_files),
        "baseline_failed": sorted(flaky),
        "kept_because_baseline_failed": len(unstable & set(units)),
        "mutant_list": [{**{k: v for k, v in m.items() if not k.startswith("_")}, "_text": m["_text"],
                         "covered_by_count": len(covering(m)), "killed_by": kills.get(i, [])}
                        for i, m in enumerate(mutants)],
        "note": note,
    }
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    r = report["removable"]
    print(f"OK test-prune analyze: テスト関数 {len(units)} 個のうち取り除き候補 {r['functions']} 個"
          f"（{r['share_of_functions']:.0%}・実行時間の {r['share_of_seconds']:.0%}）、"
          f"変異 {len(killed)}/{len(tried)} killed、parametrize 候補 {len(report['parametrize_candidates'])} 組 → {a.out}")


def collect(runner, wt):
    """いま実在するテストの nodeid の集合"""
    # -q が 2 つになると nodeid ではなくファイルごとの件数が出るので、起動コマンドの -q を外して 1 つだけ付ける
    base = re.sub(r"(?<!\S)(-q+|--quiet)(?!\S)", "", runner)
    p = subprocess.run(f"{base} --collect-only -q", shell=True, cwd=wt, capture_output=True, text=True)
    return {l.strip() for l in p.stdout.splitlines() if "::" in l and not l.startswith(" ")}


def cmd_verify(a):
    wt = a.worktree.resolve()
    if git(wt, "status", "--porcelain", "--untracked-files=no").strip():
        sys.exit("追跡しているファイルに未コミットの変更があります。取り除きをコミットしてから確かめてください")
    prune = json.loads(Path(a.prune).read_text(encoding="utf-8"))
    removed = {x["test"] for x in prune.get("removed_functions") or prune["removable_functions"]}
    mutants = prune["mutant_list"]

    existing = collect(a.runner, wt)

    def kept(nodeid):
        return nodeid in existing and unit_of(nodeid) not in removed

    def covering(m):
        # analyze のときにこの変異を殺したテストのうち、取り除かずに残したもの。plan_removals は殺した変異ごとに
        # 殺したテストを 1 つ以上残すので、これが空なら取り除き過ぎ。残したテストが本当にまだ殺すかを流して確かめる
        return {t for t in m["killed_by"] if kept(t)}

    before = {i for i, m in enumerate(mutants) if m["killed_by"] and m["killed_by"] != "timeout"}
    targets = [m for i, m in enumerate(mutants) if i in before]
    kills, note = run_mutants(wt, a.runner, targets, covering, {}, a.workers)
    lost = [{k: v for k, v in targets[i].items() if k not in ("_text", "killed_by")}
            for i in range(len(targets)) if i in kills and kills[i] != "timeout" and not kills[i]]
    lost += [{k: v for k, v in m.items() if k not in ("_text", "killed_by")}
             for i, m in enumerate(targets) if i not in kills]
    report = {"tool": "test-prune verify", "head": git(wt, "rev-parse", "HEAD").strip(),
              "mutants_killed_before": len(before), "mutants_killed_after": len(before) - len(lost),
              "lost": lost, "ok": not lost and note is None, "note": note}
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    status = "OK" if report["ok"] else "NG"
    print(f"{status} test-prune verify: 殺した変異 {report['mutants_killed_before']} → "
          f"{report['mutants_killed_after']}（失ったもの {len(lost)} 件）→ {a.out}")
    return 0 if report["ok"] else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    an = sub.add_parser("analyze")
    an.add_argument("--worktree", type=Path, required=True)
    an.add_argument("--coverage-json", required=True, help="coverage json --show-contexts の出力か、.coverage（SQLite）のパス")
    an.add_argument("--junit", help="変異なしで流したときの JUnit XML（junit_family=xunit1）。実行時間に使う")
    an.add_argument("--runner", required=True, help="pytest の起動コマンド（例: venv/bin/python -m pytest -q -p no:cacheprovider -o addopts= --tb=no）")
    an.add_argument("--mutants", type=int, default=150)
    an.add_argument("--protect", default=DEFAULT_PROTECT, help="1 つは残すテストの印（既定は TC-ID）")
    an.add_argument("--workers", type=int, default=1, help="pytest-xdist の並列数（変異 1 件のテストが 9 件以上のとき使う）")
    an.add_argument("--progress", action="store_true")
    an.add_argument("--out", required=True)
    ve = sub.add_parser("verify")
    ve.add_argument("--worktree", type=Path, required=True)
    ve.add_argument("--prune", required=True)
    ve.add_argument("--runner", required=True)
    ve.add_argument("--workers", type=int, default=1)
    ve.add_argument("--out", required=True)
    a = ap.parse_args()
    return cmd_analyze(a) if a.cmd == "analyze" else cmd_verify(a)


if __name__ == "__main__":
    sys.exit(main() or 0)
