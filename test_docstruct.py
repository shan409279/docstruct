"""docstruct 的自检测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docstruct import (check_document, check_heading_levels,  # noqa: E402
                       check_numbering, check_placeholders,
                       detect_numbering, extract_headings)

ENTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docstruct.py")


def test_extract_headings_basic() -> None:
    lines = ["# 标题", "正文", "## 小节", "内容"]
    headings = extract_headings(lines)
    assert len(headings) == 2, headings
    assert headings[0]["level"] == 1
    assert headings[1]["level"] == 2


def test_extract_headings_ignores_fenced_code() -> None:
    lines = ["# 真标题", "```", "# 这是代码里的注释", "```", "## 真小节"]
    headings = extract_headings(lines)
    assert len(headings) == 2, headings
    assert headings[1]["title"] == "真小节"


def test_extract_headings_handles_tilde_fence() -> None:
    lines = ["# 标题", "~~~", "## 假的", "~~~"]
    headings = extract_headings(lines)
    assert len(headings) == 1, headings


def test_detect_numbering_variants() -> None:
    assert detect_numbering("1. 概述") == "1"
    assert detect_numbering("1.1 细节") == "1.1"
    assert detect_numbering("一、开始") == "一"
    assert detect_numbering("第一章 绪论") == "一"
    assert detect_numbering("没有编号") is None


def test_level_jump_is_detected() -> None:
    lines = ["# 一", "## 二", "#### 四"]
    issues = check_heading_levels(extract_headings(lines))
    assert len(issues) == 1, issues
    assert issues[0].kind == "level_jump"
    assert issues[0].line == 3


def test_level_going_up_is_fine() -> None:
    lines = ["# 一", "## 二", "### 三", "## 四"]
    issues = check_heading_levels(extract_headings(lines))
    assert issues == [], issues


def test_numbering_gap_is_detected() -> None:
    lines = ["# 标题", "## 1.1 第一", "## 1.3 第三"]
    issues = check_numbering(extract_headings(lines))
    assert len(issues) == 1, issues
    assert issues[0].kind == "number_gap"


def test_numbering_continuous_is_fine() -> None:
    lines = ["# 标题", "## 1.1 第一", "## 1.2 第二", "## 1.3 第三"]
    issues = check_numbering(extract_headings(lines))
    assert issues == [], issues


def test_numbering_different_series_not_compared() -> None:
    lines = ["# 标题", "## 1.1 第一", "## 2.1 第二"]
    issues = check_numbering(extract_headings(lines))
    assert issues == [], issues


def test_placeholder_is_detected() -> None:
    lines = ["# 标题", "这里 <待补充> 没写。"]
    issues = check_placeholders(lines)
    assert len(issues) == 1, issues
    assert issues[0].kind == "placeholder"


def test_placeholder_in_code_fence_ignored() -> None:
    lines = ["# 标题", "```", "TODO: fix this", "```"]
    issues = check_placeholders(lines)
    assert issues == [], issues


def test_empty_section_is_detected() -> None:
    lines = ["# 标题", "## 空节", "## 正常节", "这一节有足够的正文内容用于通过阈值检查，所以不会被判定为空节。"]
    report = check_document(lines)
    kinds = [i["kind"] for i in report["issues"]]
    assert "empty_section" in kinds, report


def test_report_ok_on_clean_document() -> None:
    lines = [
        "# 标题",
        "",
        "这是一段足够长的正文内容，用来避免被判定为空章节，应该能顺利通过检查。",
        "",
        "## 1.1 小节",
        "",
        "这是小节的正文，同样写得足够长，足够长，足够长，足够长，足够长，足够长，"
        "足够长，足够长，足够长。",
        "",
        "### 1.1.1 更深一层",
        "",
        "这一层的正文也需要足够长才能通过空章节的检查阈值，所以这里写得比较长，"
        "比较长，比较长，比较长，比较长，比较长。",
    ]
    report = check_document(lines)
    assert report["ok"], report["issues"]


def test_cli_help_exits_zero() -> None:
    r = subprocess.run([sys.executable, ENTRY, "--help"], capture_output=True,
                       text=True, timeout=30, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert "docstruct" in r.stdout


def _write(content: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as f:
        f.write(content)
        return f.name


def test_cli_reports_issues_and_exits_one() -> None:
    path = _write("# 标题\n\n## 一\n\n#### 跳级\n")
    try:
        r = subprocess.run([sys.executable, ENTRY, "--file", path],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 1, (r.returncode, r.stdout)
        assert "level_jump" in r.stdout, r.stdout
    finally:
        os.unlink(path)


def test_cli_json_output() -> None:
    path = _write("# 标题\n\n## 一\n\n#### 跳级\n")
    try:
        r = subprocess.run([sys.executable, ENTRY, "--file", path, "--json"],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 1, r.returncode
        data = json.loads(r.stdout)
        assert data["issue_count"] >= 1, data
        assert data["by_kind"], data
    finally:
        os.unlink(path)


def test_cli_outline_mode() -> None:
    path = _write("# 标题\n\n## 小节\n\n### 更深\n")
    try:
        r = subprocess.run([sys.executable, ENTRY, "--file", path, "--outline"],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
        assert "h1 标题" in r.stdout, r.stdout
        assert "h3 更深" in r.stdout, r.stdout
    finally:
        os.unlink(path)


def test_cli_missing_file_exits_two() -> None:
    r = subprocess.run([sys.executable, ENTRY, "--file", "nope.md"],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 2, (r.returncode, r.stderr)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK  ] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n共 {len(tests)} 项，失败 {failed} 项")
    raise SystemExit(1 if failed else 0)
