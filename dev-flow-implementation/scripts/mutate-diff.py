#!/usr/bin/env python3
"""変更した行に機械的に変異を入れ、テストが落ちるかを確かめる（implementer が変異を選ばない独立ミューテーション）。

使い方:
    mutate-diff.py --worktree DIR --base REV --test-cmd "CMD" [--max 12] [--timeout 秒]
                   [--exclude REGEX]... [--result-file PATH | --out PATH]

implementation STEP C.5（統合検証）の 3.6 で、QA implementer が int worktree（Dev の実装＋ユニットテスト＋
QA の仕様テスト）に対して実行する。

なぜ: implementer が自分で選んで試すミューテーション（result.mutation）は、2026-09〜11 の
photo-incident-triage の run で記録された 461 件がすべて killed だった。それでもレビュアーは、仕様で決まった値を
下限でしか見ないテスト・実装の出力を正解にしたテスト・生き残る変異を 43 件見つけている。自分で選べる検査は
「通る壊し方」を選びがちで、テストの強さの指標にならない（Goodhart）。ここでは変異を道具が決め、
結果は implementer に直させる目標にせず、レビュアーが仕分ける材料と台帳の記録にする。

やること:
1. git diff {base}...HEAD で追加・変更された行のうち、テスト・設定でないソースファイルの行を集める
2. 行ごとに機械的な変異（比較演算子・論理演算子・真偽値・否定・加減算・整数リテラル）を作る。文字列の中は触らない
3. 変異が多ければ --max 件を決まった乱数（base と HEAD から作る）で選ぶ。同じコミットなら毎回同じ変異になる
4. 変異なしでテストが通ることを確かめ（通らなければ中止）、変異を 1 件ずつ入れてテストを実行し、必ず元に戻す
5. テストが失敗した（またはタイムアウトした）変異は killed、通った変異は survived

出力（--result-file なら、その JSON の result.independent_mutation に書く）:
    {"tool": "mutate-diff", "base": …, "head": …, "test_cmd": …, "total": N, "killed": K, "survived": S,
     "timeout": T, "score": K/N, "survivors": [{"file", "line", "operator", "original", "mutated"}], "note": …}

注意: 変異でコンパイルや構文が壊れたときもテストは失敗するので killed に数える（演算子の前後に空白がある形だけを
変えるので、実際にはまれ）。等価な変異（意味の変わらない変異）は survived に残るので、生き残りを
そのまま「テストが弱い」と決めつけず、レビュアーが仕分ける。
"""
import argparse
import json
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOURCE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".php", ".go", ".rb", ".java", ".kt",
              ".rs", ".cs", ".swift", ".scala", ".sh", ".bash"}
TEST_PATH = re.compile(
    r"(^|/)(tests?|spec|specs|__tests__|__mocks__|e2e|testdata|fixtures|test-lint)/"
    r"|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$|(^|/)conftest\.py$|Test\.(php|java|kt)$"
)
SKIP_LINE = re.compile(r"^\s*($|#|//|/\*|\*|--|<!--|import\s|from\s+\S+\s+import\s|package\s|use\s|require[ (]|#include)")
STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')

# (名前, パターン, 置き換え)。パターンは文字列の外だけに当てる。演算子は前後に空白がある形だけを変える
OPERATORS = [
    ("cmp-eq", re.compile(r" (===?) "), lambda m: " !== " if m.group(1) == "===" else " != "),
    ("cmp-ne", re.compile(r" (!==?) "), lambda m: " === " if m.group(1) == "!==" else " == "),
    ("cmp-le", re.compile(r" <= "), lambda m: " < "),
    ("cmp-ge", re.compile(r" >= "), lambda m: " > "),
    ("cmp-lt", re.compile(r" < "), lambda m: " <= "),
    ("cmp-gt", re.compile(r" > "), lambda m: " >= "),
    ("logic-and", re.compile(r" (and|&&) "), lambda m: " or " if m.group(1) == "and" else " || "),
    ("logic-or", re.compile(r" (or|\|\|) "), lambda m: " and " if m.group(1) == "or" else " && "),
    ("bool", re.compile(r"\b(True|False|true|false)\b"),
     lambda m: {"True": "False", "False": "True", "true": "false", "false": "true"}[m.group(1)]),
    ("not", re.compile(r"\bnot "), lambda m: ""),
    ("bang", re.compile(r"(?<![!=<>\w)\]])!(?=[A-Za-z_($])"), lambda m: ""),
    ("arith-add", re.compile(r" \+ "), lambda m: " - "),
    ("arith-sub", re.compile(r" - "), lambda m: " + "),
    ("int", re.compile(r"(?<![\w.])(\d+)(?![\w.])"), lambda m: str(int(m.group(1)) + 1)),
]


