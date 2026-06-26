---
name: playwright-trace-analysis
description: >
  Analyzes Playwright TraceViewer trace.zip files to diagnose E2E test failures.
  Use this skill whenever a user shares a Playwright trace zip (local path or URL),
  asks why a test failed, or wants to investigate a timeout / selector error / network
  issue captured in a trace. Triggers on: "trace.zip", "traceviewer", "analyze trace",
  "用例失败", "排查 trace", "waitForSelector timeout", "waitForNavigation timeout",
  or any reference to a .zip from a Playwright test run.
---

# Playwright Trace 失败分析

## 概述

接收一个 Playwright trace.zip 的**本地路径**或 **HTTP(S) URL**，提取关键诊断信息，输出结构化的失败根因报告。

---

## Step 1 — 获取 zip 文件

- **URL**：用以下命令下载到唯一路径（`$$` 是进程 ID，确保不冲突）：
  ```bash
  ZIP_FILE=$(mktemp -p "${TMPDIR:-/tmp}" pw_trace_XXXXXX.zip)
  curl -L -o "$ZIP_FILE" "<URL>"
  ```
- **本地路径**：直接用原始路径作为 `ZIP_FILE`，无需复制

---

## Step 2 — 解压

**必须使用唯一临时目录**，防止多次运行时路径冲突：

```bash
TRACE_DIR=$(mktemp -d -p "${TMPDIR:-/tmp}")
unzip -o <zip_path> -d "$TRACE_DIR"
```

确认 `$TRACE_DIR` 中存在 `trace.trace` 和 `trace.network`（都是 NDJSON 格式，每行一个 JSON 对象）。

---

## Step 3 — 收集必要的用例失败信息并初步推断原因

> **此步骤为必选项。** 在解析 trace 之前，必须先获取用例失败的结构化信息，并据此形成初步假设。

### 3.1 要求用户提供失败信息

用户**必须**提供以下结构的失败信息（全部字段）：

```json
{
    "class": "a.b.c",
    "method": "testxx",
    "args": [],
    "logs": "line1\nline2\nline3\nline4",
    "error": "error message",
    "stacktrace": "<stack_trace>",
    "start": "2026-02-03T00:00:00Z",
    "end": "2026-02-03T00:00:10Z"
}
```

如果用户未提供，**停止分析并明确要求用户补充完整的失败信息**，说明每个字段的用途。

各字段用途：

| 字段 | 用途 |
|------|------|
| `start`/`end` | 传给 `--start`/`--end` 参数，将 trace 分析范围锁定到该用例时间窗口 |
| `error` | 与 trace `failures[].error` 交叉验证，确认异常一致性 |
| `stacktrace` | 定位直接失败点（`waitForSelector`、`waitForNavigation`、断言方法等） |
| `logs` | 补充业务层上下文：断言前状态、初始化日志、最后执行到的业务步骤 |
| `class`/`method` | 反向确认时间窗口是否对应正确用例 |

### 3.2 基于失败信息初步推断错误原因

在运行任何脚本之前，对 `error`、`stacktrace`、`logs` 进行静态分析，形成初步假设：

**分析 `error` 字段**，识别错误类型：
- 含 `TimeoutError` / `timeout` → 元素等待超时或页面导航超时
- 含 `AssertionError` / `expected` / `but was` → 断言失败，数据或状态不符合预期
- 含 `NoSuchElementException` / `ElementNotFound` → 选择器失效或元素未渲染
- 含 `NullPointerException` → 页面对象或数据初始化问题
- 含网络相关关键词（`connection refused`、`ECONNREFUSED`、`502`、`503`）→ 后端服务异常

**分析 `stacktrace` 字段**，定位失败调用栈：
- 找到测试代码中的最后一帧（含 `class`/`method` 名），确认失败发生的具体测试步骤
- 找到 Playwright/框架的最后一帧，确认是哪个 Playwright API 失败（`waitForSelector`、`waitForNavigation`、`click`、`fill` 等）
- 若 stacktrace 含 `assertThat` / `assertEquals` 等断言方法，说明是断言失败而非 UI 交互失败

**分析 `logs` 字段**，提取业务上下文：
- 找到最后一条成功日志，确认用例执行到了哪一步
- 识别是否有异常初始化日志（如登录失败、团队创建失败等前置条件问题）
- 识别是否有业务断言前的状态打印（如"实际值: xxx，期望值: yyy"）

**输出初步推断**，在开始解析 trace 之前，明确说明：
```
初步推断（基于 error/stacktrace/logs）：
- 错误类型：<TimeoutError / AssertionError / ...>
- 失败位置：<class.method，第 N 行>
- 失败操作：<waitForSelector(".xxx") / assertThat(...) / ...>
- 最后成功步骤：<从 logs 提取>
- 初步假设：<1~2 句话说明可能原因>
- 待 trace 验证的假设：<列出需要从 trace 中证实或证伪的具体点>
```

---

## Step 4 — 运行解析脚本，从 trace 中提取失败证据

