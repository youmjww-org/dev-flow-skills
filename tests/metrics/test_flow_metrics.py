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


def write_agent(subdir, agent_id, meta, timestamps, out_tokens=100):
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))
    lines = [json.dumps({"type": "user", "timestamp": timestamps[0]})]
    lines += [json.dumps({"type": "assistant", "timestamp": t, "message": {"usage": {"output_tokens": out_tokens}}})
              for t in timestamps[1:]]
    (subdir / f"agent-{agent_id}.jsonl").write_text("\n".join(lines) + "\n")


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
        old = reviews / "group-9-qa-app-r4.json"  # 前の run のファイルは数えない
        old.write_text(json.dumps({"status": "approved"}))
        os.utime(old, (0, 0))
        for p in reviews.glob("group-1-*"):
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
                    ["2026-10-09T09:28:00Z", "2026-10-09T09:31:00Z"])
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
    check("subagents", s["subagents"], 4)
    check("max review rounds", s["max_review_rounds"], 2)
    roles = {x["role"]: x for x in r["roles"]}
    check("roles", sorted(roles), ["implementer-dev", "implementer-qa", "reviewer", "stage"])
    check("dev minutes", roles["implementer-dev"]["total_minutes"], 10.0)
    groups = {g["group"]: g for g in r["groups"]}
    check("groups", sorted(groups), ["1", "1-p2"])
    check("group 1 reviewer", groups["1"].get("reviewer"), 3.0)
    check("reviews", [(x["review"], x["rounds"], x["engines"], x["last_status"]) for x in r["reviews"]],
          [("group-1-dev-app", 2, ["claude", "codex"], "approved")])
    check("markdown header", md.splitlines()[0], "# dev-flow run の集計（kind=change / profile=cost / reviewer=auto）")

    if failures:
        print("\n".join(failures))
        sys.exit(1)
    print("flow-metrics: OK")


if __name__ == "__main__":
    main()