def git(worktree, *args):
    return subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True, check=True).stdout


def changed_lines(worktree, base, excludes):
    """{path: [行番号(1 始まり), ...]}。追加・変更された行だけ"""
    diff = git(worktree, "diff", "-U0", "--no-color", "--diff-filter=AM", f"{base}...HEAD")
    out, path = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            p = line[4:]
            path = p[2:] if p.startswith("b/") else None
            if path and (Path(path).suffix not in SOURCE_EXT or TEST_PATH.search(path)
                         or any(re.search(x, path) for x in excludes)):
                path = None
            continue
        m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
        if m and path:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            out.setdefault(path, []).extend(range(start, start + count))
    return out


def string_spans(text):
    return [m.span() for m in STRING.finditer(text)]


def inside(spans, pos):
    return any(a <= pos < b for a, b in spans)


def mutants_for_line(path, lineno, text):
    if SKIP_LINE.match(text):
        return []
    # 行末のコメントは触らない（# / // の後ろ。文字列の中の # は除く）
    spans = string_spans(text)
    code_end = len(text)
    for m in re.finditer(r"#|//", text):
        if not inside(spans, m.start()):
            code_end = m.start()
            break
    out = []
    for name, pat, repl in OPERATORS:
        for m in pat.finditer(text, 0, code_end):
            if inside(spans, m.start()):
                continue
            mutated = text[:m.start()] + repl(m) + text[m.end():]
            if mutated != text:
                out.append({"file": path, "line": lineno, "operator": name,
                            "original": text.strip(), "mutated": mutated.strip(), "_text": mutated})
    return out


class Interrupted(Exception):
    pass


def on_signal(signum, frame):
    raise Interrupted(f"signal {signum}")


def run_tests(cmd, worktree, timeout):
    """(結果, 秒)。結果は passed / failed / timeout"""
    start = time.monotonic()
    # Python のバイトコードのキャッシュは更新時刻（秒）と大きさで古さを判断する。大きさの変わらない変異を
    # 同じ秒に書くと前の変異のキャッシュが使われるので、キャッシュを読み書きしない
    with tempfile.TemporaryDirectory() as cache:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": cache}
        return _run(cmd, worktree, timeout, env, start)


def _run(cmd, worktree, timeout, env, start):
    p = subprocess.Popen(cmd, shell=True, cwd=worktree, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=env)
    try:
        rc = p.wait(timeout=timeout)
        return ("passed" if rc == 0 else "failed"), time.monotonic() - start
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()
        return "timeout", time.monotonic() - start


class Writer:
    """ファイルを書くたびに更新時刻を 1 秒ずつ進める。更新時刻でキャッシュの古さを判断する道具
    （Python 以外のビルドキャッシュなど）にも、書き換えたことが確実に伝わるようにする"""

    def __init__(self):
        self.clock = int(time.time()) + 1

    def write(self, path, text):
        path.write_text(text, encoding="utf-8")
        self.clock += 1
        os.utime(path, (self.clock, self.clock))


