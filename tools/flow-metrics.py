#!/usr/bin/env python3
"""dev-flow の 1 run の所要時間を、ステージ・役割・グループごとに集計する。

使い方:
    python3 tools/flow-metrics.py <プロジェクトのディレクトリ> [--json] [--claude-dir ~/.claude]
    python3 tools/flow-metrics.py --ledger <プロジェクトのディレクトリ | review-ledger.jsonl>... [--json]

読むもの（どれも読み取りだけ）:
- <project>/doc/process/state.json の harness.stage_history … ステージの時間と人間待ち
- ~/.claude/projects/<エンコードしたパス>/**/subagents/*.jsonl と *.meta.json … サブエージェントの時間とトークン
- <project>/doc/process/reviews/*.json … レビューの往復回数とエンジン、spec の公式ドキュメント照合で見つかった食い違いの件数
- transcript の assistant メッセージの model … 実際に使われたモデル ID（"opus" のような別名ではなく claude-opus-5-5 など）

--ledger は、implementation STEP H が残す doc/process/metrics/review-ledger.jsonl（git で追跡する）を
run をまたいで集計する。レビューのエンジン別の差し戻し率、重大な指摘の系統、往復回数、
implementer の自己申告（ミューテーション）とレビュアーの指摘の食い違いを出す。

state.json は run ごとに stage_history を作り直すので、集計できるのは最後の run だけ。
スキルを変えた前後で比べるときは、run が終わるたびにこのスクリプトの --json を保存しておく。

プロンプトやツールの引数は出力しない（名前・時間・件数だけ）。
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def parse_ts(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def encode_project_path(path):
    # Claude Code は英数字以外を "-" にしたディレクトリ名で transcript を置く
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def minutes(seconds):
    return round(seconds / 60, 1)


# 名前（無ければ description）から役割を決める。上から順に最初に当たったもの
ROLE_PATTERNS = [
    ("stage", r"^stage-"),
    ("fact-checker", r"fact-check"),
    ("reviewer", r"review|レビュ"),
    ("implementer-dev", r"dev-implementer|implementer-dev|dev 実装|Dev 実装"),
    ("implementer-qa", r"qa-implementer|implementer-qa|qa 実装|QA 実装"),
    ("writer", r"writer|生成|作成"),
    ("test-runner", r"test-runner|テスト実行"),
    ("checker", r"check|verif|impact|整合|準拠"),
]

GROUP_RE = re.compile(r"group-?(\d+)(?:-p(\d+))?|G(\d+)\b", re.IGNORECASE)


def classify(name, description):
    text = f"{name or ''} {description or ''}"
    for role, pat in ROLE_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return role
    return "other"


def group_of(name, description):
    m = GROUP_RE.search(f"{name or ''} {description or ''}")
    if not m:
        return None
    n = m.group(1) or m.group(3)
    return f"{n}-p{m.group(2)}" if m.group(2) else n


def read_agent(jsonl, meta_path):
    first = last = None
    out_tokens = 0
    model_ids = set()
    with open(jsonl, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = parse_ts(e.get("timestamp"))
            if ts:
                first = first or ts
                last = ts
            if e.get("type") == "assistant":
                msg = e.get("message") or {}
                usage = msg.get("usage") or {}
                out_tokens += usage.get("output_tokens", 0)
                if is_model_id(msg.get("model")):
                    model_ids.add(msg["model"])
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "name": meta.get("name"),
        "description": meta.get("description"),
        "agent_type": meta.get("agentType"),
        "model": meta.get("model"),
        "model_ids": model_ids,
        "start": first,
        "end": last,
        "seconds": (last - first).total_seconds() if first and last else 0,
        "output_tokens": out_tokens,
    }


def is_model_id(m):
    # API エラーなどで入る "<synthetic>" は除く
    return isinstance(m, str) and m and not m.startswith("<")


def main_session_models(transcript_dir, start, end):
    """メインの会話（オーケストレーター）で run の間に使われたモデル ID と、その件数"""
    counts = defaultdict(int)
    for jsonl in transcript_dir.glob("*.jsonl"):
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                if '"assistant"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") != "assistant":
                    continue
                ts = parse_ts(e.get("timestamp"))
                model = (e.get("message") or {}).get("model")
                if ts and start <= ts <= end and is_model_id(model):
                    counts[model] += 1
    return dict(sorted(counts.items()))


def collect_agents(transcript_dir, start, end):
    agents = []
    for jsonl in transcript_dir.glob("*/subagents/*.jsonl"):
        a = read_agent(jsonl, jsonl.with_suffix(".meta.json"))
        if not a["start"] or a["start"] < start or a["start"] > end:
            continue
        a["role"] = classify(a["name"] or a["agent_type"], a["description"])
        a["group"] = group_of(a["name"], a["description"])
        agents.append(a)
    return agents


def stage_summary(history):
    stages = defaultdict(lambda: {"runs": 0, "seconds": 0})
    human_waits = []
    for i, h in enumerate(history):
        s = stages[h["stage"]]
        s["runs"] += 1
        started, completed = parse_ts(h.get("started_at")), parse_ts(h.get("completed_at"))
        if started and completed:
            s["seconds"] += (completed - started).total_seconds()
        # outcome が「人間待ち」のステージ実行で止まったとみなし、次の実行までの空きを待ち時間にする
        # （空きの長さだけで判定すると、すぐ答えたときに数えられない。AskUserQuestion で同じターン内に答えた分は含まない）
        if completed and i + 1 < len(history) and "人間待ち" in (h.get("outcome") or ""):
            nxt = parse_ts(history[i + 1].get("started_at"))
            if nxt:
                human_waits.append({"after": h["stage"], "seconds": max(0, (nxt - completed).total_seconds())})
    return stages, human_waits


def review_rounds(reviews_dir, start, end):
    """reviews/*.json の名前から往復回数を数える。例 group-2-dev-app-r3.json → group 2 / dev / 3 回。
    前の run のファイルも残っているので、更新時刻が run の間（終わりから 1 時間の余裕を見る）のものだけ数える"""
    rounds = defaultdict(lambda: {"rounds": 0, "engines": set(), "last_status": None})
    if not reviews_dir.is_dir():
        return rounds
    pat = re.compile(r"^(?P<key>.+?)-r(?P<n>\d+)\.json$")
    for p in sorted(reviews_dir.glob("*.json")):
        m = pat.match(p.name)
        if not m or m.group("key").endswith("-fact-checker"):  # 照合は fact_checks で数える
            continue
        mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
        if mtime < start or (mtime - end).total_seconds() > 3600:
            continue
        r = rounds[m.group("key")]
        n = int(m.group("n"))
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        # engine にフォールバックの理由が続くことがあるので先頭の語だけ使う。
        # 記録の無い古いファイルは Claude と決めつけず unknown にする
        engine = str(data.get("engine") or "unknown").split()[0]
        r["engines"].add(engine)
        if n >= r["rounds"]:
            r["rounds"] = n
            r["last_status"] = data.get("status")
    return rounds


def fact_checks(reviews_dir, start, end):
    """spec-*-fact-checker-rN.json から、公式ドキュメントとの照合で見つかった食い違いを数える。
    1 回目（r1）の指摘件数が「writer が最初に書いた誤り」の数で、導入の効果を測る指標にする"""
    found = {}
    if not reviews_dir.is_dir():
        return found
    pat = re.compile(r"^spec-(?P<name>.+-fact-checker)-r(?P<n>\d+)\.json$")
    for p in sorted(reviews_dir.glob("spec-*-fact-checker-r*.json")):
        m = pat.match(p.name)
        if not m:
            continue
        mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
        if mtime < start or (mtime - end).total_seconds() > 3600:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        f = found.setdefault(m.group("name"), {"first_issues": 0, "checked": 0, "unverified": 0, "rounds": 0})
        n = int(m.group("n"))
        if n == 1:
            f["first_issues"] = len(data.get("issues") or [])
            f["checked"] = data.get("checked") or 0
            f["unverified"] = data.get("unverified") or 0
        f["rounds"] = max(f["rounds"], n)
    return found


def build_report(project, claude_dir):
    state_path = project / "doc/process/state.json"
    if not state_path.exists():
        sys.exit(f"state.json がありません: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    history = (state.get("harness") or {}).get("stage_history") or []
    if not history:
        sys.exit("harness.stage_history が空です（run がまだ始まっていない）")

    start = parse_ts(history[0]["started_at"])
    end = max(parse_ts(h.get("completed_at") or h["started_at"]) for h in history)
    stages, human_waits = stage_summary(history)

    transcript_dir = claude_dir / "projects" / encode_project_path(project.resolve())
    agents = collect_agents(transcript_dir, start, end) if transcript_dir.is_dir() else []
    main_models = main_session_models(transcript_dir, start, end) if transcript_dir.is_dir() else {}

    roles = defaultdict(lambda: {"count": 0, "seconds": 0, "max_seconds": 0, "output_tokens": 0, "models": set()})
    groups = defaultdict(lambda: defaultdict(float))
    for a in agents:
        r = roles[a["role"]]
        r["count"] += 1
        r["seconds"] += a["seconds"]
        r["max_seconds"] = max(r["max_seconds"], a["seconds"])
        r["output_tokens"] += a["output_tokens"]
        # 実際のモデル ID を優先し、transcript に無ければ起動時に渡した別名を使う
        if a["model_ids"]:
            r["models"].update(a["model_ids"])
        elif a["model"]:
            r["models"].add(a["model"])
        if a["group"] and a["role"] != "stage":
            groups[a["group"]][a["role"]] = max(groups[a["group"]][a["role"]], a["seconds"])

    reviews = review_rounds(project / "doc/process/reviews", start, end)
    facts = fact_checks(project / "doc/process/reviews", start, end)

    agent_seconds = sum(s["seconds"] for s in stages.values())
    wait_seconds = sum(w["seconds"] for w in human_waits)
    return {
        "project": str(project),
        "kind": state.get("kind"),
        "profile": state.get("profile", "quality"),
        "reviewer_engine": state.get("reviewer_engine", "auto"),
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "summary": {
            "wall_minutes": minutes((end - start).total_seconds()),
            "agent_minutes": minutes(agent_seconds),
            "human_wait_minutes": minutes(wait_seconds),
            "human_stops": len(human_waits),
            "subagents": len(agents),
            "max_review_rounds": max((r["rounds"] for r in reviews.values()), default=0),
            "fact_check_issues": sum(f["first_issues"] for f in facts.values()),
        },
        "main_models": main_models,
        "stages": [
            {"stage": k, "runs": v["runs"], "minutes": minutes(v["seconds"])} for k, v in stages.items()
        ],
        "human_waits": [{"after": w["after"], "minutes": minutes(w["seconds"])} for w in human_waits],
        "roles": [
            {
                "role": k,
                "count": v["count"],
                "total_minutes": minutes(v["seconds"]),
                "max_minutes": minutes(v["max_seconds"]),
                "output_ktokens": round(v["output_tokens"] / 1000, 1),
                "models": sorted(v["models"]),
            }
            for k, v in sorted(roles.items(), key=lambda kv: -kv[1]["seconds"])
        ],
        "groups": [
            {"group": g, **{role: minutes(sec) for role, sec in sorted(v.items())}}
            for g, v in sorted(groups.items(), key=lambda kv: [int(x) if x.isdigit() else x for x in re.split(r"-p", kv[0])])
        ],
        "reviews": [
            {"review": k, "rounds": v["rounds"], "engines": sorted(v["engines"]), "last_status": v["last_status"]}
            for k, v in sorted(reviews.items())
        ],
        "fact_checks": [{"checker": k, **v} for k, v in sorted(facts.items())],
    }


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def render_markdown(r):
    s = r["summary"]
    out = [
        f"# dev-flow run の集計（kind={r['kind']} / profile={r['profile']} / reviewer={r['reviewer_engine']}）",
        "",
        f"{r['started_at']} 〜 {r['ended_at']}",
        "",
        table(
            ["経過", "エージェント作業", "人間待ち", "人間で止まった回数", "サブエージェント数", "最大レビュー往復"],
            [[f"{s['wall_minutes']} 分", f"{s['agent_minutes']} 分", f"{s['human_wait_minutes']} 分",
              s["human_stops"], s["subagents"], s["max_review_rounds"]]],
        ),
        "",
        "メインの会話のモデル: " + (", ".join(f"{k}（{v}）" for k, v in r["main_models"].items()) or "不明"),
        "",
        "## ステージ",
        "",
        table(["stage", "実行回数", "分"], [[x["stage"], x["runs"], x["minutes"]] for x in r["stages"]]),
        "",
        "## 役割",
        "",
        table(
            ["role", "数", "合計 分", "最長 分", "出力 ktok", "model"],
            [[x["role"], x["count"], x["total_minutes"], x["max_minutes"], x["output_ktokens"], ",".join(x["models"])]
             for x in r["roles"]],
        ),
    ]
    if r["groups"]:
        roles = sorted({k for g in r["groups"] for k in g if k != "group"})
        out += ["", "## グループ（役割ごとの最長 分）", "",
                table(["group", *roles], [[g["group"], *[g.get(k, "") for k in roles]] for g in r["groups"]])]
    if r["reviews"]:
        out += ["", "## レビュー", "",
                table(["review", "往復", "engine", "最終 status"],
                      [[x["review"], x["rounds"], ",".join(x["engines"]), x["last_status"]] for x in r["reviews"]])]
    if r["fact_checks"]:
        out += ["", "## 公式ドキュメントとの照合（spec）", "",
                table(["checker", "照合した主張", "1 回目の食い違い", "未確認", "往復"],
                      [[x["checker"], x["checked"], x["first_issues"], x["unverified"], x["rounds"]] for x in r["fact_checks"]])]
    return "\n".join(out)


LEDGER = "doc/process/metrics/review-ledger.jsonl"
# implementer の自己申告（テスト全件成功・ミューテーション全件 killed）をすり抜けた「テストの弱さ」の指摘
WEAK_TEST_RULES = ("test/tc-coverage", "test/expected-from-impl", "test/mutation-checked")


def load_ledger(paths):
    rows = []
    for p in paths:
        f = p / LEDGER if p.is_dir() else p
        if not f.exists():
            sys.exit(f"台帳がありません: {f}")
        project = str(p if p.is_dir() else p.parents[3] if len(p.parents) > 3 else p.parent)
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            e["project"] = project
            rows.append(e)
    return rows


def pct(n, d):
    return round(100 * n / d) if d else None


def build_ledger_report(rows):
    reviews = [r for r in rows if r.get("type") == "review"]
    results = [r for r in rows if r.get("type") == "result"]

    engines = defaultdict(lambda: {"reviews": 0, "r1": 0, "r1_changes": 0, "serious": 0, "serious_outside": 0, "verified": 0})
    prefixes = defaultdict(lambda: defaultdict(int))
    rules = defaultdict(int)
    max_round = {}
    weak_tests = 0
    mutant_verdicts = defaultdict(int)
    for r in reviews:
        if r.get("role") == "mut":
            # 生き残った変異の仕分けは通常のレビューと性質が違うので、エンジン・往復の集計には入れない
            for k, v in (r.get("mutant_verdicts") or {}).items():
                mutant_verdicts[k] += v or 0
            for x in r.get("serious") or []:
                rule = str(x.get("rule") or "none")
                prefixes[rule.split("/")[0]][x.get("severity")] += 1
                rules[rule] += 1
            continue
        e = engines[r.get("engine") or "unknown"]
        e["reviews"] += 1
        serious = r.get("serious") or []
        e["serious"] += len(serious)
        e["serious_outside"] += sum(1 for x in serious if str(x.get("rule", "")).startswith("review/"))
        e["verified"] += sum(1 for x in serious if x.get("verified"))
        if r.get("round") == 1:
            e["r1"] += 1
            e["r1_changes"] += r.get("status") == "changes_requested"
        for x in serious:
            rule = str(x.get("rule") or "none")
            prefixes[rule.split("/")[0]][x.get("severity")] += 1
            rules[rule] += 1
            weak_tests += rule in WEAK_TEST_RULES
        key = (r["project"], r.get("run"), r.get("group"), r.get("part"), r.get("role"), r.get("team"))
        max_round[key] = max(max_round.get(key, 0), r.get("round") or 0)

    rounds = defaultdict(int)
    for n in max_round.values():
        rounds[n] += 1

    self_n = sum((r.get("self_mutation") or {}).get("n", 0) for r in results)
    self_killed = sum((r.get("self_mutation") or {}).get("killed", 0) for r in results)
    indep = [r["independent_mutation"] for r in results if r.get("independent_mutation")]
    fixed = [r["self_review_fixed"] for r in results if isinstance(r.get("self_review_fixed"), int)]

    return {
        "projects": sorted({r["project"] for r in rows}),
        "runs": len({(r["project"], r.get("run")) for r in rows}),
        "reviews": len(reviews),
        "results": len(results),
        "engines": [
            {"engine": k, **v, "r1_changes_pct": pct(v["r1_changes"], v["r1"]),
             "serious_per_review": round(v["serious"] / v["reviews"], 2) if v["reviews"] else None}
            for k, v in sorted(engines.items())
        ],
        "rounds": [{"max_round": k, "count": v} for k, v in sorted(rounds.items())],
        "serious_by_prefix": [
            {"prefix": k, **dict(sorted(v.items())), "total": sum(v.values())}
            for k, v in sorted(prefixes.items(), key=lambda kv: -sum(kv[1].values()))
        ],
        "top_rules": [{"rule": k, "count": v} for k, v in sorted(rules.items(), key=lambda kv: (-kv[1], kv[0]))[:15]],
        "self_reported": {
            "mutation_total": self_n,
            "mutation_killed": self_killed,
            "self_review_fixed_avg": round(sum(fixed) / len(fixed), 1) if fixed else None,
            "weak_test_findings": weak_tests,
        },
        "independent_mutation": {
            "groups": len(indep),
            "total": sum(x.get("total") or 0 for x in indep),
            "survived": sum(x.get("survived") or 0 for x in indep),
            "verdicts": dict(sorted(mutant_verdicts.items())),
        },
    }


def render_ledger_markdown(r):
    sr, im = r["self_reported"], r["independent_mutation"]
    out = [
        f"# dev-flow 台帳の集計（{r['runs']} run・レビュー {r['reviews']} 件・完了 JSON {r['results']} 件）",
        "",
        "## レビューのエンジン",
        "",
        table(["engine", "レビュー", "1 回目", "1 回目の差し戻し率", "重大/件", "うち規約外", "うち実行で確認"],
              [[x["engine"], x["reviews"], x["r1"], f"{x['r1_changes_pct']}%" if x["r1_changes_pct"] is not None else "-",
                x["serious_per_review"], x["serious_outside"], x["verified"]] for x in r["engines"]]),
        "",
        "エンジンごとの差は、担当したグループ・時期が違えばそのまま比べられない。",
        "",
        "## 往復回数（グループ・役割ごとの最終回）",
        "",
        table(["最終回", "数"], [[x["max_round"], x["count"]] for x in r["rounds"]]),
        "",
        "## 重大な指摘（blocker / major）の系統",
        "",
        table(["系統", "blocker", "major", "計"],
              [[x["prefix"], x.get("blocker", 0), x.get("major", 0), x["total"]] for x in r["serious_by_prefix"]]),
        "",
        table(["rule", "件数"], [[x["rule"], x["count"]] for x in r["top_rules"]]),
        "",
        "## 自己申告とレビュアーの指摘",
        "",
        f"- implementer が自分で試したミューテーション: {sr['mutation_killed']} / {sr['mutation_total']} 件が killed",
        f"- それでもレビュアーが見つけたテストの弱さ（{' ・ '.join(WEAK_TEST_RULES)}）: {sr['weak_test_findings']} 件",
        f"- セルフレビューで直した件数の平均: {sr['self_review_fixed_avg']}",
    ]
    if im["groups"]:
        out.append(f"- 道具が選んだミューテーション（mutate-diff）: {im['groups']} 件で計 {im['total']} 変異、生き残り {im['survived']}")
        if im["verdicts"]:
            out.append("- 生き残りの仕分け: " + "・".join(f"{k} {v}" for k, v in im["verdicts"].items()))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", type=Path, metavar="project",
                    help="プロジェクトのディレクトリ（--ledger では複数可。台帳のファイルを直接渡してもよい）")
    ap.add_argument("--ledger", action="store_true", help="review-ledger.jsonl を run をまたいで集計する")
    ap.add_argument("--run", help="--ledger で、この開始時刻（state.json の harness.started_at）の run だけを集計する")
    ap.add_argument("--json", action="store_true", help="JSON で出す（run ごとに保存して比べる用）")
    ap.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude")
    args = ap.parse_args()
    if args.ledger:
        rows = load_ledger(args.paths)
        if args.run:
            want = parse_ts(args.run)
            rows = [r for r in rows if parse_ts(r.get("run")) == want]
            if not rows:
                sys.exit(f"台帳にこの run の行がありません: {args.run}")
        report = build_ledger_report(rows)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_ledger_markdown(report))
        return
    if len(args.paths) != 1:
        ap.error("--ledger なしではプロジェクトを 1 つだけ指定してください")
    report = build_report(args.paths[0], args.claude_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))


if __name__ == "__main__":
    main()
