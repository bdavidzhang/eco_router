# EcoRoute MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that surfaces carbon-efficiency recommendations directly inside **Zed's agent panel**. The AI agent calls EcoRoute tools autonomously to find the most carbon-efficient model for each coding task — auto-detecting which model is currently active in Zed.

---

## Why MCP instead of a slash command?

Zed's **agent panel** (powered by the MCP architecture) does not support the older slash-command extension API. This MCP server works natively with the agent panel — the AI calls the tools autonomously as part of its reasoning loop.

---

## Architecture

```
Zed agent panel
      │  MCP stdio transport
      ▼
ecoroute-mcp/server.py
      │  HTTP  localhost:8000
      ▼
backend/main.py  (FastAPI)
      │
      ├── POST /tasks/score       → carbon-efficient model recommendation
      ├── GET  /models/rankings   → all models sorted by efficiency
      └── GET  /health            → backend status + model count
```

- **Transport:** stdio — the MCP spec standard, required by Zed.
- **Model auto-detection:** `server.py` reads `~/.config/zed/settings.json` at call time to detect the active Zed model automatically. No manual configuration needed.
- **Backend required:** Unlike the previous version, there is no hardcoded mock fallback. The FastAPI backend (`backend/main.py`) must be running for the tools to work.

---

## Setup

### 1. Create the virtual environment

```bash
cd /path/to/yhacktemp/ecoroute-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> **Why a venv?** macOS ships with a Homebrew-managed Python that blocks system-wide `pip` installs (PEP 668). The venv is self-contained and keeps your system Python untouched.

### 2. Start the FastAPI backend

The backend lives in a separate directory. Run it from `backend/`:

```bash
cd /path/to/yhacktemp/backend
/path/to/yhacktemp/ecoroute-mcp/.venv/bin/uvicorn main:app --reload --port 8000
```

Keep this terminal open. The backend must be running before Zed starts calling tools.

You can verify it is up:

```bash
curl http://localhost:8000/health
# {"status":"ok","models_loaded":14}
```

### 3. Register the server in Zed

Zed reads settings from `~/.config/zed/settings.json`. Add the `context_servers` block:

```json
{
  "context_servers": {
    "ecoroute": {
      "command": "/path/to/yhacktemp/ecoroute-mcp/.venv/bin/python3",
      "args": [
        "/path/to/yhacktemp/ecoroute-mcp/server.py"
      ]
    }
  }
}
```

> Replace `/path/to/yhacktemp` with the actual path on your machine.  
> Run `pwd` inside `ecoroute-mcp/` to get the base path quickly.

### 4. Reload Zed

After saving `settings.json`, reload the window (`Cmd+Shift+P` → `Reload Window`).  
The three EcoRoute tools will appear in the agent panel's tool list.

---

## Tools

### `score_task`

Recommends the most carbon-efficient model that meets quality requirements for the given coding task. **Auto-detects the active Zed model** from `~/.config/zed/settings.json` — no need to pass `current_model` manually.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `task_type` | string | ✅ | — | One of: `autocomplete`, `chat`, `debug`, `refactor` |
| `context_size` | integer | ❌ | `0` | Approximate token count of the current context |
| `current_model` | string | ❌ | auto-detected | Override the current model (e.g. `claude-sonnet-4-6`) |

**Example prompt:**

> *"Before you answer, use score_task to check the most carbon-efficient model for this refactoring task."*

**Example output:**

```json
{
  "current_model": "gemini-3-flash",
  "current_sci": 0.00137,
  "task_type": "chat",
  "task_complexity": 2,
  "recommended_model": "claude-haiku-4-5",
  "recommended_sci": 0.00072,
  "carbon_savings_pct": 47,
  "quality_confidence_pct": 99,
  "efficiency_score": 1.0,
  "zed_active_model": "gemini-3-flash"
}
```

---

### `get_model_rankings`

Returns all Zed-configured models sorted by efficiency score (best first). No parameters required.

Each entry includes: `model`, `sci_per_token`, `quality_tier`, `efficiency_score`.

**Example prompt:**

> *"Use get_model_rankings to show me all available models sorted by carbon efficiency."*

---

### `health_check`

Checks whether the EcoRoute backend is running and how many models are loaded. No parameters required.

**Example output:**

```json
{
  "status": "ok",
  "models_loaded": 14
}
```

---

## SCI values

SCI (Software Carbon Intensity) values are in **gCO₂ per token**, derived from:

```
SCI = (gpu_count × tdp_w / tokens_per_sec / 3_600_000) × grid_intensity × PUE + embodied
```

where:
- `grid_intensity` = 400 gCO₂/kWh (US average)
- `PUE` = 1.3 (data center overhead)
- `embodied` = 0.00003 gCO₂/token

---

## File structure

```
yhacktemp/
├── backend/
│   ├── main.py            # FastAPI backend — must be running on port 8000
│   └── requirements.txt   # fastapi, uvicorn, numpy, scikit-learn, pandas
│
└── ecoroute-mcp/
    ├── server.py          # MCP server — registered in Zed settings
    ├── requirements.txt   # mcp, httpx, anyio, fastapi, uvicorn, numpy, scikit-learn, pandas
    ├── .venv/             # local virtual environment (created during setup)
    └── README.md          # this file
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Tools do not appear in agent panel | Confirm both absolute paths in `settings.json` are correct; reload Zed |
| `ModuleNotFoundError: mcp` | Make sure `"command"` points to `.venv/bin/python3`, not the system `python3` |
| Tool call fails / connection error | Start the FastAPI backend: `uvicorn main:app --reload --port 8000` from `backend/` |
| `zed_active_model` returns `null` | Ensure `~/.config/zed/settings.json` exists and has `agent.default_model.model` set |
| Venv missing after clone | Re-run `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` |
| Wrong Python version | Check with `.venv/bin/python3 --version`; requires Python 3.10+ |

---

*Built at YHack 2025 — making every token a carbon decision.*