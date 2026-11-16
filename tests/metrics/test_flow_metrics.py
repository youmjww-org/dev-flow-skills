#!/usr/bin/env python3
"""tools/flow-metrics.py のテスト。一時ディレクトリに state.json・transcript・reviews を作って集計結果を確かめる。"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/flow-metrics.py"


def write_agent(subdir, agent_id, meta, timestamps, out_tokens=100, model_id=None):
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))
    lines = [json.dumps({"type": "user", "timestamp": timestamps[0]})]
    message = {"usage": {"output_tokens": out_tokens}}
    if model_id:
        message["model"] = model_id
    lines += [json.dumps({"type": "assistant", "timestamp": t, "message": message}) for t in timestamps[1:]]
    (subdir / f"agent-{agent_id}.jsonl").write_text("\n".join(lines) + "\n")


def check_ledger(check):
    """--ledger: 台帳をプロジェクト 2 つ分まとめて集計する"""
    rows_a = [
        {"type": "review", "run": "r1", "group": "1", "part": None, "role": "dev", "team": "app", "round": 1,
         "engine": "codex", "status": "changes_requested",
         "serious": [{"severity": "blocker", "rule": "review/bypass", "verified": True},
                     {"severity": "major", "rule": "test/tc-coverage", "verified": False}]},
        {"type": "review", "run": "r1", "group": "1", "part": None, "role": "dev", "team": "app", "round": 2,
         "engine": "codex", "status": "approved", "serious": []},
        {"type": "result", "run": "r1", "group": "1", "role": "dev", "team": "app", "self_review_fixed": 2,
         "self_mutation": {"n": 3, "killed": 3}, "independent_mutation": {"tool": "mutmut", "total": 10, "survived": 4}},
    ]
    rows_b = [
        {"type": "review", "run": "r9", "group": "1", "part": None, "role": "qa", "team": "app", "round": 1,
         "engine": "claude", "status": "approved", "serious": []},
        {"type": "result", "run": "r9", "group": "1", "role": "qa", "team": "app", "self_review_fixed": 4,
         "self_mutation": {"n": 1, "killed": 1}},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name, rows in (("a", rows_a), ("b", rows_b)):
            f = tmp / name / "doc/process/metrics/review-ledger.jsonl"
            f.parent.mkdir(parents=True)
            f.write_text("\n".join(json.dumps(x) for x in rows) + "\nnot json\n")
        args = [sys.executable, str(SCRIPT), "--ledger", str(tmp / "a"), str(tmp / "b" / "doc/process/metrics/review-ledger.jsonl")]
        r = json.loads(subprocess.run(args + ["--json"], capture_output=True, text=True, check=True).stdout)
        md = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    check("ledger runs", (r["runs"], r["reviews"], r["results"]), (2, 3, 2))
    check("ledger projects", len(r["projects"]), 2)
    eng = {x["engine"]: x for x in r["engines"]}
    check("ledger codex", (eng["codex"]["r1"], eng["codex"]["r1_changes_pct"], eng["codex"]["serious_per_review"],
                           eng["codex"]["serious_outside"], eng["codex"]["verified"]), (1, 100, 1.0, 1, 1))
    check("ledger claude", (eng["claude"]["r1_changes_pct"], eng["claude"]["serious"]), (0, 0))
    check("ledger rounds", r["rounds"], [{"max_round": 1, "count": 1}, {"max_round": 2, "count": 1}])
    check("ledger prefixes", [(x["prefix"], x["total"]) for x in r["serious_by_prefix"]], [("review", 1), ("test", 1)])
    check("ledger self reported", r["self_reported"],
          {"mutation_total": 4, "mutation_killed": 4, "self_review_fixed_avg": 3.0, "weak_test_findings": 1})
    check("ledger independent", r["independent_mutation"], {"groups": 1, "total": 10, "survived": 4})
    check("ledger markdown", "| codex | 2 | 1 | 100% | 1.0 | 1 | 1 |" in md, True)
    check("ledger markdown header", md.splitlines()[0], "# dev-flow 台帳の集計（2 run・レビュー 3 件・完了 JSON 2 件）")
    # --run で 1 run に絞る（表記の違う同じ時刻も一致させる）
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "doc/process/metrics/review-ledger.jsonl"
        f.parent.mkdir(parents=True)
        f.write_text("\n".join(json.dumps(x) for x in [
            {**rows_a[0], "run": "2026-11-01T00:00:00+00:00"}, {**rows_b[0], "run": "2026-11-02T00:00:00+00:00"}]))
        one = json.loads(subprocess.run([sys.executable, str(SCRIPT), "--ledger", tmp, "--run", "2026-11-01T00:00:00Z", "--json"],
                                        capture_output=True, text=True, check=True).stdout)
        none = subprocess.run([sys.executable, str(SCRIPT), "--ledger", tmp, "--run", "2026-12-01T00:00:00Z"],
                              capture_output=True, text=True)
    check("ledger --run", (one["runs"], one["reviews"]), (1, 1))
    check("ledger --run no rows", none.returncode != 0 and "この run の行がありません" in none.stderr, True)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        project = tmp / "proj"
        (project / "doc/process/reviews").mkdir(parents=True)
        (project / "doc/process/state.json").write_text(json.dumps({
            "kind": "change", "profile": "cost",
            "harness": {"stage_history": [
                {"stage": "requirements", "started_at": "2026-10-09T09:00:00Z", "completed_at": "2026-10-09T09:05:00Z", "outcome": "人間待ち"},
                # 09:05〜09:15 は人間待ち
                {"stage": "requirements", "started_at": "2026-10-09T09:15:00Z", "completed_at": "2026-10-09T09:16:00Z"},
                # 人間待ちでない実行の後の空きは数えない
                {"stage": "implementation", "started_at": "2026-10-09T09:17:10Z", "completed_at": "2026-10-09T09:36:10Z"},
            ]},
        }))
        reviews = project / "doc/process/reviews"
        (reviews / "group-1-dev-app-r1.json").write_text(json.dumps({"engine": "codex", "status": "changes_requested"}))
        (reviews / "group-1-dev-app-r2.json").write_text(json.dumps({"engine": "claude (codex usage limit)", "status": "approved"}))
        (reviews / "group-2-dev-app-r1.json").write_text(json.dumps({"status": "approved"}))  # engine の記録なし
        old = reviews / "group-9-qa-app-r4.json"  # 前の run のファイルは数えない
        old.write_text(json.dumps({"status": "approved"}))
        os.utime(old, (0, 0))
        (reviews / "spec-infra-fact-checker-r1.json").write_text(json.dumps({
            "status": "changes_requested", "checked": 9, "unverified": 2,
            "issues": [{"location": "INFRA-003", "problem": "p", "fix": "f", "source": "https://example.com"}] * 2}))
        (reviews / "spec-infra-fact-checker-r2.json").write_text(json.dumps({
            "status": "approved", "checked": 2, "unverified": 0, "issues": []}))
        for p in [*reviews.glob("group-1-*"), *reviews.glob("group-2-*"), *reviews.glob("spec-*")]:
            t = 1791536400  # 2026-10-09T09:40:00Z
            os.utime(p, (t, t))

        claude = tmp / "claude"
        tdir = claude / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(project.resolve())) / "sess" / "subagents"
        write_agent(tdir, "a1", {"agentType": "stage-implementation-agent", "name": "stage-implementation-agent"},
                    ["2026-10-09T09:16:10Z", "2026-10-09T09:36:10Z"])
        write_agent(tdir, "a2", {"agentType": "general-purpose", "name": "dev-implementer-app-group-1", "model": "sonnet"},
                    ["2026-10-09T09:17:00Z", "2026-10-09T09:27:00Z"])
        write_agent(tdir, "a3", {"agentType": "general-purpose", "name": "qa-implementer-app-group-1-p2", "model": "sonnet"},
                    ["2026-10-09T09:17:00Z", "2026-10-09T09:23:00Z"])
        write_agent(tdir, "a4", {"agentType": "general-purpose", "name": "reviewer-dev-app-group-1", "model": "opus"},
                    ["2026-10-09T09:28:00Z", "2026-10-09T09:31:00Z"], model_id="claude-opus-5-5")
        # メインの会話（オーケストレーター）。run の外の行と "<synthetic>" は数えない
        (tdir.parent.parent / "sess.jsonl").write_text("\n".join(json.dumps(x) for x in [
            {"type": "assistant", "timestamp": "2026-10-09T09:01:00Z", "message": {"model": "claude-opus-5-5"}},
            {"type": "assistant", "timestamp": "2026-10-09T09:20:00Z", "message": {"model": "claude-opus-4-8"}},
            {"type": "assistant", "timestamp": "2026-10-09T09:21:00Z", "message": {"model": "<synthetic>"}},
            {"type": "assistant", "timestamp": "2026-10-08T09:00:00Z", "message": {"model": "claude-haiku-4-5"}},
        ]) + "\n")
        write_agent(tdir, "a6", {"agentType": "general-purpose", "name": "infra-fact-checker", "model": "opus"},
                    ["2026-10-09T09:16:00Z", "2026-10-09T09:20:00Z"])
        # run の外で起動したエージェントは数えない
        write_agent(tdir, "a5", {"agentType": "general-purpose", "name": "test-spec-writer"},
                    ["2026-10-08T09:00:00Z", "2026-10-08T09:10:00Z"])

        out = subprocess.run([sys.executable, str(SCRIPT), str(project), "--json", "--claude-dir", str(claude)],
                             capture_output=True, text=True, check=True)
        r = json.loads(out.stdout)
        md = subprocess.run([sys.executable, str(SCRIPT), str(project), "--claude-dir", str(claude)],
                            capture_output=True, text=True, check=True).stdout

    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    s = r["summary"]
    check("wall", s["wall_minutes"], 36.2)
    check("agent", s["agent_minutes"], 25.0)
    check("human wait", s["human_wait_minutes"], 10.0)
    check("human stops", s["human_stops"], 1)
    check("subagents", s["subagents"], 5)
    check("max review rounds", s["max_review_rounds"], 2)
    roles = {x["role"]: x for x in r["roles"]}
    check("roles", sorted(roles), ["fact-checker", "implementer-dev", "implementer-qa", "reviewer", "stage"])
    check("dev minutes", roles["implementer-dev"]["total_minutes"], 10.0)
    groups = {g["group"]: g for g in r["groups"]}
    check("groups", sorted(groups), ["1", "1-p2"])
    check("group 1 reviewer", groups["1"].get("reviewer"), 3.0)
    check("reviews", [(x["review"], x["rounds"], x["engines"], x["last_status"]) for x in r["reviews"]],
          [("group-1-dev-app", 2, ["claude", "codex"], "approved"), ("group-2-dev-app", 1, ["unknown"], "approved")])
    check("reviewer model id", roles["reviewer"]["models"], ["claude-opus-5-5"])
    check("implementer alias fallback", roles["implementer-dev"]["models"], ["sonnet"])
    check("main models", r["main_models"], {"claude-opus-4-8": 1, "claude-opus-5-5": 1})
    check("markdown main models", "メインの会話のモデル: claude-opus-4-8（1）, claude-opus-5-5（1）" in md, True)
    check("fact check issues", s["fact_check_issues"], 2)
    check("fact checks", r["fact_checks"],
          [{"checker": "infra-fact-checker", "first_issues": 2, "checked": 9, "unverified": 2, "rounds": 2}])
    check("markdown fact checks", "| infra-fact-checker | 9 | 2 | 2 | 2 |" in md, True)
    check("markdown header", md.splitlines()[0], "# dev-flow run の集計（kind=change / profile=cost / reviewer=auto）")

    check_ledger(check)

    if failures:
        print("\n".join(failures))
        sys.exit(1)
    print("flow-metrics: OK")


if __name__ == "__main__":
    main()
