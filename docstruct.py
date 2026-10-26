"""docstruct —— 给长文档做结构体检。

设计原则：
1. 只检查能机械判定的事：层级、编号、空节、占位符。
2. 每条问题给行号，方便直接跳过去改。
3. 不评价措辞好坏，也不评价观点对错。

仅依赖 Python 标准库。

用法:
    python docstruct.py --help
    python docstruct.py --file README.md
    python docstruct.py --file README.md --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 标题：# ~###### ，允许尾随的 #
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

# 编号体系：1. / 1.1 / 1.1.1 / 一、 / 第一章 / 1) 等
NUM_PATTERNS = [
    re.compile(r"^(\d+(?:\.\d+)*)\s+"),            # 1. 1.1 1.1.1
    re.compile(r"^(\d+(?:\.\d+)*)[、.]\s*"),          # 1、1.1、
    re.compile(r"^([一二三四五六七八九十百]+)、"),   # 一、
    re.compile(r"^第([0-9一二三四五六七八九十百]+)[章節节]"),  # 第一章
]

# 占位符：和开源工厂的验收口径保持一致
PLACEHOLDER_PATTERNS = [
    (re.compile(r"<\s*待补充\s*>"), "待补充占位符"),
    (re.compile(r"<\s*待核验\s*>"), "待核验占位符"),
    (re.compile(r"<\s*待填写\s*>"), "待填写占位符"),
    (re.compile(r"<\s*此处.*?\">"), "此处…占位符"),
    (re.compile(r"\bTODO\b"), "TODO"),
    (re.compile(r"\bFIXME\b"), "FIXME"),
    (re.compile(r"\bXXX\b"), "XXX"),
    (re.compile(r"待补充"), "待补充"),
    (re.compile(r"占位"), "占位"),
]

FENCE_RE = re.compile(r"^\s*(```|~~~)")

# 章节里「正文内容」的判定：标题到下一个同级或更高级标题之间
BODY_MIN_CHARS = 40


class Issue:
    """一条结构问题。"""

    def __init__(self, line: int, kind: str, message: str, detail: str = "") -> None:
        self.line = line
        self.kind = kind
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict:
        return {"line": self.line, "kind": self.kind,
                "message": self.message, "detail": self.detail}


def extract_headings(lines: list[str]) -> list[dict]:
    """从文本里抽出标题，跳过代码块内部。"""
    headings: list[dict] = []
    in_fence = False
    fence_marker = ""
    for number, raw in enumerate(lines, 1):
        if FENCE_RE.match(raw):
            marker = FENCE_RE.match(raw).group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(raw)
        if m:
            headings.append({
                "line": number,
                "level": len(m.group(1)),
                "title": m.group(2).strip(),
                "text": raw,
            })
    return headings


def detect_numbering(title: str) -> str | None:
    """从标题里识别编号前缀，返回编号本身；识别不到返回 None。"""
    for pattern in NUM_PATTERNS:
        m = pattern.match(title)
        if m:
            return m.group(1)
    return None


def check_heading_levels(headings: list[dict]) -> list[Issue]:
    """检查标题层级是否连续：不允许从 h2 直接跳到 h4。"""
    issues: list[Issue] = []
    for previous, current in zip(headings, headings[1:]):
        if current["level"] > previous["level"] + 1:
            issues.append(Issue(
                current["line"],
                "level_jump",
                f"标题层级从 h{previous['level']} 跳到 h{current['level']}",
                f"上一节「{previous['title']}」在第 {previous['line']} 行，"
                f"本节「{current['title']}」在第 {current['line']} 行；"
                f"若确为有意的分层，需人工确认",
            ))
    return issues


def check_numbering(headings: list[dict]) -> list[Issue]:
    """检查同编号体系内的编号连续性。"""
    issues: list[Issue] = []
    # 只在检测到编号的标题之间比较
    numbered = [(h, detect_numbering(h["title"])) for h in headings]
    numbered = [(h, num) for h, num in numbered if num]

    def parts(num: str) -> list[int] | None:
        if "." not in num:
            return None
        try:
            return [int(p) for p in num.split(".")]
        except ValueError:
            return None

    previous_dotted: tuple[dict, list[int]] | None = None
    for heading, num in numbered:
        parsed = parts(num)
        if parsed is None:
            continue
        if previous_dotted is not None:
            prev_heading, prev_parts = previous_dotted
            if parsed[:-1] == prev_parts[:-1] and len(parsed) == len(prev_parts):
                if parsed[-1] != prev_parts[-1] + 1:
                    issues.append(Issue(
                        heading["line"],
                        "number_gap",
                        f"编号不连续：{prev_parts[0]}.{prev_parts[1] if len(prev_parts) > 1 else ''}"
                        f" → {num}",
                        f"第 {prev_heading['line']} 行的编号与第 {heading['line']} 行之间有跳号，"
                        f"读者可能以为漏了内容",
                    ))
        previous_dotted = (heading, parsed)
    return issues


def check_empty_sections(lines: list[str], headings: list[dict]) -> list[Issue]:
    """检查正文过短的章节（空节）。"""
    issues: list[Issue] = []
    for index, heading in enumerate(headings):
        start = heading["line"]
        end = headings[index + 1]["line"] - 1 if index + 1 < len(headings) else len(lines)
        body_lines = lines[start:end]  # 从标题行之后开始
        text = "".join(body_lines).strip()
        # 去掉嵌套子标题的内容，只看本节直属正文
        direct = []
        for line in body_lines:
            if HEADING_RE.match(line):
                break
            direct.append(line)
        direct_text = "".join(direct).strip()
        if len(direct_text) < BODY_MIN_CHARS and heading["level"] >= 2:
            issues.append(Issue(
                start,
                "empty_section",
                f"章节「{heading['title']}」正文过短",
                f"本节直属正文只有 {len(direct_text)} 个字符"
                f"（阈值 {BODY_MIN_CHARS}）；若是待补内容请标注占位符",
            ))
        _ = text
    return issues


def check_placeholders(lines: list[str]) -> list[Issue]:
    """检查残留占位符。"""
    issues: list[Issue] = []
    in_fence = False
    fence_marker = ""
    for number, raw in enumerate(lines, 1):
        fence = FENCE_RE.match(raw)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            continue
        if in_fence:
            continue
        for pattern, label in PLACEHOLDER_PATTERNS:
            m = pattern.search(raw)
            if m:
                issues.append(Issue(number, "placeholder",
                                    f"残留占位符：{label}",
                                    f"第 {number} 行包含 {m.group(0)!r}"))
                break
    return issues


def check_document(lines: list[str]) -> dict:
    """跑完所有检查，返回结构化报告。"""
    headings = extract_headings(lines)
    issues: list[Issue] = []
    issues.extend(check_heading_levels(headings))
    issues.extend(check_numbering(headings))
    issues.extend(check_empty_sections(lines, headings))
    issues.extend(check_placeholders(lines))

    by_kind: dict[str, int] = {}
    for issue in issues:
        by_kind[issue.kind] = by_kind.get(issue.kind, 0) + 1

    return {
        "line_count": len(lines),
        "heading_count": len(headings),
        "headings": headings,
        "issues": [i.as_dict() for i in issues],
        "issue_count": len(issues),
        "by_kind": by_kind,
        "ok": not issues,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="docstruct",
        description="检查 Markdown 文档的标题层级、编号连续性、空章节与占位符残留（仅依赖标准库）",
        epilog=(
            "示例:\n"
            "  python docstruct.py --file README.md\n"
            "  python docstruct.py --file README.md --json\n"
            "\n"
            "本工具只做机械可判定的检查，不评价措辞与观点。\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--file", "-f", required=True, help="Markdown 文件路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--outline", action="store_true", help="只打印目录结构")
    args = ap.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        print(f"找不到文件：{path}", file=sys.stderr)
        return 2
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"读取失败：{e}", file=sys.stderr)
        return 2

    lines = text.splitlines()
    report = check_document(lines)

    if args.outline:
        print(f"目录结构（{report['heading_count']} 个标题）：")
        for h in report["headings"]:
            indent = "  " * (h["level"] - 1)
            print(f"  {indent}h{h['level']} {h['title']}  (行 {h['line']})")
        return 0

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    print(f"文件：{path}")
    print(f"行数：{report['line_count']}，标题数：{report['heading_count']}")
    print()

    if report["ok"]:
        print("结构检查通过：未发现层级跳级、编号断档、空章节或占位符残留。")
        return 0

    print(f"发现 {report['issue_count']} 处结构问题：")
    for issue in report["issues"]:
        print(f"  行 {issue['line']:>4}  [{issue['kind']}] {issue['message']}")
        if issue["detail"]:
            print(f"          {issue['detail']}")
    print()
    print("分类统计：" + "、".join(f"{k} {v}" for k, v in report["by_kind"].items()))
    print()
    print("说明：本工具只做机械判定。「空章节」阈值可按文档体量自行调整；")
    print("「编号断档」只在同一编号体系内比较，跨体系不报。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