def write_result(report, args):
    if args.result_file:
        path = Path(args.result_file)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data.setdefault("result", {})["independent_mutation"] = report
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--worktree", required=True, type=Path)
    ap.add_argument("--base", required=True, help="変更の起点（STEP A の {DIFF_BASE}）")
    ap.add_argument("--test-cmd", required=True, help="Dev のユニットテストと QA の仕様テストを流すコマンド")
    ap.add_argument("--max", type=int, default=12, help="試す変異の上限（既定 12）")
    ap.add_argument("--timeout", type=float, default=0,
                    help="変異 1 件あたりのテストの上限秒。0 なら変異なしの所要時間の 3 倍＋30 秒")
    ap.add_argument("--exclude", action="append", default=[], help="変異を入れないパスの正規表現（複数可）")
    ap.add_argument("--result-file", help="完了 JSON。result.independent_mutation に書く")
    ap.add_argument("--out", help="結果だけを書くファイル")
    args = ap.parse_args()

    wt = args.worktree.resolve()
    if git(wt, "status", "--porcelain", "--untracked-files=no").strip():
        sys.exit("追跡しているファイルに未コミットの変更があります。変異を入れる前にきれいな状態にしてください")
    head = git(wt, "rev-parse", "HEAD").strip()
    base = git(wt, "rev-parse", args.base).strip()
    report = {"tool": "mutate-diff", "base": base, "head": head, "test_cmd": args.test_cmd}

    candidates = []
    for path, lines in sorted(changed_lines(wt, base, args.exclude).items()):
        text = (wt / path).read_text(encoding="utf-8").split("\n")
        for n in sorted(set(lines)):
            if 1 <= n <= len(text):
                candidates.extend(mutants_for_line(path, n, text[n - 1]))
    report["candidates"] = len(candidates)
    if not candidates:
        report.update(total=0, killed=0, survived=0, timeout=0, score=None, survivors=[],
                      note="変異を入れられる変更行がありませんでした（テスト・設定だけの変更など）")
        write_result(report, args)
        print("独立ミューテーション: 変異を入れられる変更行なし")
        return

    rng = random.Random(f"{base}:{head}")
    chosen = candidates if len(candidates) <= args.max else rng.sample(candidates, args.max)
    chosen.sort(key=lambda m: (m["file"], m["line"], m["operator"]))

    status, seconds = run_tests(args.test_cmd, wt, args.timeout or None)
    if status != "passed":
        sys.exit(f"変異を入れる前のテストが通りません（{status}）。先に統合検証を通してください")
    timeout = args.timeout or seconds * 3 + 30

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    originals = {}
    mtimes = {}
    results = []
    writer = Writer()
    try:
        for m in chosen:
            f = wt / m["file"]
            original = originals.setdefault(m["file"], f.read_text(encoding="utf-8"))
            mtimes.setdefault(m["file"], f.stat().st_mtime_ns)
            lines = original.split("\n")
            lines[m["line"] - 1] = m["_text"]
            try:
                writer.write(f, "\n".join(lines))
                outcome, _ = run_tests(args.test_cmd, wt, timeout)
            finally:
                writer.write(f, original)
            results.append((m, outcome))
    except Interrupted as e:
        report["note"] = f"途中で止めました（{e}）。試した分だけを数えています"
    finally:
        for rel, original in originals.items():
            (wt / rel).write_text(original, encoding="utf-8")
            os.utime(wt / rel, ns=(mtimes[rel], mtimes[rel]))

    dirty = git(wt, "status", "--porcelain", "--untracked-files=no").strip()
    if dirty:
        sys.exit(f"元に戻せていないファイルがあります（git checkout で戻してください）:\n{dirty}")

    survivors = [{k: v for k, v in m.items() if not k.startswith("_")} for m, o in results if o == "passed"]
    killed = sum(1 for _, o in results if o != "passed")
    report.update(
        total=len(results), killed=killed, survived=len(survivors),
        timeout=sum(1 for _, o in results if o == "timeout"),
        score=round(killed / len(results), 2) if results else None,
        survivors=survivors,
    )
    write_result(report, args)
    print(f"独立ミューテーション: {killed}/{len(results)} killed・生き残り {len(survivors)}（候補 {len(candidates)}）")


if __name__ == "__main__":
    main()