使用 Step 3 得到的 `start`/`end` 时间范围，将分析范围精确锁定到该用例：

```bash
REPORT_FILE=$(mktemp -p "${TMPDIR:-/tmp}" pw_trace_report_XXXXXX.json)
python3 <skill_dir>/scripts/parse_trace.py "$TRACE_DIR" \
    --start "2026-02-03T00:00:00Z" \
    --end   "2026-02-03T00:00:10Z" \
    > "$REPORT_FILE"
```

过滤逻辑：时间窗口两端各留 1s 缓冲，处理机器时钟轻微漂移；`before` 事件按 `startTime` 过滤，`screencast-frame` 按 `timestamp` 过滤，网络请求按 `startedDateTime` 过滤。

脚本输出一个 JSON 对象，包含以下字段：

| 字段 | 说明 |
|------|------|
| `environment` | browser、版本、platform、baseURL、SDK 语言 |
| `actions` | 所有操作的摘要（callId、class.method、耗时、状态） |
| `failures` | 仅包含失败操作（有 `error` 字段） |
| `logs` | 失败操作前后的详细 Playwright 内部日志 |
| `console_events` | 页面 console 输出（含 JS 错误） |
| `screencast_timeline` | 截图帧时间轴 + 长空白期检测（`long_gaps`） |
| `dom_at_failure` | 失败时的 HTML 快照 + 加载类名 + selector 搜索结果 |
| `key_screenshots` | 关键截图文件路径（first / before_failure / after_failure / last） |
| `network_requests` | API 请求列表，`anomaly:true` 标出 4xx/5xx/-1 |

---

## Step 5 — 读取关键截图

使用 **Read 工具**（支持多模态 JPEG）直接读取 `key_screenshots` 中的图片，顺序：

1. `first`：初始状态
2. `before_failure`：最接近超时/失败的帧
3. `last`（如与 before_failure 不同）

从截图中观察：页面是否空白、是否停留在全屏 Loading 动画、是否有可见内容但目标元素缺失。

---

## Step 5.5 — Selector 失效检测与自动修复（按需）

**当且仅当** `dom_at_failure.selector_mismatch_detected == true` 时，执行本步骤。这表示页面 DOM 有内容，但测试等待的目标 selector 在 DOM 中找不到——是典型的**页面 DOM 变更导致 selector 失效**场景。

### 5.5.1 运行 selector 候选分析

```bash
python3 <skill_dir>/scripts/find_selector.py \
    "$TRACE_DIR" \
    "<broken_selector>" \
    [--snapshot "<snapshot_name>"]
```

- `broken_selector`：从 `failures[].key_param` 取失败操作的 selector 值
- `snapshot_name`：可选，指定用哪个 DOM 快照（默认自动选最大的 before@ 快照）

脚本输出 JSON，关键字段：

| 字段 | 说明 |
|------|------|
| `hints` | 从 broken selector 解析出的语义线索（文本、tag、class、attrs） |
| `snapshot_used` | 实际使用的快照名 |
| `candidates` | 最多 5 个候选元素，每个附带多个 `suggested_selectors` |
| `suggested_selectors[].stability` | `high` / `medium` / `low`，优先选 high |
| `source_usages` | 若传了 `--fix`，列出测试文件中使用 broken selector 的位置 |

### 5.5.2 选择最佳替代 selector

从 `candidates[0].suggested_selectors` 中按优先级选择：
1. `stability: high` → `data-testid` 或 `aria-label` 选择器
2. `stability: medium-high` → 文本选择器（`:has-text("...")`）
3. 避免选 `stability: low`，除非没有更好的选项

### 5.5.3 搜索测试源码中的使用位置

```bash
python3 <skill_dir>/scripts/find_selector.py \
    "$TRACE_DIR" \
    "<broken_selector>" \
    --fix <test_src_dir>
```

`test_src_dir` 是测试代码所在目录（`.java`/`.ts`/`.py` 文件）。输出的 `source_usages` 包含每处使用的文件路径、行号、代码上下文。

### 5.5.4 自动应用修复

确认候选 selector 合理后，用 **Edit 工具**直接修改测试文件：

```python
# 示例：将旧 selector 替换为新 selector
# 用 Edit 工具，old_string = 旧 selector，new_string = 新 selector
# replace_all=true 替换所有同一 selector 的使用
```

或用脚本批量替换（当同一 selector 在多处使用时）：

```bash
python3 <skill_dir>/scripts/find_selector.py \
    "$TRACE_DIR" \
    "<broken_selector>" \
    --fix <test_src_dir> \
    --apply "<new_selector>"
```

### 5.5.5 在报告中说明修复结果

在最终报告的"修复建议"部分，展示：
- 失效的原始 selector
- DOM 中实际找到的元素（tag、class、text）
- 推荐的新 selector 及稳定性说明
- 已修改的文件列表（如执行了自动修复）

---

## Step 6 — 综合分析，输出报告

将 Step 3.2 的初步推断与 trace 提取的证据合并，输出结构化报告：

### 报告结构

