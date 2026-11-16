#!/usr/bin/env python3
"""グループのレビュー結果と implementer の完了 JSON の要点を、git で追跡する台帳に 1 行ずつ足す。

使い方: group-ledger.py <N> [--root DIR]

implementation STEP H で mark-group-done.sh から呼ばれる（hook ではない）。
reviews/ と results/ は run の中だけで使う作業ファイルで git では追跡しない（#94）。次の run で同じ
グループ番号のファイルが上書きされると、レビューで何が見つかったか・何往復したかが後から分からなくなる。
dev-flow 自体の改修（どの仕組みが効いているか）を記録から判断できるよう、要点だけをここに残す。

読むもの:
- doc/process/state.json … run の開始時刻（harness.started_at）と kind
- doc/process/reviews/group-{N}[-pK]-{dev|qa|mut}-{app|infra}-r{K}.json … 更新時刻が run の開始より後のものだけ
  （mut は統合検証で生き残った変異の仕分け。reviewer-mutants.md）
- doc/process/results/group-{N}[-pK]-{dev|qa}-{app|infra}.json … 同上

書くもの: doc/process/metrics/review-ledger.jsonl（追記のみ。同じ run の同じファイルは 2 回書かない＝冪等）
1 行 1 ファイル。指摘は件数と blocker / major の rule・file・problem（先頭 300 字）だけを残し、fix は残さない。
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

LEDGER = "doc/process/metrics/review-ledger.jsonl"
PROBLEM_CHARS = 300
SEVERITIES = ("blocker", "major", "minor", "info")
# group-102-dev-app-r3.json / group-5-p2-qa-app-r1.json（QA の part は {GROUP_N} = N-pK）
REVIEW_RE = re.compile(r"^group-(?P<g>\d+)(?:-p(?P<p>\d+))?-(?P<role>dev|qa|mut)-(?P<team>app|infra)-r(?P<r>\d+)\.json$")
RESULT_RE = re.compile(r"^group-(?P<g>\d+)(?:-p(?P<p>\d+))?-(?P<role>dev|qa)-(?P<team>app|infra)\.json$")


def parse_ts(s):
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def run_start(state):
    h = state.get("harness") or {}
    t = parse_ts(h.get("started_at"))
    if t:
        return t
    hist = h.get("stage_history") or []
    return parse_ts(hist[0].get("started_at")) if hist else None


def load_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def first_word(v):
    # engine に「claude (codex usage limit)」のようにフォールバックの理由が続くことがある
    return str(v).split()[0] if v else "unknown"


def review_row(data):
    findings = [f for f in (data.get("findings") or []) if isinstance(f, dict)]
    counts = {s: sum(1 for f in findings if f.get("severity") == s) for s in SEVERITIES}
    serious = [
        {
            "severity": f.get("severity"),
            "rule": f.get("rule") or "none",
            "file": f.get("file"),
            "problem": str(f.get("problem") or "")[:PROBLEM_CHARS],
            # A: レビュアーが実際に動かして確かめた指摘（攻撃・ミューテーションの再現など）
            "verified": bool(f.get("verified_by_execution")),
        }
        for f in findings
        if f.get("severity") in ("blocker", "major")
    ]
    verdicts = data.get("uncertainty_verdicts") or []
    mutants = [m for m in (data.get("mutant_verdicts") or []) if isinstance(m, dict)]
    row_mutants = None
    if mutants:
        row_mutants = {v: sum(1 for m in mutants if m.get("verdict") == v)
                       for v in ("spec", "branch", "equivalent", "unspecified")}
        row_mutants["needs_human"] = sum(1 for m in mutants if m.get("needs_human"))
    return {
        "type": "review",
        "engine": first_word(data.get("engine") or data.get("reviewer_engine")),
        "model": data.get("model"),
        "status": data.get("status"),
        "reviewed_commit": data.get("reviewed_commit"),
        "counts": counts,
        "serious": serious,
        "needs_human": sum(1 for v in verdicts if isinstance(v, dict) and v.get("verdict") == "needs_human"),
        "mutant_verdicts": row_mutants,
    }


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def as_dict(v):
    # 古い run の完了 JSON には、同じ項目が文字列や配列で入っているものがある
    return v if isinstance(v, dict) else {}


def result_row(data):
    r = as_dict(data.get("result"))
    mutation = [m for m in as_list(r.get("mutation")) if isinstance(m, dict)]
    responses = [x for x in as_list(r.get("review_responses")) if isinstance(x, dict)]
    unit = as_dict(r.get("unit_tests"))
    lint = as_dict(r.get("lint"))
    self_review = as_dict(r.get("self_review"))
    integration = as_dict(r.get("integration"))
    independent = as_dict(r.get("independent_mutation"))
    fixed = self_review.get("fixed")
    return {
        "type": "result",
        "status": data.get("status"),
        "confidence": data.get("confidence"),
        "needs_human_review": data.get("needs_human_review"),
        "uncertainty_points": len(as_list(data.get("uncertainty_points"))),
        "self_review_fixed": fixed if isinstance(fixed, int) else None,
        # implementer が自分で選んで試したミューテーション（自己申告）
        "self_mutation": {"n": len(mutation), "killed": sum(1 for m in mutation if m.get("killed") is True)},
        # B: オーケストレーター側の道具で入れたミューテーション（implementer は選べない）
        "independent_mutation": {k: independent.get(k) for k in ("tool", "total", "killed", "survived", "score")
                                 if k in independent} or None,
        "unit_tests": {k: unit.get(k) for k in ("passed", "failed", "skipped") if k in unit},
        "lint_exit_code": lint.get("exit_code"),
        "integration_cause": integration.get("cause"),
        # 1 件が 2 秒を超えるテスト（本番の待ちを実時間で待っている。test/slow-real-wait）
        "slow_tests": len(as_list(integration.get("slow_tests"))),
        "review_responses": {a: sum(1 for x in responses if x.get("action") == a) for a in ("fixed", "not_fixed")},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("group")
    ap.add_argument("--root", type=Path, default=Path.cwd())
    args = ap.parse_args()
    if not args.group.isdigit():
        sys.exit(f"グループ番号は整数で指定してください: {args.group}")

    process = args.root / "doc/process"
    state = load_json(process / "state.json")
    if state is None:
        sys.exit(f"state.json が読めません: {process / 'state.json'}")
    start = run_start(state)
    if start is None:
        sys.exit("run の開始時刻が分かりません（harness.started_at も stage_history も無い）")
    run = start.isoformat()

    ledger = args.root / LEDGER
    seen = set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add((e.get("run"), e.get("source")))

    rows = []
    for kind, pattern in (("reviews", REVIEW_RE), ("results", RESULT_RE)):
        d = process / kind
        if not d.is_dir():
            continue
        for p in sorted(d.glob(f"group-{args.group}-*.json")):
            m = pattern.match(p.name)
            if not m or m.group("g") != args.group:
                continue
            # 前の run が残した同じ番号のファイルは数えない
            if datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) < start:
                continue
            source = f"{kind}/{p.name}"
            if (run, source) in seen:
                continue
            data = load_json(p)
            if not isinstance(data, dict):
                print(f"読めないので飛ばす: {source}", file=sys.stderr)
                continue
            row = {
                "run": run,
                "kind": state.get("kind"),
                "source": source,
                "group": args.group,
                "part": int(m.group("p")) if m.group("p") else None,
                "role": m.group("role"),
                "team": m.group("team"),
            }
            if kind == "reviews":
                row["round"] = int(m.group("r"))
                row.update(review_row(data))
            else:
                row.update(result_row(data))
            row["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            rows.append(row)

    if rows:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    reviews = sum(1 for r in rows if r["type"] == "review")
    print(f"台帳: グループ {args.group} のレビュー {reviews} 件・完了 JSON {len(rows) - reviews} 件を追記（{LEDGER}）")


if __name__ == "__main__":
    main()
