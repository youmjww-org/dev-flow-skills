#!/usr/bin/env python3
"""implementer・レビュアーに渡す「担当分の仕様」を切り出す。

使い方: group-context.py <N | N-pK> <dev|qa> <app|infra> [--root DIR] [--out PATH]

1. doc/process/task_checklist.md から「### グループ N (App|Infra)」の節を探し、
   その層（Dev / QA。QA の part K があれば part K）のタスクと節の前書きを取り出す
2. タスクの文面から ID（REQ / TC / API / INFRA / NFR。「REQ-127〜REQ-129」「TC-408〜427」の範囲も展開）と、
   「API 仕様書 12.17」「インフラ仕様書 3.2〜3.4」のような節番号を拾う
3. 要件定義書・テスト定義書・API 仕様書・インフラ仕様書・スペックキャッシュから、
   見出しにその ID・節番号を含む節（次の同じか上のレベルの見出しまで）だけを取り出す。
   テスト定義書は frontmatter の test_cases から該当 TC の項目も取り出す
4. doc/process/injection/group-{N}-{dev|qa}-{team}.md に書き、標準出力には 1 行の要約だけを出す

仕様の全文は implementer が必要なときに grep で読む。ここでは「まず読む範囲」を絞ることだけが目的なので、
拾えなかった節があっても失敗にはしない。
"""
import argparse, glob, os, re, sys

ID_RE = re.compile(r"\b(REQ|TC|API|INFRA|NFR)-(\d{2,4})(?:\s*[〜~～-]\s*(?:\1-)?(\d{2,4}))?\b")
DOCS = [  # (ラベル, glob, 節番号を拾う語, 見出しで照合する ID の種類)
    # 文書ごとに「その文書が定義する ID」だけで照合する。テスト定義書の章見出し「（REQ-127〜REQ-129）」のような
    # 参照で章を丸ごと拾うと、担当の TC 1 つのために 900 行の章が入る（2026-11 の試行で確認）
    ("要件定義書", "doc/requirements/*.md", ("要件定義書", "要件"), ("REQ", "NFR")),
    ("テスト定義書", "doc/test-spec/*.md", ("テスト定義書",), ("TC",)),
    ("API 仕様書", "doc/api-spec/*.md", ("API 仕様書", "API仕様書", "API "), ("API",)),
    ("インフラ仕様書", "doc/infra-spec/*.md", ("インフラ仕様書",), ("INFRA", "NFR")),
    ("スペックキャッシュ", "doc/internal/spec_cache.md", ("スペックキャッシュ", "spec_cache"), ("REQ", "NFR", "TC", "API", "INFRA")),
]
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def expand_ids(text):
    ids = set()
    for m in ID_RE.finditer(text):
        kind, a, b = m.group(1), m.group(2), m.group(3)
        width = len(a)
        if b and int(b) >= int(a) and int(b) - int(a) <= 200:
            for n in range(int(a), int(b) + 1):
                ids.add(f"{kind}-{n:0{width}d}")
        else:
            ids.add(f"{kind}-{a}")
    return ids


def section_numbers(text, words):
    """「API 仕様書 12.17〜12.19・12.4.2」のような参照から節番号を拾う。"""
    nums = set()
    for w in words:
        for m in re.finditer(re.escape(w) + r"\s*((?:\d+(?:\.\d+)+)(?:\s*[〜~～・、,]\s*\d+(?:\.\d+)+)*)", text):
            parts = re.split(r"\s*([〜~～・、,])\s*", m.group(1))
            prev = None; op = None
            for p in parts:
                if p in "〜~～・、,":
                    op = p; continue
                if op in ("〜", "~", "～") and prev and prev.rsplit(".", 1)[0] == p.rsplit(".", 1)[0]:
                    base, a = prev.rsplit(".", 1); b = p.rsplit(".", 1)[1]
                    for n in range(int(a), int(b) + 1):
                        nums.add(f"{base}.{n}")
                nums.add(p); prev = p; op = None
    return nums


def checklist_section(lines, n, role, team):
    """グループ N（part K なら QA の part K）・指定の層の節を返す。"""
    base, part = (n.split("-p") + [None])[:2] if "-p" in n else (n, None)
    team_label = {"app": "App", "infra": "Infra"}[team]
    role_label = {"dev": "Dev", "qa": "QA"}[role]
    start = None
    for i, l in enumerate(lines):
        m = re.match(r"^###\s+グループ\s+(\d+)\s*(?:\((\w+)\))?", l)
        if m and m.group(1) == base and (m.group(2) in (None, team_label, "Cross")):
            start = i; break
    if start is None:
        return None
    end = next((j for j in range(start + 1, len(lines)) if re.match(r"^#{1,3}\s", lines[j])), len(lines))
    sec = lines[start:end]
    out, keep = [], True
    for l in sec:
        m = re.match(r"^####\s+(Dev|QA)\s+タスク(.*)$", l)
        if m:
            keep = m.group(1) == role_label
            tm = re.search(r"\((App|Infra)\)", m.group(2))
            if tm and tm.group(1) != team_label:
                keep = False
            if keep and part and role == "qa":
                pm = re.search(r"part\s*(\d+)", m.group(2))
                keep = (pm is None and part == "1") or (pm is not None and pm.group(1) == part)
        if keep:
            out.append(l)
    return out


