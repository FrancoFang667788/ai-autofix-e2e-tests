# AI Autofix E2E Tests

Automatically analyze Playwright `trace.zip` files, diagnose E2E test failures, and auto-fix broken selectors in test code.

## What It Does

When a Playwright E2E test fails, you get a `trace.zip` — but manually digging through NDJSON events, DOM snapshots, screenshots, and network logs is painful. This tool automates the entire diagnosis workflow:

1. **Parse** the trace file, filtering by test case time window
2. **Static analysis** of error/stacktrace/logs to form initial hypothesis
3. **Extract evidence** — actions, failures, DOM snapshots, screenshots, network requests
4. **Visual confirmation** via key screenshots (first frame, before failure, last frame)
5. **Auto-fix broken selectors** — when DOM changes break your selectors, automatically find replacements from the actual DOM and patch your test files
6. **Structured root cause report** with timeline, evidence, and actionable fix suggestions

## Known Failure Patterns

| Pattern | Symptom | Root Cause |
|---------|---------|------------|
| **A: Navigation timeout** | `navigated to .../pending?redirect=...` | App uses intermediate pending page |
| **B: Loading stuck** | Long screenshot gaps, `loading-pane` in DOM | React app stuck in full-screen loading |
| **C: Selector mismatch** | DOM has content but target selector missing | CSS class / DOM structure changed |
| **D: API errors** | `status >= 400` in network requests | Backend returning errors |
| **E: No response** | `status: -1` in network requests | API request got no response |
| **F: Null PageModel** | `NullPointerException` on page object field | Selector didn't match, injection returned null |

## Project Structure

```
ai-autofix-e2e-tests/
├── SKILL.md                    # Claude Code skill definition (full 6-step workflow)
├── scripts/
│   ├── parse_trace.py          # Parse trace.zip into structured JSON report
│   └── find_selector.py        # Find replacement selectors & auto-fix test files
├── docs/
│   └── trace-reference.md      # Playwright trace.zip file format reference
└── evals/
    └── evals.json              # Evaluation test cases
```

## Usage

### As a Claude Code Skill

Copy the project into your `.claude/skills/` directory:

```bash
cp -r ai-autofix-e2e-tests /path/to/your/project/.claude/skills/playwright-trace-analysis
```

Then in Claude Code, invoke with:
```
/playwright-trace-analysis
```

Provide a trace.zip path or URL and the test failure info, and the skill will guide the full analysis.

### Standalone Scripts

#### Parse a trace

```bash
# Extract trace.zip first
unzip trace.zip -d /tmp/trace_output

# Parse with optional time window filtering
python3 scripts/parse_trace.py /tmp/trace_output \
    --start "2026-01-01T00:00:00Z" \
    --end   "2026-01-01T00:01:00Z"
```

Output is a JSON object with: `environment`, `actions`, `failures`, `logs`, `console_events`, `screencast_timeline`, `dom_at_failure`, `key_screenshots`, `network_requests`.

#### Find replacement selectors

```bash
# Analyze a broken selector against actual DOM
python3 scripts/find_selector.py /tmp/trace_output ".broken-selector"

# Search test source code for usages
python3 scripts/find_selector.py /tmp/trace_output ".broken-selector" \
    --fix /path/to/test/src

# Auto-apply a replacement
python3 scripts/find_selector.py /tmp/trace_output ".broken-selector" \
    --fix /path/to/test/src \
    --apply ".new-selector"
```

Selector stability priority: `data-testid` > `aria-label` > `:has-text()` > stable CSS class > tag+text. CSS Module hashed classes are automatically filtered out.

## Requirements

- Python 3.8+
- No external dependencies (stdlib only)

## How It Works

### parse_trace.py

- Reads `trace.trace` (NDJSON) and `trace.network` (NDJSON) from the extracted trace directory
- Converts Playwright's monotonic timestamps to wall clock using the `context-options` baseline
- Filters events by time window (with 1s buffer for clock drift)
- Converts Playwright VDOM trees (nested arrays) to readable text
- Detects loading states, screenshot gaps, selector mismatches, and network anomalies

### find_selector.py

- Parses broken selectors to extract semantic hints (text, tag, classes, attributes)
- Walks the Playwright VDOM tree to find all rendered elements
- Scores candidates by text match, tag match, class overlap, and attribute similarity
- Generates replacement selectors ranked by stability
- Optionally searches test source files and applies fixes via string replacement

## License

MIT
