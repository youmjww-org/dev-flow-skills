#!/usr/bin/env python3
"""test-spec-writer の出力を機械的に採点する。

使い方:
  score-test-spec.py --project-dir DIR --scenario feature
  score-test-spec.py --project-dir DIR --scenario change --baseline-spec PATH --changed "REQ-003:modified,REQ-006:added"

出力: 各チェックの PASS/FAIL と score（0.0〜1.0）を表示し、JSON を最後に 1 行出力。
終了コード: すべての must チェックが PASS なら 0、それ以外 1。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VALIDATOR = os.path.join(HERE, "..", "..", "dev-flow", "hooks", "doc-validate.py")
sys.path.insert(0, os.path.join(HERE, "..", "..", "dev-flow", "hooks"))
from importlib.machinery import SourceFileLoader  # noqa: E402

dv = SourceFileLoader("doc_validate", VALIDATOR).load_module()

AMBIGUOUS = ["適切に", "必要に応じて", "できる限り", "場合がある", "など", "等）", "等。"]


def load(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    fm_src, body = dv.split_frontmatter(text)
    fm = dv.parse_yaml_subset(fm_src) if fm_src else {}
    return fm, body, text


def tc_sections(body):
    """### TC-NNN: ... から次の ### までを dict にする"""
    out = {}
    for m in re.finditer(r"^### (TC-\d{3,}):(.*?)(?=^### |^## |\Z)", body, re.M | re.S):
        out[m.group(1)] = m.group(2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--scenario", choices=["feature", "change"], required=True)
    ap.add_argument("--spec", default=None, help="採点対象（既定: doc/test-spec/*.md の先頭）")
    ap.add_argument("--baseline-spec", default=None, help="change: 変更前のテスト定義書")
    ap.add_argument("--changed", default="", help="change: REQ-003:modified,REQ-006:added")
    args = ap.parse_args()
    pd = os.path.abspath(args.project_dir)

    checks = []  # (name, must, passed, detail)

    def check(name, must, passed, detail=""):
        checks.append((name, must, bool(passed), detail))

    specs = [args.spec] if args.spec else sorted(glob.glob(os.path.join(pd, "doc", "test-spec", "*.md")))
    check("output_exists", True, bool(specs) and os.path.exists(specs[0]), "doc/test-spec/*.md が生成されている")
    if not specs or not os.path.exists(specs[0]):
        return finish(checks)
    spec = specs[0]

    # 1. スキーマ検証（hook と同じ）
    r = subprocess.run([sys.executable, VALIDATOR, "--project-dir", pd, spec], capture_output=True, text=True)
    check("schema_valid", True, r.returncode == 0, r.stdout.strip().splitlines()[-1] if r.stdout else "")

    fm, body, text = load(spec)
    tcs = [t for t in (fm.get("test_cases") or []) if isinstance(t, dict)]
    sections = tc_sections(body)
    req_ids = dv.collect_req_ids(pd)

    # 2. REQ 網羅（非機能要件は TC が無くてもよいので must にしない）
    covered = {str(c) for t in tcs for c in (t.get("covers") or [])}
    uncovered = sorted(req_ids - covered)
    ratio = (len(req_ids) - len(uncovered)) / len(req_ids) if req_ids else 0
    check("req_coverage>=0.8", True, ratio >= 0.8, f"{ratio:.2f} 未カバー: {uncovered}")
    check("req_coverage==1.0", False, ratio == 1.0, f"{ratio:.2f}")

    # 3. 各 TC の構造
    no_gherkin = [i for i, s in sections.items() if "```gherkin" not in s]
    no_io = [i for i, s in sections.items() if "入力" not in s or "期待出力" not in s]
    check("all_tc_have_gherkin", True, not no_gherkin, f"欠落: {no_gherkin}")
    check("all_tc_have_io_values", True, not no_io, f"欠落: {no_io}")
    bad_names = [str(t.get("id")) for t in tcs if not re.match(r"^(正常系|異常系|境界値|セキュリティ|パフォーマンス|不変条件|不具合再現)[:：]", str(t.get("title", "")))]
    check("tc_title_prefix", False, not bad_names, f"接頭辞なし: {bad_names}")

    # 4. 曖昧表現リント
    found = sorted({w for w in AMBIGUOUS if w in body})
    check("no_ambiguous_words", False, not found, f"検出: {found}")

    # 5. 正常系・異常系の両方がある（タイトル接頭辞、または「## 正常系/異常系」セクション配下の TC で判定）
    kinds = {m.group(1) for t in tcs for m in [re.match(r"^(正常系|異常系)", str(t.get("title", "")))] if m}
    for m in re.finditer(r"^## (正常系|異常系)[^\n]*\n(.*?)(?=^## |\Z)", body, re.M | re.S):
        if re.search(r"^### TC-\d{3,}", m.group(2), re.M):
            kinds.add(m.group(1))
    check("has_normal_and_error_cases", True, kinds >= {"正常系", "異常系"}, f"{sorted(kinds)}")

    # 6. change シナリオ: 既存 ID の保持と差分マーカー
    if args.scenario == "change":
        assert args.baseline_spec, "--baseline-spec が必要"
        bfm, _, _ = load(args.baseline_spec)
        base = {str(t["id"]): t for t in (bfm.get("test_cases") or []) if isinstance(t, dict) and t.get("id")}
        cur = {str(t["id"]): t for t in tcs if t.get("id")}
        missing = sorted(set(base) - set(cur))
        check("existing_ids_preserved", True, not missing, f"消えた ID: {missing}")
        changed = dict(x.split(":") for x in args.changed.split(",") if x)
        modified_reqs = {r for r, s in changed.items() if s == "modified"}
        added_reqs = {r for r, s in changed.items() if s == "added"}
        # 変更していない既存 TC はタイトルと covers が同一
        untouched = [i for i in base if not (set(map(str, base[i].get("covers") or [])) & modified_reqs)]
        altered = [i for i in untouched if i in cur and (cur[i].get("title") != base[i].get("title") or cur[i].get("status"))]
        check("untouched_tc_unchanged", True, not altered, f"無関係なのに変更された: {altered}")
        # modified REQ を covers する既存 TC は status: modified
        need_mod = [i for i in base if set(map(str, base[i].get("covers") or [])) & modified_reqs]
        not_marked = [i for i in need_mod if i in cur and cur[i].get("status") != "modified"]
        check("modified_tc_marked", True, not not_marked, f"status: modified 無し: {not_marked}")
        # added REQ に対して新 TC が status: added で、番号は既存最大 + 1 以降
        max_base = max((int(i.split("-")[1]) for i in base), default=0)
        new_tcs = [i for i in cur if i not in base]
        bad_new = [i for i in new_tcs if cur[i].get("status") != "added" or int(i.split("-")[1]) <= max_base]
        covers_added = any(set(map(str, cur[i].get("covers") or [])) & added_reqs for i in new_tcs)
        check("added_tc_marked_and_numbered", True, new_tcs and not bad_new, f"新規: {new_tcs} 不正: {bad_new}")
        check("added_req_covered", True, covers_added, f"added REQ {sorted(added_reqs)} を covers する新 TC")

    return finish(checks)


def finish(checks):
    must_total = sum(1 for c in checks if c[1])
    must_pass = sum(1 for c in checks if c[1] and c[2])
    total_pass = sum(1 for c in checks if c[2])
    for name, must, passed, detail in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {'[must]' if must else '[nice]'} {name}  {detail}")
    score = total_pass / len(checks) if checks else 0
    ok = must_pass == must_total
    print(f"score: {score:.2f} ({total_pass}/{len(checks)})  must: {must_pass}/{must_total}  → {'PASS' if ok else 'FAIL'}")
    print(json.dumps({"score": round(score, 3), "must_pass": must_pass, "must_total": must_total, "pass": ok,
                      "checks": [{"name": n, "must": m, "pass": p, "detail": d} for n, m, p, d in checks]}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
