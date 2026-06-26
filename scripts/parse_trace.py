#!/usr/bin/env python3
"""
parse_trace.py  —  从解压后的 Playwright trace 目录提取结构化诊断数据。

用法：
    python3 parse_trace.py <trace_dir> [--start ISO] [--end ISO]

输出：JSON，写到 stdout。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


def load_ndjson(path):
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events


def mono_to_wall_str(mono, base_mono, base_wall_ms):
    wall_ms = base_wall_ms + (mono - base_mono)
    dt = datetime.fromtimestamp(wall_ms / 1000, tz=timezone.utc)
    return dt.strftime("%H:%M:%S.%f")[:-3]


def parse_iso(ts: str) -> float:
    """ISO 8601 timestamp → epoch milliseconds（支持 Z 和 +00:00）。"""
    ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    return dt.timestamp() * 1000


def in_window(wall_ms: float, win_start_ms, win_end_ms) -> bool:
    """判断 wall_ms 是否在时间窗口内，两端各留 1s 缓冲。"""
    if win_start_ms is None and win_end_ms is None:
        return True
    if win_start_ms is not None and wall_ms < win_start_ms - 1000:
        return False
    if win_end_ms is not None and wall_ms > win_end_ms + 1000:
        return False
    return True


def vdom_to_text(node, depth=0, max_depth=8):
    """将 Playwright VDOM 树（嵌套数组）转为缩进文本。"""
    if depth > max_depth:
        return ""
    if isinstance(node, str):
        txt = node.strip()
        return ("  " * depth + txt) if txt else ""
    if isinstance(node, list) and len(node) >= 1 and isinstance(node[0], int):
        return ""  # resource reference [type, id], skip
    if not isinstance(node, list) or len(node) < 1:
        return ""

    tag = node[0]
    if not isinstance(tag, str):
        return ""
    tag = tag.lower()

    attrs = node[1] if len(node) > 1 and isinstance(node[1], dict) else {}
    children = node[2:] if len(node) > 1 and isinstance(node[1], dict) else node[1:]

    cls = attrs.get("class", "")
    id_ = attrs.get("id", "")
    aria_busy = attrs.get("aria-busy", "")
    attr_parts = []
    if id_:
        attr_parts.append(f'id="{id_}"')
    if cls:
        attr_parts.append(f'class="{cls[:80]}"')
    if aria_busy:
        attr_parts.append(f'aria-busy="{aria_busy}"')
    attr_str = (" " + " ".join(attr_parts)) if attr_parts else ""
    opening = f"{'  ' * depth}<{tag}{attr_str}>"

    lines = [opening]
    for child in children:
        r = vdom_to_text(child, depth + 1, max_depth)
        if r:
            lines.append(r)
    return "\n".join(lines)


def search_selectors_in_dom(html_str, selectors):
    """在序列化后的 HTML 字符串中搜索 selector 关键词是否出现。"""
    results = {}
    for sel in selectors:
        # 从 selector 里提取 class 名（.foo）和文本关键词
        classes = re.findall(r'\.([\w-]+)', sel)
        found = any(cls in html_str for cls in classes)
        results[sel] = {"found": found, "classes_searched": classes}
    return results


def extract_key_param(params):
    for key in ("url", "selector", "value", "text", "expression"):
        if key in params:
            val = str(params[key])
            return val[:120] + ("…" if len(val) > 120 else "")
    if params:
        first = next(iter(params.values()))
        if isinstance(first, str):
            return first[:80]
    return ""


def main():
    parser = argparse.ArgumentParser(description="Parse Playwright trace")
    parser.add_argument("trace_dir")
    parser.add_argument("--start", help="Test case start time (ISO 8601), e.g. 2026-02-03T00:00:00Z")
    parser.add_argument("--end",   help="Test case end time (ISO 8601)")
    args = parser.parse_args()

    trace_dir = args.trace_dir
    win_start_ms = parse_iso(args.start) if args.start else None
    win_end_ms   = parse_iso(args.end)   if args.end   else None

    trace_path = os.path.join(trace_dir, "trace.trace")
    network_path = os.path.join(trace_dir, "trace.network")
    resources_dir = os.path.join(trace_dir, "resources")

    if not os.path.exists(trace_path):
        print(json.dumps({"error": f"trace.trace not found in {trace_dir}"}))
        sys.exit(1)

    trace_events = load_ndjson(trace_path)

    # ── 环境信息 ──
    ctx = next((e for e in trace_events if e.get("type") == "context-options"), {})
    base_mono = ctx.get("monotonicTime", 0)
    base_wall = ctx.get("wallTime", 0)

    environment = {
        "browserName": ctx.get("browserName"),
        "playwrightVersion": ctx.get("playwrightVersion"),
        "platform": ctx.get("platform"),
        "baseURL": ctx.get("options", {}).get("baseURL"),
        "sdkLanguage": ctx.get("sdkLanguage"),
        "wallTime_iso": datetime.fromtimestamp(base_wall / 1000, tz=timezone.utc).isoformat(),
    }

    # ── 构建 callId 映射（按时间窗口过滤）──
    calls = {}
    for e in trace_events:
        t = e.get("type")
        cid = e.get("callId")
        if not cid:
            continue
        if t == "before":
            wall = base_wall + (e.get("startTime", 0) - base_mono)
            if not in_window(wall, win_start_ms, win_end_ms):
                continue
            calls.setdefault(cid, {})["before"] = e
        elif t == "after":
            calls.setdefault(cid, {})["after"] = e

    # ── 操作摘要 + 失败列表 ──
    actions = []
    failures = []
    for cid in sorted(calls.keys()):
        c = calls[cid]
        b = c.get("before", {})
        a = c.get("after", {})
        err = a.get("error")
        start = b.get("startTime", 0)
        end = a.get("endTime", start)
        duration_ms = round(end - start)
        wall_str = mono_to_wall_str(start, base_mono, base_wall) if start else ""
        action = {
            "callId": cid,
            "class": b.get("class"),
            "method": b.get("method"),
            "key_param": extract_key_param(b.get("params", {})),
            "duration_ms": duration_ms,
            "wall_time": wall_str,
            "status": "error" if err else "ok",
            "error": err,
            "before_snapshot": b.get("beforeSnapshot"),
            "after_snapshot": a.get("afterSnapshot"),
        }
        actions.append(action)
        if err:
            failures.append(action)

    # ── 失败前后的操作日志 ──
    failed_ids = {f["callId"] for f in failures}
    all_ids = sorted(calls.keys())
    context_ids = set()
    for fid in failed_ids:
        if fid in all_ids:
            idx = all_ids.index(fid)
            context_ids.update(all_ids[max(0, idx - 3):idx + 1])
    if not context_ids:
        context_ids = set(all_ids)

    logs = []
    for e in trace_events:
        if e.get("type") != "log":
            continue
        cid = e.get("callId", "")
        if cid not in context_ids:
            continue
        logs.append({
            "callId": cid,
            "wall_time": mono_to_wall_str(e.get("time", 0), base_mono, base_wall),
            "message": e.get("message", ""),
        })

    # ── Console 输出 ──
    console_events = []
    for e in trace_events:
        if e.get("type") != "console":
            continue
        wall = base_wall + (e.get("time", 0) - base_mono)
        if not in_window(wall, win_start_ms, win_end_ms):
            continue
        console_events.append({
            "messageType": e.get("messageType"),
            "text": e.get("text", ""),
            "url": e.get("location", {}).get("url", ""),
            "wall_time": mono_to_wall_str(e.get("time", 0), base_mono, base_wall),
        })

    # ── Screencast 时间轴 ──
    screencast_frames = []
    for e in trace_events:
        if e.get("type") != "screencast-frame":
            continue
        ts = e.get("timestamp", 0)
        wall = base_wall + (ts - base_mono)
        if not in_window(wall, win_start_ms, win_end_ms):
            continue
        screencast_frames.append({
            "sha1": e.get("sha1", ""),
            "wall_time": mono_to_wall_str(ts, base_mono, base_wall),
            "monotonic_ms": ts,
            "file_path": os.path.join(resources_dir, e.get("sha1", "")),
        })

    # 检测空白期（相邻帧间隔 > 5s）
    gaps = []
    for i in range(1, len(screencast_frames)):
        prev = screencast_frames[i - 1]["monotonic_ms"]
        curr = screencast_frames[i]["monotonic_ms"]
        gap_s = (curr - prev) / 1000
        if gap_s > 5:
            gaps.append({
                "from": screencast_frames[i - 1]["wall_time"],
                "to": screencast_frames[i]["wall_time"],
                "gap_seconds": round(gap_s, 1),
            })

    # ── DOM 快照（取失败操作的 after snapshot）──
    dom_analysis = None
    if failures:
        target_snapshot = failures[-1].get("after_snapshot") or failures[-1].get("before_snapshot")
        # 同时收集失败操作等待的目标 selector
        fail_call = calls.get(failures[-1]["callId"], {})
        fail_params = fail_call.get("before", {}).get("params", {})
        target_selectors = []
        if "selector" in fail_params:
            target_selectors.append(fail_params["selector"])

        for e in trace_events:
            if e.get("type") != "frame-snapshot":
                continue
            s = e.get("snapshot", {})
            if s.get("snapshotName") != target_snapshot:
                continue

            html_str = json.dumps(s.get("html", []))
            dom_text = vdom_to_text(s.get("html", []))

            # 搜索常见的 loading/spinner class
            loading_classes = []
            for m in re.finditer(r'"class",\s*"([^"]*(?:load|spin|busy|pane|skeleton)[^"]*)"',
                                  html_str, re.IGNORECASE):
                loading_classes.append(m.group(1))

            aria_busy_vals = re.findall(r'"aria-busy",\s*"([^"]+)"', html_str)

            selector_search = search_selectors_in_dom(html_str, target_selectors)

            # ── 判断是否是 selector 失效（DOM 有内容但目标找不到）──
            dom_has_content = len(html_str) > 5000
            selector_broken = (
                target_selectors
                and all(not v["found"] for v in selector_search.values())
                and dom_has_content
                and not loading_classes  # 排除 loading 状态导致的误判
            )

            dom_analysis = {
                "snapshot_name": target_snapshot,
                "frame_url": s.get("frameUrl"),
                "html_text": dom_text[:6000] + ("\n… (truncated)" if len(dom_text) > 6000 else ""),
                "loading_classes_found": list(set(loading_classes)),
                "aria_busy_values": aria_busy_vals,
                "target_selector_search": selector_search,
                "selector_mismatch_detected": selector_broken,
                "selector_mismatch_hint": (
                    "DOM 内容丰富（{} bytes）但目标 selector 未找到，"
                    "可能是页面 DOM 结构/class 名变更。"
                    "运行 find_selector.py 获取替代选择器。".format(len(html_str))
                ) if selector_broken else None,
            }
            break

    # ── 关键截图路径 ──
    key_screenshots = {}
    if screencast_frames:
        key_screenshots["first"] = screencast_frames[0]["file_path"]
        # 失败时间点最近的帧
        if failures:
            fail_end_mono = calls.get(failures[-1]["callId"], {}).get("after", {}).get("endTime", 0)
            before_fail = [f for f in screencast_frames if f["monotonic_ms"] <= fail_end_mono]
            after_fail = [f for f in screencast_frames if f["monotonic_ms"] > fail_end_mono]
            if before_fail:
                key_screenshots["before_failure"] = before_fail[-1]["file_path"]
            if after_fail:
                key_screenshots["after_failure"] = after_fail[0]["file_path"]
        if screencast_frames[-1]["file_path"] not in key_screenshots.values():
            key_screenshots["last"] = screencast_frames[-1]["file_path"]

    # ── 网络请求（仅 API）──
    network_requests = []
    if os.path.exists(network_path):
        net_events = load_ndjson(network_path)
        for e in net_events:
            snap = e.get("snapshot", {})
            req = snap.get("request", {})
            resp = snap.get("response", {})
            url = req.get("url", "")
            if not any(x in url for x in ["/api/", "/graphql", "/rpc", "/v1/", "/v2/"]):
                continue
            started = snap.get("startedDateTime", "")
            if started and (win_start_ms is not None or win_end_ms is not None):
                try:
                    req_wall = parse_iso(started)
                    if not in_window(req_wall, win_start_ms, win_end_ms):
                        continue
                except ValueError:
                    pass
            method = req.get("method", "")
            status = resp.get("status")
            post_data = req.get("postData", {}).get("text", "") if method in ("POST", "PUT", "PATCH") else ""
            resp_text = resp.get("content", {}).get("text", "") if isinstance(status, int) and status >= 400 else ""
            network_requests.append({
                "started_at": snap.get("startedDateTime", "")[11:23],
                "method": method,
                "url": url,
                "status": status,
                "duration_ms": round(snap.get("time", 0)),
                "anomaly": status == -1 or (isinstance(status, int) and status >= 400),
                "request_body": post_data[:400] if post_data else None,
                "response_body": resp_text[:400] if resp_text else None,
            })

    result = {
        "environment": environment,
        "actions": actions,
        "failures": failures,
        "logs": logs,
        "console_events": console_events,
        "screencast_timeline": {
            "total_frames": len(screencast_frames),
            "frames": screencast_frames,
            "long_gaps": gaps,
        },
        "dom_at_failure": dom_analysis,
        "key_screenshots": key_screenshots,
        "network_requests": network_requests,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
