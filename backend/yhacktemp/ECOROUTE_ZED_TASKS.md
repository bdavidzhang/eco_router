# EcoRoute — Zed Extension Task Breakdown

> **Project:** EcoRoute Zed Extension (Rust → WASM)
> **Repo:** `ecoroute-zed/`
> **Goal:** Ship a working `/eco` slash command that calls the backend and surfaces carbon-efficient model recommendations in Zed's agent panel.
> **Hackathon:** YHack 2025

---

## System Context

```
Autoresearch Agent (Python, DGX Spark)
└─ Collects SCI scores + BPB per model config
└─ Fits linear regression → efficiency frontier
└─ Stores results in combined_results.json

Backend API (FastAPI, Python)
└─ GET  /models/rankings  → efficiency leaderboard
└─ POST /tasks/score      → complexity + recommendation
└─ POST /session/log      → carbon ledger

Web Dashboard (React)
└─ SCI vs BPB scatter + regression line
└─ Live routing decision feed

Zed Extension (Rust → WASM) ← THIS DOCUMENT
└─ /eco slash command
└─ Calls POST /tasks/score
└─ Formats + injects recommendation
```

The autoresearch workbench (in `autoresearch-yaledgx/`) has already produced
real SCI data from runs on the DGX Spark. The backend serves this data.
The extension is the last-mile delivery into the developer's editor.

---

## Current State

The extension **compiles and loads** in Zed. The `/eco` command works but
returns a **hardcoded mock response**. All three files exist:

| File | Status |
|------|--------|
| `Cargo.toml` | ✅ Done — `zed_extension_api = 0.4.0` |
| `extension.toml` | ✅ Done — slash command + curl capability registered |
| `src/lib.rs` | ✅ Alpha complete — backend call, JSON parsing, fallback chain |

---

## Agent Assignment

### 🐺 Agent Alpha — Core Backend Integration

**Focus:** Make `/eco` actually talk to the backend and render real data.

**Claim ID:** Assign to your agent via coordination.

#### Task A1 — Replace Mock with Real `curl` Call

**Priority:** 🔴 P0 — Nothing works without this
**File:** `src/lib.rs`
**Estimated effort:** ~30 min

Replace the hardcoded string in `run_slash_command` with a real HTTP call
using `zed::process::Command` to shell out to `curl`:

```rust
fn run_slash_command(
    &self,
    _command: SlashCommand,
    _args: Vec<String>,
    worktree: Option<&zed::Worktree>,
) -> Result<SlashCommandOutput, String> {
    let output = zed::process::Command::new("curl")
        .args([
            "-s",
            "-X", "POST",
            "http://localhost:8000/tasks/score",
            "-H", "Content-Type: application/json",
            "-d", r#"{"task_type": "autocomplete", "context_size": 200}"#,
        ])
        .output()
        .map_err(|e| format!("Failed to call backend: {}", e))?;

    let response = String::from_utf8(output.stdout)
        .map_err(|e| format!("Invalid UTF-8 response: {}", e))?;

    format_recommendation(&response)
}
```

**Acceptance criteria:**
- [ ] `/eco` calls `POST /tasks/score` on `localhost:8000`
- [ ] Graceful error message if backend is unreachable
- [ ] Falls back to the hardcoded mock if curl fails (demo safety net)

#### Task A2 — Parse Backend JSON Response

**Priority:** 🔴 P0 — Needed to display real data
**File:** `src/lib.rs`
**Estimated effort:** ~45 min

Write `format_recommendation()` to parse this backend response:

```json
{
  "current_model": "gpt-4o",
  "current_sci": 41.2,
  "task_complexity": 2,
  "task_type": "autocomplete",
  "recommended_model": "claude-haiku",
  "recommended_sci": 3.1,
  "carbon_savings_pct": 92,
  "quality_confidence_pct": 96,
  "efficiency_score": 0.87,
  "above_frontier": false
}
```

