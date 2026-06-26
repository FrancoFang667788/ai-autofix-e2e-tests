# AI Autofix E2E Tests

自动分析 Playwright `trace.zip` 文件，诊断 E2E 测试失败根因，并自动修复失效的选择器。

## 它能做什么

Playwright E2E 测试失败时会生成 `trace.zip`，但手动翻 NDJSON 事件流、DOM 快照、截图和网络日志非常痛苦。这个工具把整个诊断流程自动化：

1. **解析** trace 文件，按测试用例时间窗口过滤
2. **静态分析** error/stacktrace/logs，形成初步假设
3. **提取证据** — 操作序列、失败详情、DOM 快照、截图、网络请求
4. **截图确认** — 读取关键帧（首帧、失败前、末帧），视觉验证页面状态
5. **自动修复失效选择器** — 当 DOM 变更导致选择器失效时，从实际 DOM 中自动寻找替代选择器并修改测试文件
6. **输出结构化根因报告** — 包含时间线、证据链和可操作的修复建议

## 已知失败模式

| 模式 | 症状 | 根因 |
|------|------|------|
| **A: 导航超时** | 日志出现 `navigated to .../pending?redirect=...` | 应用使用了中间跳转页 |
| **B: Loading 卡住** | 截图长时间空白，DOM 含 `loading-pane` | React 应用停留在全屏 loading |
| **C: 选择器失效** | DOM 有内容但目标选择器找不到 | CSS class / DOM 结构变更 |
| **D: API 报错** | 网络请求 `status >= 400` | 后端接口返回错误 |
| **E: 无响应** | 网络请求 `status: -1` | API 请求未收到响应 |
| **F: PageModel 为 Null** | 页面对象字段 `NullPointerException` | 选择器未匹配，注入返回 null |

## 项目结构

```
ai-autofix-e2e-tests/
├── SKILL.md                    # Claude Code skill 定义（完整 6 步工作流）
├── scripts/
│   ├── parse_trace.py          # 解析 trace.zip，输出结构化 JSON 报告
│   └── find_selector.py        # 寻找替代选择器并自动修复测试文件
├── docs/
│   └── trace-reference.md      # Playwright trace.zip 文件格式参考
└── evals/
    └── evals.json              # 评估用例
```

## 使用方式

### 作为 Claude Code Skill 使用

将项目复制到你的 `.claude/skills/` 目录：

```bash
cp -r ai-autofix-e2e-tests /path/to/your/project/.claude/skills/playwright-trace-analysis
```

然后在 Claude Code 中调用：
```
/playwright-trace-analysis
```

提供 trace.zip 路径（或 URL）和测试失败信息，skill 会引导完成完整分析。

### 独立使用脚本

#### 解析 trace

```bash
# 先解压 trace.zip
unzip trace.zip -d /tmp/trace_output

# 解析，可选按时间窗口过滤
python3 scripts/parse_trace.py /tmp/trace_output \
    --start "2026-01-01T00:00:00Z" \
    --end   "2026-01-01T00:01:00Z"
```

输出 JSON 对象，包含：`environment`、`actions`、`failures`、`logs`、`console_events`、`screencast_timeline`、`dom_at_failure`、`key_screenshots`、`network_requests`。

#### 寻找替代选择器

```bash
# 分析失效选择器，输出候选方案
python3 scripts/find_selector.py /tmp/trace_output ".broken-selector"

# 搜索测试源码中的使用位置
python3 scripts/find_selector.py /tmp/trace_output ".broken-selector" \
    --fix /path/to/test/src

# 自动应用替换
python3 scripts/find_selector.py /tmp/trace_output ".broken-selector" \
    --fix /path/to/test/src \
    --apply ".new-selector"
```

选择器稳定性优先级：`data-testid` > `aria-label` > `:has-text()` > 稳定 CSS class > tag+text。CSS Module 哈希类名会被自动过滤。

## 环境要求

- Python 3.8+
- 无外部依赖（仅使用标准库）

## 工作原理

### parse_trace.py

- 读取解压后的 `trace.trace`（NDJSON）和 `trace.network`（NDJSON）
- 使用 `context-options` 中的基准值，将 Playwright 单调时钟转换为 wall clock
- 按时间窗口过滤事件（两端各留 1s 缓冲，应对时钟漂移）
- 将 Playwright VDOM 树（嵌套数组）转换为可读文本
- 自动检测 loading 状态、截图空白期、选择器失效和网络异常

### find_selector.py

- 解析失效选择器，提取语义线索（文本、标签、class、属性）
- 遍历 Playwright VDOM 树，收集所有已渲染元素
- 按文本匹配、标签匹配、class 重叠度和属性相似度为候选元素打分
- 生成按稳定性排序的替代选择器
- 可选搜索测试源码并通过字符串替换自动修复

## 开源协议

MIT