def pick_sections(lines, ids, nums):
    """見出しに ID・節番号を含む節の行範囲（重なりはまとめる）。コードブロック内の # は見出しとみなさない。"""
    heads, fence = [], False
    for i, l in enumerate(lines):
        if l.lstrip().startswith("```"):
            fence = not fence; continue
        m = None if fence else HEAD_RE.match(l)
        if m:
            heads.append((i, len(m.group(1)), m.group(2)))
    ranges = []
    for k, (i, lv, title) in enumerate(heads):
        hit = any(re.search(r"(?<![\w-])" + re.escape(x) + r"(?!\d)", title) for x in ids)
        if not hit and nums:
            lead = re.match(r"^(\d+(?:\.\d+)*)", title.strip())
            hit = bool(lead) and lead.group(1) in nums
        if hit:
            end = next((j for j, lv2, _ in heads[k + 1:] if lv2 <= lv), len(lines))
            ranges.append([i, end])
    ranges.sort(); merged = []
    for a, b in ranges:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def frontmatter_tcs(lines, ids):
    if not lines or lines[0].strip() != "---":
        return []
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return []
    out, cur = [], None
    for l in lines[1:end]:
        m = re.match(r"^\s*-\s+id:\s*(TC-\d+)", l)
        if m:
            cur = m.group(1) in ids
            if cur: out.append(l)
        elif cur and re.match(r"^\s{4,}\S", l):
            out.append(l)
        elif not l.startswith(" "):
            cur = None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("group"); ap.add_argument("role", choices=["dev", "qa"]); ap.add_argument("team", choices=["app", "infra"])
    ap.add_argument("--root", default="."); ap.add_argument("--out")
    a = ap.parse_args()
    root = a.root
    ck_path = os.path.join(root, "doc/process/task_checklist.md")
    if not os.path.exists(ck_path):
        print(f"NG group-context: {ck_path} がありません"); return 1
    sec = checklist_section(open(ck_path, encoding="utf-8").read().split("\n"), a.group, a.role, a.team)
    if sec is None:
        print(f"NG group-context: チェックリストに「### グループ {a.group.split('-p')[0]}」の節がありません"); return 1
    task_text = "\n".join(sec)
    ids = expand_ids(task_text)
    out_path = a.out or os.path.join(root, f"doc/process/injection/group-{a.group}-{a.role}-{a.team}.md")
    parts = [f"# グループ {a.group} {a.role.upper()} ({a.team}) の担当分の仕様（group-context.py が生成）",
             "",
             "チェックリストのタスクに出てくる ID・節番号の節だけを各文書から切り出したもの。ここに無い節が要るときは、"
             "元の文書を `grep -n` で探して必要な行だけ読む（全文を Read しない）。",
             "", f"拾った ID: {', '.join(sorted(ids)) or '（なし）'}", "", "## チェックリスト（このグループ・この層）", ""] + sec
    total_src = picked = 0; n_sec = 0
    for label, pat, words, kinds in DOCS:
        nums = section_numbers(task_text, words)
        doc_ids = {x for x in ids if x.split("-")[0] in kinds}
        for path in sorted(glob.glob(os.path.join(root, pat))):
            lines = open(path, encoding="utf-8").read().split("\n")
            total_src += len(lines)
            rel = os.path.relpath(path, root)
            chunks = []
            if label == "テスト定義書":
                fm = frontmatter_tcs(lines, doc_ids)
                if fm:
                    chunks.append(f"### {rel}: frontmatter の test_cases（該当分）\n\n```yaml\n" + "\n".join(fm) + "\n```")
                    picked += len(fm)
            for s, e in pick_sections(lines, doc_ids, nums):
                chunks.append(f"### {rel}:{s + 1}-{e}\n\n" + "\n".join(lines[s:e]).rstrip())
                picked += e - s; n_sec += 1
            if chunks:
                parts += ["", f"## {label}（{rel}）", ""] + chunks
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w", encoding="utf-8").write("\n".join(parts).rstrip() + "\n")
    ratio = picked / total_src if total_src else 0
    warn = ""
    if n_sec == 0:
        warn = " WARN 仕様の節を 1 つも拾えなかった（タスクに ID・節番号が無い可能性）"
    elif ratio > 0.5:
        warn = " WARN 全文の 50% を超えた（絞れていない）"
    print(f"OK group-context: {os.path.relpath(out_path, root)} ID {len(ids)} 件・節 {n_sec} 個・{picked} 行（仕様の全文 {total_src} 行の {ratio:.0%}）{warn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