**Constraints:**
- No `serde_json` — WASM binary size matters and it's a hackathon
- Use simple string matching / manual parsing (find `"key":`, extract value)
- Helper function: `fn extract_json_string(json: &str, key: &str) -> Option<String>`
- Helper function: `fn extract_json_number(json: &str, key: &str) -> Option<f64>`
- Helper function: `fn extract_json_bool(json: &str, key: &str) -> Option<bool>`

**Output format** (returned as `SlashCommandOutput`):

```
🌱 EcoRoute Recommendation
─────────────────────────────
Current model:   GPT-4o
SCI score:       41.2 gCO₂ / 1000 tokens

Task complexity: 2 / 10 (simple task detected)

Recommended:     Claude Haiku
SCI score:       3.1 gCO₂ / 1000 tokens
Carbon savings:  92% less carbon
Quality match:   96% for this task type
Efficiency:      0.87 (below frontier ✓)

→ Switch model: Settings > AI > Model
─────────────────────────────
Powered by EcoRoute · SCI Standard (Green Software Foundation)
```

**Acceptance criteria:**
- [ ] All 10 JSON fields are extracted and displayed
- [ ] `above_frontier: true` shows `⚠️ above frontier` instead of `✓`
- [ ] Malformed JSON returns a clear error, not a panic
- [ ] `format_recommendation()` is a pure function (testable in isolation)

#### Task A3 — Error Handling & Fallback

**Priority:** 🟡 P1 — Critical for demo reliability
**File:** `src/lib.rs`
**Estimated effort:** ~15 min

Implement a robust fallback chain:

```
1. Try curl → backend
2. If curl fails → return MOCK_RESPONSE (hardcoded)
3. If JSON parse fails → return partial data + "[parse error]" markers
4. Always return *something* — never let /eco produce an empty panel
```

**Why:** During the demo, the backend might crash. The mock must always
be ready as a safety net. The pitch still works with hardcoded data —
it just works *better* with live data.

**Acceptance criteria:**
- [ ] Backend down → shows mock with `⚠️ Using cached recommendation (backend unreachable)`
- [ ] Partial JSON → shows what it can, marks rest as `[unavailable]`
- [ ] Never returns `Err(...)` to Zed (always `Ok(SlashCommandOutput {...})`)

---

### 🦊 Agent Beta — Context Detection & Integration

**Focus:** Make the extension smart about *what* the developer is doing,
and wire up the stretch-goal MCP server.

**Claim ID:** Assign to your agent via coordination.

#### Task B1 — Context Detection from Worktree

**Priority:** 🟡 P1 — Upgrades recommendation quality significantly
**File:** `src/lib.rs`
**Estimated effort:** ~45 min

Instead of hardcoding `"task_type": "autocomplete"`, detect context from
the Zed worktree:

```rust
fn detect_context(worktree: Option<&zed::Worktree>) -> String {
    if let Some(wt) = worktree {
        // 1. Try to detect primary file extension
        //    → Maps to task_type heuristic:
        //      .rs, .go, .c       → "refactor" (systems code, complex)
        //      .py, .js, .ts      → "autocomplete" (scripting, simpler)
        //      .md, .txt           → "chat" (prose, low complexity)
        //      .toml, .yaml, .json → "config" (low complexity)
        //
        // 2. Use worktree file count as complexity proxy
        //    → < 10 files: context_size = 200
        //    → 10-50 files: context_size = 500
        //    → 50+ files: context_size = 1000
        //
        // 3. Read current file if possible (worktree.read_text_file)
        //    → Use line count as complexity signal
    }

    // Fallback
    r#"{"task_type": "general", "context_size": 500}"#.to_string()
}
```

**Heuristics table:**

| Signal | Source | Maps To |
|--------|--------|---------|
| File extension | `worktree.entries()` or args | `task_type` |
| File count | `worktree.entries().len()` | `context_size` |
| Line count | `worktree.read_text_file()` | `complexity_signals.loc` |
| Has tests? | Look for `test` in filenames | `complexity_signals.has_tests` |

