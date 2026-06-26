#!/usr/bin/env python3
"""
find_selector.py  —  从实际渲染的 DOM 中为失效的 Playwright selector 寻找替代选择器，
                     并可选地在测试源码中自动修复。

用法：
    # 仅分析，输出候选 selector
    python3 find_selector.py <trace_dir> <broken_selector> [--snapshot <snapshot_name>]

    # 同时搜索并修复测试源码
    python3 find_selector.py <trace_dir> <broken_selector> --fix <test_src_dir>

输出：JSON，写到 stdout。
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


# ─── Selector 解析 ───────────────────────────────────────────────────────────

def parse_selector_hints(selector: str) -> dict:
    """
    从 Playwright selector 中提取语义线索。
    返回 dict:
      text          文本内容线索（来自 :has-text、text=、contains）
      tag           标签名（button、input 等，小写）
      classes       CSS class 列表（稳定的，过滤掉哈希化 class）
      id            元素 id
      attrs         {attr: value} 字典
      purpose       推断的用途（click_button / fill_input / assert_text 等）
    """
    hints = {"text": [], "tag": None, "classes": [], "id": None,
             "attrs": {}, "purpose": "unknown"}

    # 去掉 visibility 过滤和 xpath 穿透等噪声
    cleaned = re.sub(r'>>\s*visible=true', '', selector)
    cleaned = re.sub(r'>>\s*xpath=\.', '', cleaned)
    cleaned = re.sub(r'>>\s*nth=\d+', '', cleaned)
    cleaned = re.sub(r'internal:or="[^"]*"', '', cleaned)
    cleaned = re.sub(r':visible', '', cleaned)

    # :has-text('...') 或 :has-text("...")
    for m in re.finditer(r':has-text\([\'"](.+?)[\'"]\)', cleaned, re.IGNORECASE):
        hints["text"].append(m.group(1).strip())

    # text=... 或 text="..."（Playwright text selector）
    for m in re.finditer(r'(?:^|>>)\s*text=[\'"]?([^\'">\s]+)[\'"]?', cleaned):
        hints["text"].append(m.group(1).strip())

    # CSS class names
    for m in re.finditer(r'\.([\w][\w-]*)', cleaned):
        cls = m.group(1)
        if _is_stable_class(cls):
            hints["classes"].append(cls)

    # ID
    m = re.search(r'#([\w-]+)', cleaned)
    if m:
        hints["id"] = m.group(1)

    # Tag name（第一个词，非 . 或 # 开头）
    m = re.match(r'^([a-z][a-z0-9-]*)', cleaned.strip())
    if m and m.group(1) not in ('visible', 'text', 'nth', 'xpath', 'internal'):
        hints["tag"] = m.group(1)

    # 属性 [attr=value] 或 [attr='value']
    for m in re.finditer(r'\[([^=\]]+)=[\'"]?([^\'"=\]]+)[\'"]?\]', cleaned):
        hints["attrs"][m.group(1)] = m.group(2)

    # 推断用途
    if hints["tag"] == "button" or "button" in " ".join(hints["classes"]):
        hints["purpose"] = "click_button"
    elif hints["tag"] in ("input", "textarea"):
        hints["purpose"] = "fill_input"
    elif hints["tag"] in ("a", "link"):
        hints["purpose"] = "click_link"
    elif hints["text"]:
        hints["purpose"] = "click_button"

    return hints


def _is_stable_class(cls: str) -> bool:
    """判断 CSS class 是否稳定（非 CSS Module 哈希）。"""
    # 过滤掉：含随机哈希段（连字符后跟 6+ 位字母数字）
    if re.search(r'---[A-Za-z0-9]{3,}$', cls):
        return False
    if re.search(r'__[a-z0-9]{5,}$', cls):
        return False
    # 过滤掉全小写+数字看起来像 hash 的（如 "abc123ef"）
    if re.match(r'^[a-f0-9]{6,}$', cls):
        return False
    return True


# ─── DOM 遍历 ────────────────────────────────────────────────────────────────

def walk_vdom(node, path=None, results=None):
    """
    递归遍历 Playwright VDOM 树，收集所有真实元素节点。
    每个结果是 dict:
      tag, classes, id, text, attrs, path（CSS path 描述）, data_testid
    """
    if results is None:
        results = []
    if path is None:
        path = []

    # resource reference: [3, N] — 跳过
    if isinstance(node, list) and len(node) == 2 and isinstance(node[0], int):
        return results

    if not isinstance(node, list) or not node:
        return results

    # 字符串节点
    if isinstance(node[0], str) and node[0].isupper():
        tag = node[0].lower()
        attrs = node[1] if len(node) > 1 and isinstance(node[1], dict) else {}
        children = node[2:] if len(node) > 1 and isinstance(node[1], dict) else node[1:]

        classes_raw = attrs.get("class", "")
        classes = classes_raw.split() if classes_raw else []
        stable_classes = [c for c in classes if _is_stable_class(c)]
        node_id = attrs.get("id", "")
        data_testid = attrs.get("data-testid", "")
        aria_label = attrs.get("aria-label", "")
        role = attrs.get("role", "")
        type_ = attrs.get("type", "")
        placeholder = attrs.get("placeholder", "")
        name = attrs.get("name", "")

        # 收集直接文本子节点
        text_parts = []
        for child in children:
            if isinstance(child, str):
                t = child.strip()
                if t:
                    text_parts.append(t)

        node_path = path + [f"{tag}{'#' + node_id if node_id else ''}"]

        record = {
            "tag": tag,
            "classes": classes,
            "stable_classes": stable_classes,
            "id": node_id,
            "data_testid": data_testid,
            "aria_label": aria_label,
            "role": role,
            "type": type_,
            "placeholder": placeholder,
            "name": name,
            "text": " ".join(text_parts),
            "attrs": {k: v for k, v in attrs.items()
                      if k not in ("class", "id", "data-testid", "aria-label",
                                   "role", "type", "placeholder", "name",
                                   "style", "data-reactroot")},
            "path": " > ".join(node_path[-5:]),
        }
        results.append(record)

        for child in children:
            walk_vdom(child, node_path, results)

    elif isinstance(node[0], list):
        for child in node:
            walk_vdom(child, path, results)

    return results


def collect_all_text(node) -> str:
    """递归收集节点下所有文本内容。"""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, list) and len(node) == 2 and isinstance(node[0], int):
        return ""
    if not isinstance(node, list):
        return ""
    parts = []
    start = 2 if (len(node) > 1 and isinstance(node[1], dict)) else 1
    for child in node[start:]:
        t = collect_all_text(child)
        if t:
            parts.append(t)
    return " ".join(parts)


# ─── 候选元素匹配 ─────────────────────────────────────────────────────────────

def score_candidate(elem: dict, hints: dict) -> float:
    """为候选元素打分，越高越匹配。"""
    score = 0.0

    # 文本匹配（最重要）
    elem_text_lower = elem["text"].lower()
    for t in hints["text"]:
        if t.lower() in elem_text_lower:
            score += 10
        elif elem_text_lower in t.lower():
            score += 5

    # Tag 匹配
    if hints["tag"] and elem["tag"] == hints["tag"]:
        score += 8
    elif hints["tag"] == "button" and elem["tag"] in ("button", "a"):
        score += 4

    # Stable class 匹配
    for cls in hints["classes"]:
        if cls in elem["stable_classes"]:
            score += 6
        elif any(cls in c for c in elem["stable_classes"]):
            score += 2

    # 属性匹配
    for attr, val in hints["attrs"].items():
        if attr == "type" and elem.get("type") == val:
            score += 5
        elif attr in elem["attrs"] and elem["attrs"][attr] == val:
            score += 5

    # ID 匹配
    if hints["id"] and elem["id"] == hints["id"]:
        score += 15

    # 降权：无文本、无稳定 class、无 id 的元素
    if not elem["text"] and not elem["stable_classes"] and not elem["id"] and not elem["data_testid"]:
        score -= 5

    return score


def find_candidates(vdom_tree, hints: dict, top_n: int = 5) -> list:
    """在 VDOM 树中找出最匹配 hints 的候选元素。"""
    all_elements = walk_vdom(vdom_tree)
    scored = []
    for elem in all_elements:
        s = score_candidate(elem, hints)
        if s > 0:
            scored.append((s, elem))
    scored.sort(key=lambda x: -x[0])
    return [elem for _, elem in scored[:top_n]]


# ─── 新 Selector 生成 ────────────────────────────────────────────────────────

def generate_selectors(elem: dict, hints: dict) -> list[dict]:
    """
    为候选元素生成多个备选 selector，按稳定性排序。
    返回 list of {selector, reason, stability}
    """
    candidates = []

    # 1. data-testid（最稳定）
    if elem["data_testid"]:
        candidates.append({
            "selector": f'[data-testid="{elem["data_testid"]}"]',
            "reason": "data-testid 属性是专为测试设计的，最稳定",
            "stability": "high",
        })

    # 2. aria-label（语义稳定）
    if elem["aria_label"]:
        tag = elem["tag"] if elem["tag"] != "div" else ""
        candidates.append({
            "selector": f'{tag}[aria-label="{elem["aria_label"]}"]'.lstrip(),
            "reason": "aria-label 是无障碍属性，通常跟随语义变化",
            "stability": "high",
        })

    # 3. 文本内容（对按钮/链接很可靠）
    if elem["text"] and hints["text"]:
        best_text = hints["text"][0]
        tag = elem["tag"] if elem["tag"] in ("button", "a", "label", "span", "h1", "h2", "h3") else ""
        candidates.append({
            "selector": f'{tag}:has-text("{best_text}")'.lstrip(":"),
            "reason": "按钮文本通常跟随 UI 文案变化，较稳定",
            "stability": "medium-high",
        })

    # 4. id（稳定但 SPA 可能动态生成）
    if elem["id"] and not re.search(r'\d{3,}', elem["id"]):
        candidates.append({
            "selector": f'#{elem["id"]}',
            "reason": "元素有固定 id，稳定但需确认不是动态生成的",
            "stability": "medium-high",
        })

    # 5. role + text
    if elem["role"] and elem["text"]:
        best_text = hints["text"][0] if hints["text"] else elem["text"][:30]
        candidates.append({
            "selector": f'[role="{elem["role"]}"]:has-text("{best_text}")',
            "reason": "role 属性结合文本，语义清晰",
            "stability": "medium",
        })

    # 6. 稳定 class 组合
    if elem["stable_classes"]:
        # 选择前 2 个最长的稳定 class（通常更具描述性）
        top_classes = sorted(elem["stable_classes"], key=len, reverse=True)[:2]
        combined = "".join(f".{c}" for c in top_classes)
        if elem["text"]:
            best_text = hints["text"][0] if hints["text"] else elem["text"][:30]
            candidates.append({
                "selector": f'{combined}:has-text("{best_text}")',
                "reason": "稳定 CSS 类 + 文本组合，比单 class 更精确",
                "stability": "medium",
            })
        else:
            candidates.append({
                "selector": combined,
                "reason": "稳定 CSS 类组合（已过滤 CSS Module 哈希类）",
                "stability": "medium",
            })

    # 7. type + text（用于 input/button 元素）
    if elem["type"] and elem["text"]:
        best_text = hints["text"][0] if hints["text"] else elem["text"][:30]
        candidates.append({
            "selector": f'{elem["tag"]}[type="{elem["type"]}"]:has-text("{best_text}")',
            "reason": "type 属性结合文本，适用于表单元素",
            "stability": "medium",
        })

    return candidates if candidates else [{
        "selector": f'{elem["tag"]}:has-text("{elem["text"][:40]}")' if elem["text"] else f'{elem["tag"]}',
        "reason": "兜底：仅使用标签和文本（建议添加 data-testid）",
        "stability": "low",
    }]


# ─── 快照加载 ────────────────────────────────────────────────────────────────

def load_snapshots(trace_dir: str) -> dict:
    """加载 trace.trace 中的所有 frame-snapshot，返回 {snapshotName: html_tree}。"""
    snapshots = {}
    trace_path = os.path.join(trace_dir, "trace.trace")
    with open(trace_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if e.get("type") == "frame-snapshot":
                s = e["snapshot"]
                name = s.get("snapshotName", "")
                html = s.get("html", [])
                if name and html:
                    snapshots[name] = {
                        "html": html,
                        "url": s.get("frameUrl", ""),
                        "html_bytes": len(json.dumps(html)),
                    }
    return snapshots


def pick_best_snapshot(snapshots: dict, failed_action_snapshot: str = None) -> tuple:
    """
    选择最适合 selector 分析的快照（内容最丰富的，优先 before 快照）。
    返回 (snapshot_name, html_tree)
    """
    if failed_action_snapshot and failed_action_snapshot in snapshots:
        snap = snapshots[failed_action_snapshot]
        if snap["html_bytes"] > 1000:
            return failed_action_snapshot, snap["html"]

    # 选内容最多的 before@ 快照
    best_name = None
    best_size = 0
    for name, snap in snapshots.items():
        if snap["html_bytes"] > best_size and "before@" in name:
            best_size = snap["html_bytes"]
            best_name = name

    if best_name:
        return best_name, snapshots[best_name]["html"]

    # 兜底：最大的任意快照
    best_name = max(snapshots, key=lambda n: snapshots[n]["html_bytes"])
    return best_name, snapshots[best_name]["html"]


# ─── 测试源码修复 ─────────────────────────────────────────────────────────────

# 支持的测试语言 selector 调用模式
_SELECTOR_PATTERNS = {
    "java": [
        r'waitForSelector\s*\(\s*"([^"]+)"',
        r'locator\s*\(\s*"([^"]+)"',
        r'querySelector\s*\(\s*"([^"]+)"',
        r'click\s*\(\s*"([^"]+)"',
        r'fill\s*\(\s*"([^"]+)"',
        r'getBySelector\s*\(\s*"([^"]+)"',
    ],
    "typescript": [
        r'locator\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'waitForSelector\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'getByText\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'\$\s*\(\s*[\'"]([^\'"]+)[\'"]',
    ],
    "python": [
        r'locator\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'wait_for_selector\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'query_selector\s*\(\s*[\'"]([^\'"]+)[\'"]',
    ],
}

_FILE_EXTENSIONS = {
    "java": [".java"],
    "typescript": [".ts", ".tsx", ".js"],
    "python": [".py"],
}


def find_selector_in_files(src_dir: str, broken_selector: str) -> list[dict]:
    """在测试源码目录中搜索使用了 broken_selector 的文件和行号。"""
    results = []
    src_path = Path(src_dir)
    if not src_path.exists():
        return results

    # 转义 selector 中的特殊正则字符用于搜索
    escaped = re.escape(broken_selector)

    for root, dirs, files in os.walk(src_path):
        # 跳过常见的非测试目录
        dirs[:] = [d for d in dirs if d not in
                   ("node_modules", ".git", "dist", "build", "target", "__pycache__", ".cache")]
        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext not in (".java", ".ts", ".tsx", ".js", ".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                content = Path(fpath).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if broken_selector not in content:
                continue
            lines = content.splitlines()
            for lineno, line in enumerate(lines, 1):
                if broken_selector in line:
                    results.append({
                        "file": fpath,
                        "line": lineno,
                        "content": line.strip(),
                        "context": "\n".join(lines[max(0, lineno - 3):lineno + 2]),
                    })
    return results


def apply_fix(file_path: str, broken_selector: str, new_selector: str) -> dict:
    """将文件中的 broken_selector 替换为 new_selector。"""
    path = Path(file_path)
    original = path.read_text(encoding="utf-8")
    count = original.count(broken_selector)
    if count == 0:
        return {"status": "not_found", "replacements": 0}
    updated = original.replace(broken_selector, new_selector)
    path.write_text(updated, encoding="utf-8")
    return {"status": "ok", "replacements": count, "file": file_path}


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Find replacement selectors for broken Playwright selectors")
    parser.add_argument("trace_dir", help="Extracted trace directory")
    parser.add_argument("broken_selector", help="The selector that failed")
    parser.add_argument("--snapshot", help="Specific snapshot name to analyze (e.g. before@call@14)")
    parser.add_argument("--fix", metavar="SRC_DIR",
                        help="Test source directory to search and fix")
    parser.add_argument("--apply", metavar="NEW_SELECTOR",
                        help="Automatically apply this selector as the fix (requires --fix)")
    args = parser.parse_args()

    result = {
        "broken_selector": args.broken_selector,
        "hints": {},
        "snapshot_used": None,
        "candidates": [],
        "source_usages": [],
        "fix_results": [],
    }

    # 1. 解析 selector 语义
    hints = parse_selector_hints(args.broken_selector)
    result["hints"] = hints

    # 2. 加载快照
    snapshots = load_snapshots(args.trace_dir)
    if not snapshots:
        result["error"] = "No frame-snapshots found in trace.trace"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 3. 选取最佳快照
    snap_name, html_tree = pick_best_snapshot(snapshots, args.snapshot)
    result["snapshot_used"] = snap_name
    result["available_snapshots"] = {
        k: {"bytes": v["html_bytes"], "url": v["url"]}
        for k, v in snapshots.items()
    }

    # 4. 在 DOM 中找候选元素
    top_candidates = find_candidates(html_tree, hints, top_n=5)

    for elem in top_candidates:
        # 用整棵子树文本补充直接文本
        elem_full_text = elem["text"]
        selectors = generate_selectors(elem, hints)
        result["candidates"].append({
            "element": {
                "tag": elem["tag"],
                "id": elem["id"],
                "data_testid": elem["data_testid"],
                "stable_classes": elem["stable_classes"],
                "text": elem_full_text,
                "aria_label": elem["aria_label"],
                "role": elem["role"],
                "path": elem["path"],
            },
            "suggested_selectors": selectors,
        })

    # 5. 搜索测试源码
    if args.fix:
        result["source_usages"] = find_selector_in_files(args.fix, args.broken_selector)

        # 6. 可选：自动应用修复
        if args.apply and result["source_usages"]:
            for usage in result["source_usages"]:
                fix_result = apply_fix(usage["file"], args.broken_selector, args.apply)
                result["fix_results"].append(fix_result)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
