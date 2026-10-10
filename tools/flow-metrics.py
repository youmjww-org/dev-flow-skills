#!/usr/bin/env python3
"""dev-flow の 1 run の所要時間を、ステージ・役割・グループごとに集計する。

使い方:
    python3 tools/flow-metrics.py <プロジェクトのディレクトリ> [--json] [--claude-dir ~/.claude]

読むもの（どれも読み取りだけ）:
- <project>/doc/process/state.json の harness.stage_history … ステージの時間と人間待ち
- ~/.claude/projects/<エンコードしたパス>/**/subagents/*.jsonl と *.meta.json … サブエージェントの時間とトークン
- <project>/doc/process/reviews/*.json … レビューの往復回数とエンジン

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
                usage = (e.get("message") or {}).get("usage") or {}
                out_tokens += usage.get("output_tokens", 0)
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
        "start": first,
        "end": last,
        "seconds": (last - first).total_seconds() if first and last else 0,
        "output_tokens": out_tokens,
    }


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
        if not m:
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
        # engine にフォールバックの理由が続くことがあるので先頭の語だけ使う
        engine = str(data.get("engine") or "claude").split()[0]
        r["engines"].add(engine)
        if n >= r["rounds"]:
            r["rounds"] = n
            r["last_status"] = data.get("status")
    return rounds


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

    roles = defaultdict(lambda: {"count": 0, "seconds": 0, "max_seconds": 0, "output_tokens": 0, "models": set()})
    groups = defaultdict(lambda: defaultdict(float))
    for a in agents:
        r = roles[a["role"]]
        r["count"] += 1
        r["seconds"] += a["seconds"]
        r["max_seconds"] = max(r["max_seconds"], a["seconds"])
        r["output_tokens"] += a["output_tokens"]
        if a["model"]:
            r["models"].add(a["model"])
        if a["group"] and a["role"] != "stage":
            groups[a["group"]][a["role"]] = max(groups[a["group"]][a["role"]], a["seconds"])

    reviews = review_rounds(project / "doc/process/reviews", start, end)

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
        },
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
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--json", action="store_true", help="JSON で出す（run ごとに保存して比べる用）")
    ap.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude")
    args = ap.parse_args()
    report = build_report(args.project, args.claude_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))


if __name__ == "__main__":
    main()