**Acceptance criteria:**
- [ ] `.rs` files → sends `task_type: "refactor"` to backend
- [ ] `.md` files → sends `task_type: "chat"` to backend
- [ ] Context JSON is well-formed (valid JSON string)
- [ ] Missing worktree → falls back gracefully to defaults
- [ ] Function is pure — takes worktree ref, returns String

#### Task B2 — Slash Command Argument Parsing

**Priority:** 🟢 P2 — Nice UX improvement
**File:** `src/lib.rs` + `extension.toml`
**Estimated effort:** ~20 min

Allow users to override the auto-detected context:

```
/eco               → auto-detect context
/eco refactor      → force task_type = "refactor"
/eco debug         → force task_type = "debug"
/eco chat          → force task_type = "chat"
```

Update `extension.toml`:

```toml
[slash_commands.eco]
description = "Get a sustainability-optimized model recommendation (optional: refactor|debug|chat|autocomplete)"
requires_argument = false
```

Parse `args` in `run_slash_command`:

```rust
let task_type = args.first()
    .map(|a| a.as_str())
    .unwrap_or("auto");
```

**Acceptance criteria:**
- [ ] `/eco` with no args → auto-detect
- [ ] `/eco refactor` → overrides task_type
- [ ] Invalid args → ignored, falls back to auto-detect
- [ ] No changes needed to `requires_argument` (stays `false`)

#### Task B3 — MCP Server Registration (Stretch Goal)

**Priority:** 🔵 P3 — Stretch goal for extra demo wow-factor
**File:** `src/lib.rs` + `extension.toml`
**Estimated effort:** ~60 min

Register EcoRoute as an MCP (Model Context Protocol) server so Zed's AI
agent can call it **autonomously** — no `/eco` needed.

Add to `extension.toml`:

```toml
[context_servers.ecoroute-mcp]
name = "EcoRoute MCP"
```

Implement in `lib.rs`:

```rust
impl zed::Extension for EcoRouteExtension {
    // ... existing methods ...

    fn context_server_command(
        &self,
        _server_id: &zed::ContextServerId,
        _worktree: &zed::Worktree,
    ) -> Result<zed::Command, String> {
        Ok(zed::Command {
            command: "curl".to_string(),
            args: vec![
                "-s".to_string(),
                "http://localhost:8000/tasks/score".to_string(),
            ],
            env: vec![],
        })
    }
}
```

**Acceptance criteria:**
- [ ] MCP server appears in Zed's context server list
- [ ] Zed's AI agent can invoke EcoRoute without explicit `/eco`
- [ ] Doesn't break existing `/eco` slash command
- [ ] Graceful degradation if backend is down

#### Task B4 — Build Verification & Demo Prep

**Priority:** 🟡 P1 — Must pass before any PR
**Estimated effort:** ~20 min

```bash
# Verify clean build
cd ecoroute-zed
cargo build --release --target wasm32-wasip1

# Verify extension loads in Zed
# Cmd+Shift+P → "zed: extensions" → Install Dev Extension → select ecoroute-zed/

# Verify /eco appears in agent panel
# Cmd+? → type /eco → enter

# Verify with backend running
cd ../backend && python main.py &
# Then /eco in Zed → should show real data

# Verify fallback (kill backend)
kill %1
# Then /eco in Zed → should show mock data with warning
```

**Acceptance criteria:**
- [ ] `cargo build --release --target wasm32-wasip1` → no errors, no warnings
- [ ] Extension loads in Zed without errors in log (`zed --foreground`)
- [ ] `/eco` returns output in both backend-up and backend-down scenarios
- [ ] WASM binary size < 5 MB (keep it lean)

---

## Dependency Graph

```
A1 (curl call) ──────┐
                      ├──▶ A3 (fallback) ──▶ B4 (build verify)
A2 (JSON parse) ─────┘         │
                               │
B1 (context detect) ──▶ B2 (arg parse) ──▶ B4 (build verify)
                                              │
B3 (MCP server) ─────────────────────────────▶ B4 (build verify)
```