```
## 失败根因分析

### 初步推断（基于 error/stacktrace/logs）
- 错误类型：<TimeoutError / AssertionError / ...>
- 失败位置：<class.method，第 N 行>
- 失败操作：<waitForSelector(".xxx") / assertThat(...) / ...>
- 最后成功步骤：<从 logs 提取>
- 初步假设：<1~2 句话>

### Trace 验证结果
- 假设是否成立：<成立 / 部分成立 / 证伪，说明原因>
- Trace 新增信息：<trace 中发现的、logs/stacktrace 未体现的信息>

### 直接错误
- 操作：<class.method(key_param)>
- 错误：<error.name: error.message>
- 耗时：<duration_ms>ms（超时值一般是 30000ms）

### 根因
[1~3 句话说清楚为什么失败——结合截图、DOM、日志、网络]

### 时间线
[列出关键时间点，重点标注异常等待空白期]

### 证据
- **截图**：[描述页面视觉状态]
- **DOM**：[在失败时刻 HTML 里找到了什么——loading-pane、spinner、还是目标 selector 缺失]
- **网络**：[哪些 API 异常，status:-1 意味着请求未收到响应]
- **日志**：[locator 解析是否成功，waiting for 目标是什么]

### 修复建议
[针对根因给出具体可操作的代码修改，最好附代码片段]
```

---

## 已知失败模式速查

分析时对照这几种典型场景，有助于快速定位：

### 模式 A：waitForURL / waitForNavigation 超时
- **特征**：日志中出现 `navigated to .../pending?redirect=...`，说明页面先跳中间页再跳目标
- **根因**：应用用 pending 中间页做异步等待，测试等待的 URL 从未直接出现
- **修复**：先 `waitForURL("**/pending**")` 再 `waitForURL("**/target**", timeout=60000)`

### 模式 B：waitForSelector 超时 — 应用 Loading 卡住
- **特征**：`screencast_timeline.long_gaps` 有 10s+ 的空白期；`dom_at_failure.loading_classes_found` 含 `loading-pane` 等；截图显示全屏白屏或 spinner
- **根因**：React 应用 shell 停留在全屏 loading 状态（通常是 JS 解析慢、关键 API 未响应）
- **修复**：先等 loading-pane 消失（`setState=HIDDEN`），再等目标元素；或增大总超时

### 模式 C：waitForSelector 超时 — Selector 失效（DOM 变更）
- **特征**：`dom_at_failure.selector_mismatch_detected == true`；截图页面有内容；`target_selector_search.found == false`
- **根因**：页面 DOM 结构或 class 名变更（版本升级、组件重构、CSS Module hash 变化）
- **修复**：执行 **Step 5.5**，运行 `find_selector.py` 自动从实际 DOM 中找替代 selector 并修复测试文件

### 模式 D：API 4xx / 5xx
- **特征**：`network_requests` 中有 `anomaly:true` 且 `status>=400` 的条目
- **根因**：后端接口返回错误，页面无法渲染数据
- **修复**：检查 `response_body` 里的错误信息，排查后端问题

### 模式 E：网络请求 status:-1
- **特征**：`network_requests` 中有 `status:-1`（请求已发出但无响应）
- **根因**：关键 API 在超时窗口内未收到响应，通常是测试环境网络/服务异常
- **修复**：增大超时，或在测试前加健康检查确认服务可用

### 模式 F：PageModel 对象为 Null（NullPointerException）
- **特征**：`error` 含 `NullPointerException`，stacktrace 指向页面对象字段访问；logs 中最后成功步骤停在页面初始化阶段
- **根因**：框架通过选择器定位元素后注入 PageModel 对象，若选择器未匹配到页面任何元素，注入结果为 null
- **修复**：**优先检查选择器**，而非检查注入逻辑——用 `dom_at_failure` 的 HTML 快照验证目标元素是否存在；再执行 **Step 5.5** 寻找正确 selector

---

## 参考文档

- [Trace 文件结构 Reference](docs/trace-reference.md)：`trace.zip` 目录结构、各文件格式及字段说明

---

## 技术备注

- `trace.trace` 是 NDJSON，每行一个事件对象；`trace.network` 同格式
- `screencast-frame` 的 `sha1` 字段对应 `resources/` 目录下的 JPEG 文件名
- `frame-snapshot` 的 `html` 是 Playwright VDOM 树（嵌套数组），脚本已转换为可读文本
- `status:-1` 表示请求发出时 trace 已停止记录，未收到响应
- 时间转换：`wall_time = base_wallTime_ms + (mono - base_mono)`，再转 UTC
- `resources/*.json` 是 API 响应体缓存（非 DOM 快照）；`resources/*.html` 是服务端初始 HTML（非渲染后 DOM）
- `find_selector.py` 的 selector 稳定性优先级：`data-testid` > `aria-label` > `:has-text()` > 稳定 CSS class > tag+text
- CSS Module 哈希 class（如 `loading-pane---cspaV`）会被自动过滤，不会出现在推荐 selector 中