# Trace 文件结构 Reference

说明 Playwright `trace.zip` 解压后的目录结构、各文件格式及字段含义，供 agent 解析时参考。

---

## 目录结构

```
trace.zip
├── trace.trace       # 主事件流（NDJSON，每行一个 JSON 对象）
├── trace.network     # 网络请求流（NDJSON，每行一个 JSON 对象）
└── resources/        # 静态资源缓存（以 SHA1 为文件名）
    ├── <sha1>.jpeg   # 截图帧
    ├── <sha1>.json   # API 响应体
    ├── <sha1>.html   # 服务端初始 HTML
    ├── <sha1>.js     # JS 文件
    ├── <sha1>.css    # CSS 文件
    ├── <sha1>.woff2  # 字体文件
    ├── <sha1>.txt    # 文本资源
    ├── <sha1>.svg    # SVG 图标
    └── <sha1>.dat    # 二进制资源（通常为空文件占位）
```

---

## trace.trace

主事件流文件，NDJSON 格式，每行一个完整 JSON 对象。包含以下事件类型：

| type | 说明 |
|---|---|
| `context-options` | BrowserContext 初始化参数，**固定为首行** |
| `before` | 一个 Playwright API 调用开始 |
| `after` | 一个 Playwright API 调用结束 |
| `log` | Playwright 内部日志条目，与 action 关联 |
| `frame-snapshot` | DOM 快照，每个 action 的 before/after 各一个 |
| `screencast-frame` | 截图帧 |
| `console` | 页面 `console.log/warn/error` 输出 |
| `input` | 键鼠输入事件 |
| `event` | 页面生命周期事件（如 load、DOMContentLoaded） |

---

### context-options（首行）

文件第一行，记录整个 trace 的环境信息。解析时优先读取此行获取时间基准。

```json
{
  "type": "context-options",
  "browserName": "chromium",
  "playwrightVersion": "1.x.x",
  "options": {
    "viewport": { "width": 1280, "height": 800 },
    "baseURL": "https://example.com",
    "testIdAttributeName": "data-testid"
  },
  "platform": "linux",
  "wallTime": 1234567890000,
  "monotonicTime": 12345.678,
  "sdkLanguage": "java"
}
```

关键字段：
- `wallTime`：trace 开始的 Unix 时间戳（毫秒）
- `monotonicTime`：与 `wallTime` 对应的单调时钟基准值（毫秒）
- wall clock 换算公式：`wall_time_ms = wallTime + (mono - monotonicTime)`
- `sdkLanguage`：决定 locator 日志的语法风格（`java` / `js` / `python` 等）

---

### before / after（action 边界）

每个 Playwright API 调用对应一对 `before`/`after`，通过 `callId` 关联。

**before：**
```json
{
  "type": "before",
  "callId": "call@<n>",
  "startTime": 12345.0,
  "class": "Frame",
  "method": "waitForSelector",
  "params": {
    "selector": ".target-element",
    "timeout": 30000
  }
}
```

**after：**
```json
{
  "type": "after",
  "callId": "call@<n>",
  "endTime": 12375.0,
  "error": null
}
```

关键字段：
- `callId`：全局唯一，格式 `call@<数字>`，贯穿 before/after/log/frame-snapshot
- `startTime` / `endTime`：单调时钟（毫秒），需用 `context-options` 基准换算为 wall clock
- `error`：失败时为 `{"name": "TimeoutError", "message": "..."}`，成功时为 `null`

---

### log

Playwright 内部日志，通过 `callId` 与 action 关联。同一个 `callId` 通常有多条 log，按时间顺序记录操作进度。

```json
{
  "type": "log",
  "callId": "call@<n>",
  "time": 12346.0,
  "message": "waiting for locator(...) to be visible"
}
```

- `time`：单调时钟（毫秒）
- 失败时，最后一条 log 的 `message` 通常描述 `waiting for...` 的目标，是定位根因的关键线索

---

### frame-snapshot

DOM 快照，每个 action 执行前后各生成一个，通过 `snapshotName` 区分。