**Critical path:** A1 → A2 → A3 → B4
**Parallel path:** B1 → B2 (can run alongside Alpha's tasks)
**Independent:** B3 (stretch — only if time permits)

---

## Backend API Contract (Reference)

### POST /tasks/score

**Request:**
```json
{
  "task_type": "autocomplete | refactor | chat | debug | config | general",
  "context_size": 200,
  "file_extension": "rs",
  "complexity_signals": {
    "loc": 150,
    "has_tests": true
  }
}
```

**Response:**
```json
{
  "current_model": "gpt-4o",
  "current_sci": 41.2,
  "task_complexity": 2,
  "task_type": "autocomplete",
  "recommended_model": "claude-haiku",
  "recommended_sci": 3.1,
  "carbon_savings_pct": 92,
  "quality_confidence_pct": 96,
  "efficiency_score": 0.87,
  "above_frontier": false
}
```

### GET /models/rankings

**Response:**
```json
[
  {
    "model": "claude-haiku",
    "sci_score": 3.1,
    "bpb_score": 0.82,
    "efficiency_score": 0.87,
    "above_frontier": false,
    "quality_tier": 7
  }
]
```

---

## SCI Formula (Quick Reference)

```
SCI = (E × I) + M  per R

E = Energy consumed (kWh)         ← nvidia-smi on DGX Spark
I = Grid carbon intensity (gCO₂/kWh) ← ElectricityMaps / region config
M = Embodied hardware carbon (gCO₂)  ← GPU lifecycle amortization
R = Functional unit               ← per 1000 tokens
```

**BPB** (bits per byte) = compression-based quality proxy. Lower = smarter model.

The efficiency frontier is the regression line through SCI vs BPB.
Models **below** the line are carbon-efficient for their quality tier.
Models **above** it are wasteful relative to what they deliver.

Real data from the DGX Spark autoresearch runs:

| Model Config | SCI (gCO₂/tok) | BPB | Pareto? |
|-------------|:---------------:|:---:|:-------:|
| Qwen3.5-0.8B batch=8 seq=2048 | 0.000326 | 1.57 | ★ |
| Qwen3.5-0.8B batch=1 seq=512 | 0.000384 | 4.07 | — |
| Qwen3.5-0.8B batch=8 seq=1024 | 0.000933 | 1.57 | — |

---

## Environment Setup (Both Agents)

```bash
# 1. Install Rust (NOT via homebrew)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# 2. Add WASM target
rustup target add wasm32-wasip1

# 3. Build
cd ecoroute-zed
cargo build --release --target wasm32-wasip1

# 4. Install dev extension in Zed
# Cmd+Shift+P → "zed: extensions" → Install Dev Extension → select ecoroute-zed/

# 5. Test
# Open agent panel (Cmd+?) → type /eco → enter

# 6. Debug
zed --foreground    # see println! output
# Cmd+Shift+P → "zed: open log"   # see extension load errors
```

**Common pitfalls:**
- `wasm32-wasi` is wrong → use `wasm32-wasip1`
- `process::exec not allowed` → check `[[capabilities]]` in `extension.toml`
- Slash command missing → verify `[slash_commands.eco]` in `extension.toml`
- API version drift → `cargo search zed_extension_api` and update `Cargo.toml`

---

## Demo Script (What We're Building Toward)

1. Developer opens Zed, starts coding a Rust file
2. Types `/eco` in the agent panel
3. Extension detects `.rs` file → sends `task_type: "refactor"` to backend
4. Backend returns recommendation: "Switch to Claude Haiku — 92% less carbon"
5. Recommendation renders beautifully in the agent panel
6. Developer switches model in settings
7. Web dashboard shows the routing decision logged in real time
8. Session carbon counter updates

**The pitch line:**
> *"Every time a developer hits tab, they're making a carbon decision they
> don't know they're making — we make that decision for them."*

**Demo safety net:** If backend is down, the mock response still tells a
compelling story. Always have this fallback ready.

---

*Built for YHack 2025 — because sustainability is the new frontier.*
*SCI = (E × I) + M. You can't optimize what you don't measure.* 🌱