```json
{
  "type": "frame-snapshot",
  "snapshot": {
    "callId": "call@<n>",
    "snapshotName": "before@call@<n>",
    "pageId": "page@<hash>",
    "frameId": "frame@<hash>",
    "frameUrl": "https://example.com/some/path",
    "html": ["..."],
    "viewport": { "width": 1280, "height": 800 },
    "timestamp": 12345.0,
    "wallTime": 1234567890000,
    "resourceOverrides": [],
    "isMainFrame": true
  }
}
```

关键字段：
- `snapshotName`：格式为 `before@<callId>` 或 `after@<callId>`
- `html`：Playwright VDOM 嵌套数组（非标准 HTML），`parse_trace.py` 会将其转换为可读文本
- `wallTime`：已是毫秒级 wall clock，无需换算

---

### screencast-frame

截图帧，`sha1` 直接对应 `resources/` 目录下的 JPEG 文件名。

```json
{
  "type": "screencast-frame",
  "pageId": "page@<hash>",
  "sha1": "page@<hash>-<wallTime_ms>.jpeg",
  "width": 1280,
  "height": 800,
  "timestamp": 12345.0,
  "frameSwapWallTime": 1234567890000.0
}
```

- `sha1`：`resources/` 目录下的完整文件名
- `frameSwapWallTime`：wall clock 时间（毫秒），精度优于 `timestamp` 换算

---

### console

页面 console 输出。

```json
{
  "type": "console",
  "time": 12345.0,
  "pageId": "page@<hash>",
  "messageType": "error",
  "message": "Uncaught TypeError: ...",
  "args": []
}
```

- `messageType`：`"log"` / `"warn"` / `"error"` / `"info"`

---

### event

页面生命周期事件。

```json
{
  "type": "event",
  "time": 12345.0,
  "class": "Page",
  "method": "load",
  "params": { "pageId": "page@<hash>" }
}
```

---

## trace.network

网络请求文件，NDJSON 格式，每行一个 `resource-snapshot` 事件，遵循 [HAR 1.2](http://www.softwareishard.com/blog/har-12-spec/) 规范。

```json
{
  "type": "resource-snapshot",
  "snapshot": {
    "pageref": "page@<hash>",
    "startedDateTime": "2026-01-01T00:00:00.000Z",
    "time": 100.0,
    "request": {
      "method": "GET",
      "url": "https://example.com/api/some/endpoint",
      "httpVersion": "HTTP/2.0",
      "cookies": [],
      "headers": [],
      "queryString": [],
      "bodySize": 0
    },
    "response": {
      "status": 200,
      "statusText": "",
      "headers": [],
      "content": {
        "size": 1024,
        "mimeType": "application/json",
        "_sha1": "<sha1>.json"
      },
      "bodySize": 512,
      "redirectURL": ""
    },
    "timings": {
      "dns": -1,
      "connect": -1,
      "ssl": -1,
      "send": 0,
      "wait": 80.0,
      "receive": 20.0
    },
    "_monotonicTime": 12345.0
  }
}
```

关键字段：
- `startedDateTime`：ISO 8601 UTC 时间，直接可用，无需换算
- `time`：请求总耗时（毫秒）
- `response.content._sha1`：响应体缓存文件名，在 `resources/<sha1>` 中查找（仅部分响应有缓存）
- `_monotonicTime`：单调时钟，用于与 `trace.trace` 事件做时间对齐
- `timings.dns/connect/ssl` 为 `-1` 表示连接复用（HTTP/2 持久连接）

---

## resources/

以内容 SHA1 为文件名的静态资源缓存目录。

| 扩展名 | 内容 | 引用来源 |
|---|---|---|
| `.jpeg` | 截图帧 | `trace.trace` 中 `screencast-frame` 事件的 `sha1` 字段 |
| `.json` | API 响应体（已解压） | `trace.network` 中 `resource-snapshot` 的 `response.content._sha1` |
| `.html` | 服务端初始 HTML | 页面导航时的初始响应 |
| `.js` / `.css` / `.woff2` / `.txt` / `.svg` / `.dat` | 页面加载的静态资源 | `trace.network` 中的静态资源响应 |

**注意**：`.html` 是服务端返回的框架入口 shell，不是 React 渲染后的 DOM。渲染后的 DOM 快照在 `trace.trace` 的 `frame-snapshot` 事件中。
