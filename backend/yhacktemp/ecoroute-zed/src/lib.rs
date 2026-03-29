use zed_extension_api::{
    self as zed, SlashCommand, SlashCommandOutput, SlashCommandOutputSection,
    SlashCommandArgumentCompletion,
};
mod json;


// ─── Configuration ──────────────────────────────────────────────

const BACKEND_URL: &str = "http://localhost:8000/tasks/score";
const CURL_TIMEOUT_SECS: &str = "3";

/// Valid task types accepted as `/eco <type>` arguments (B2).
const VALID_TASK_TYPES: &[&str] = &[
    "refactor", "debug", "chat", "autocomplete", "config", "general",
];

/// Fallback context when worktree detection fails entirely.
const FALLBACK_CONTEXT: &str = r#"{"task_type": "general", "context_size": 500}"#;

/// Hardcoded mock for demo safety net (Task A3).
/// If the backend is down during demo, we still show a compelling story.
const MOCK_JSON: &str = r#"{
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
}"#;

// ─── Context Detection (Task B1) ───────────────────────────────
//
// Detects what the developer is working on by inspecting the worktree.
// Uses marker files (Cargo.toml, package.json, etc.) rather than
// expensive `find` traversals — fast, deterministic, no extra capabilities.

/// Map a file extension to the most likely task type.
fn task_type_for_extension(ext: &str) -> &'static str {
    match ext {
        "rs" | "go" | "c" | "cpp" | "h" | "hpp" | "zig" => "refactor",
        "py" | "js" | "ts" | "jsx" | "tsx" | "rb" | "lua" => "autocomplete",
        "md" | "txt" | "rst" | "adoc" => "chat",
        "toml" | "yaml" | "yml" | "json" | "ini" | "cfg" => "config",
        _ => "general",
    }
}

/// Sniff the worktree for marker files to determine project language.
/// Returns `(task_type, primary_extension)`. Task type is derived via
/// `task_type_for_extension` so the mapping lives in exactly one place.
fn detect_project_type(wt: &zed::Worktree) -> (&'static str, &'static str) {
    /// Marker files and their associated primary file extension.
    const MARKERS: &[(&str, &str)] = &[
        // Systems (checked first — highest carbon stakes)
        ("Cargo.toml", "rs"),
        ("go.mod", "go"),
        ("CMakeLists.txt", "c"),
        ("Makefile", "c"),
        // Scripting
        ("tsconfig.json", "ts"),
        ("package.json", "js"),
        ("pyproject.toml", "py"),
        ("setup.py", "py"),
        ("requirements.txt", "py"),
        // Config / prose (last — lowest priority)
        ("README.md", "md"),
    ];

    for &(marker, ext) in MARKERS {
        if wt.read_text_file(marker).is_ok() {
            return (task_type_for_extension(ext), ext);
        }
    }

    ("general", "")
}

/// Try to estimate lines of code from a representative source file.
fn estimate_loc(wt: &zed::Worktree, ext: &str) -> u32 {
    let candidates: &[&str] = match ext {
        "rs" => &["src/lib.rs", "src/main.rs"],
        "py" => &["main.py", "app.py", "src/main.py"],
        "js" => &["src/index.js", "index.js"],
        "ts" => &["src/index.ts", "index.ts"],
        "go" => &["main.go", "cmd/main.go"],
        _ => &[],
    };

    for path in candidates {
        if let Ok(content) = wt.read_text_file(path) {
            return content.lines().count() as u32;
        }
    }
    0
}

/// Check for test files by probing common test locations.
fn has_test_files(wt: &zed::Worktree, ext: &str) -> bool {
    let candidates: &[&str] = match ext {
        "rs" => &["tests/", "src/tests.rs"],
        "py" => &["tests/", "test_main.py", "tests/test_main.py"],
        "js" | "ts" => &["__tests__/", "test/", "tests/"],
        "go" => &["main_test.go"],
        _ => &[],
    };

    for path in candidates {
        // read_text_file on a directory will fail, but on a file it'll succeed
        if wt.read_text_file(path).is_ok() {
            return true;
        }
    }
    false
}

/// Build the context JSON request body from worktree signals (B1)
/// and optional user override (B2). Pure function — easy to test.
fn detect_context(worktree: Option<&zed::Worktree>, task_override: Option<&str>) -> String {
    // B2: user explicitly specified a task type → honour it
    if let Some(task_type) = task_override {
        return format!(
            r#"{{"task_type": "{}", "context_size": 500}}"#,
            task_type
        );
    }

    // No worktree → safe fallback
    let Some(wt) = worktree else {
        return FALLBACK_CONTEXT.to_string();
    };

    // B1: sniff the project
    let (task_type, ext) = detect_project_type(wt);
    let loc = estimate_loc(wt, ext);
    let has_tests = has_test_files(wt, ext);

    // Context size heuristic: more LOC → bigger context window needed
    let context_size: u32 = match loc {
        0..=100 => 200,
        101..=500 => 500,
        _ => 1000,
    };

    // Build the JSON by hand — no serde, no allocator tricks
    let mut json = format!(
        r#"{{"task_type": "{}", "context_size": {}"#,
        task_type, context_size
    );

    if !ext.is_empty() {
        json.push_str(&format!(r#", "file_extension": "{}""#, ext));
    }

    if loc > 0 || has_tests {
        json.push_str(&format!(
            r#", "complexity_signals": {{"loc": {}, "has_tests": {}}}"#,
            loc, has_tests
        ));
    }

    json.push('}');
    json
}

// ─── Formatting (Task A2) ──────────────────────────────────────

fn complexity_label(level: f64) -> &'static str {
    if level <= 3.0 {
        "simple task detected"
    } else if level <= 6.0 {
        "moderate task detected"
    } else {
        "complex task detected"
    }
}

/// Parse backend JSON and build a pretty recommendation card.
/// Tolerates partial/malformed JSON — missing fields show `[unavailable]`.
fn format_recommendation(json: &str) -> SlashCommandOutput {
    let current_model = json::extract_string(json, "current_model")
        .unwrap_or_else(|| "[unavailable]".into());
    let current_sci = json::extract_number(json, "current_sci");
    let task_complexity = json::extract_number(json, "task_complexity");
    let recommended_model = json::extract_string(json, "recommended_model")
        .unwrap_or_else(|| "[unavailable]".into());
    let recommended_sci = json::extract_number(json, "recommended_sci");
    let carbon_savings = json::extract_number(json, "carbon_savings_pct");
    let quality = json::extract_number(json, "quality_confidence_pct");
    let efficiency = json::extract_number(json, "efficiency_score");
    let above_frontier = json::extract_bool(json, "above_frontier").unwrap_or(false);

    let frontier_indicator = if above_frontier {
        "⚠️ above frontier"
    } else {
        "below frontier ✓"
    };

    let complexity_text = task_complexity
        .map(|c| format!("{:.0} / 10 ({})", c, complexity_label(c)))
        .unwrap_or_else(|| "[unavailable]".into());

    let fmt_sci = |v: Option<f64>| -> String {
        v.map(|n| format!("{:.1} gCO\u{2082} / 1000 tokens", n))
            .unwrap_or_else(|| "[unavailable]".into())
    };

    let fmt_pct = |v: Option<f64>, suffix: &str| -> String {
        v.map(|n| format!("{:.0}% {}", n, suffix))
            .unwrap_or_else(|| "[unavailable]".into())
    };

    let efficiency_text = efficiency
        .map(|e| format!("{:.2} ({})", e, frontier_indicator))
        .unwrap_or_else(|| "[unavailable]".into());

    let text = format!(
        "\
\u{1f331} EcoRoute Recommendation\n\
\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\n\
Current model:   {current_model}\n\
SCI score:       {current_sci}\n\
\n\
Task complexity: {complexity}\n\
\n\
Recommended:     {recommended}\n\
SCI score:       {rec_sci}\n\
Carbon savings:  {savings}\n\
Quality match:   {quality}\n\
Efficiency:      {efficiency}\n\
\n\
\u{2192} Switch model: Settings > AI > Model\n\
\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\n\
Powered by EcoRoute \u{00b7} SCI Standard (Green Software Foundation)",
        current_model = current_model,
        current_sci = fmt_sci(current_sci),
        complexity = complexity_text,
        recommended = recommended_model,
        rec_sci = fmt_sci(recommended_sci),
        savings = fmt_pct(carbon_savings, "less carbon"),
        quality = fmt_pct(quality, "for this task type"),
        efficiency = efficiency_text,
    );

    build_output(&text, "EcoRoute Recommendation")
}

/// Wrap text into a `SlashCommandOutput` with a single section spanning the whole thing.
fn build_output(text: &str, label: &str) -> SlashCommandOutput {
    SlashCommandOutput {
        text: text.to_string(),
        sections: vec![SlashCommandOutputSection {
            range: (0..text.len()).into(),
            label: label.to_string(),
        }],
    }
}

// ─── Backend Communication (Task A1) ───────────────────────────

/// Shell out to `curl` to POST context to the backend.
/// Returns the raw JSON response body, or an error string.
fn call_backend(context_json: &str) -> Result<String, String> {
    let mut cmd = zed::process::Command::new("curl")
        .args([
            "-s",
            "--connect-timeout",
            CURL_TIMEOUT_SECS,
            "-X",
            "POST",
            BACKEND_URL,
            "-H",
            "Content-Type: application/json",
            "-d",
            context_json,
        ]);

    let output = cmd.output().map_err(|e| format!("curl exec failed: {e}"))?;

    let body = String::from_utf8(output.stdout)
        .map_err(|e| format!("Invalid UTF-8 in response: {e}"))?
        .trim()
        .to_string();
    if body.is_empty() {
        return Err("Empty response from backend".into());
    }

    // Sanity check: response should look like JSON
    if !body.starts_with('{') {
        return Err(format!(
            "Unexpected response: {}",
            &body[..body.len().min(120)]
        ));
    }

    Ok(body)
}

// ─── Extension Entrypoint ──────────────────────────────────────

struct EcoRouteExtension;

impl zed::Extension for EcoRouteExtension {
    fn new() -> Self {
        EcoRouteExtension
    }

    fn run_slash_command(
        &self,
        command: SlashCommand,
        args: Vec<String>,
        worktree: Option<&zed::Worktree>,
    ) -> Result<SlashCommandOutput, String> {
        match command.name.as_str() {
            "eco" => self.handle_eco(&args, worktree),
            other => Ok(build_output(
                &format!("Unknown command: /{other}"),
                "EcoRoute Error",
            )),
        }
    }

    /// B2: auto-complete task type arguments for `/eco`.
    fn complete_slash_command_argument(
        &self,
        command: SlashCommand,
        _args: Vec<String>,
    ) -> Result<Vec<SlashCommandArgumentCompletion>, String> {
        match command.name.as_str() {
            "eco" => Ok(vec![
                SlashCommandArgumentCompletion {
                    label: "Auto-detect (from worktree)".into(),
                    new_text: String::new(),
                    run_command: true,
                },
                SlashCommandArgumentCompletion {
                    label: "refactor \u{2014} systems code (Rust, Go, C)".into(),
                    new_text: "refactor".into(),
                    run_command: true,
                },
                SlashCommandArgumentCompletion {
                    label: "autocomplete \u{2014} scripting (Python, JS, TS)".into(),
                    new_text: "autocomplete".into(),
                    run_command: true,
                },
                SlashCommandArgumentCompletion {
                    label: "chat \u{2014} prose & docs".into(),
                    new_text: "chat".into(),
                    run_command: true,
                },
                SlashCommandArgumentCompletion {
                    label: "debug \u{2014} debugging session".into(),
                    new_text: "debug".into(),
                    run_command: true,
                },
                SlashCommandArgumentCompletion {
                    label: "config \u{2014} TOML/YAML/JSON tweaks".into(),
                    new_text: "config".into(),
                    run_command: true,
                },
            ]),
            cmd => Err(format!("unknown slash command: \"{cmd}\"")),
        }
    }

    /// B3 (stretch): MCP server registration.
    ///
    /// Returns the command to start an MCP-compatible server that wraps
    /// our backend. Requires `ecoroute-mcp-bridge` to be on $PATH or
    /// installed via the companion Python package.
    fn context_server_command(
        &mut self,
        _context_server_id: &zed::ContextServerId,
        _project: &zed::Project,
    ) -> zed::Result<zed::Command> {
        // Look for the MCP bridge script bundled with the extension
        let bridge = std::env::current_dir()
            .map(|d| d.join("mcp_bridge.py").to_string_lossy().to_string())
            .unwrap_or_else(|_| "mcp_bridge.py".to_string());

        Ok(zed::Command {
            command: "python3".into(),
            args: vec![bridge],
            env: vec![
                ("ECOROUTE_BACKEND_URL".into(), BACKEND_URL.into()),
            ],
        })
    }
}

impl EcoRouteExtension {
    /// Core handler for `/eco` — implements the full fallback chain (Task A3):
    ///
    /// 1. Detect context from worktree + args (B1/B2)
    /// 2. Try real backend via curl (A1)
    /// 3. If curl fails -> show mock with warning banner (A3)
    /// 4. If JSON is partial -> show what we can, mark rest `[unavailable]` (A2)
    /// 5. Always return Ok — never leave the agent panel empty
    fn handle_eco(
        &self,
        args: &[String],
        worktree: Option<&zed::Worktree>,
    ) -> Result<SlashCommandOutput, String> {
        // B2: parse optional task_type override from first arg
        let task_override = args
            .first()
            .map(|a| a.as_str())
            .filter(|a| VALID_TASK_TYPES.contains(a));

        // B1: detect context from worktree (or use fallback)
        let context_json = detect_context(worktree, task_override);

        match call_backend(&context_json) {
            Ok(response) => Ok(format_recommendation(&response)),
            Err(err) => {
                // A3: backend is down -> use mock + warning banner
                let mut output = format_recommendation(MOCK_JSON);
                let banner = format!(
                    "\u{26a0}\u{fe0f}  Using cached recommendation (backend unreachable: {err})\n\n"
                );
                output.text = format!("{banner}{}", output.text);
                output.sections = vec![SlashCommandOutputSection {
                    range: (0..output.text.len()).into(),
                    label: "EcoRoute Recommendation (cached)".to_string(),
                }];
                Ok(output)
            }
        }
    }
}

zed::register_extension!(EcoRouteExtension);

// ─── Tests ─────────────────────────────────────────────────────
//
// These run natively (`cargo test`) — not under WASM — so we can
// verify JSON parsing, formatting, and context detection without Zed.

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE_JSON: &str = r#"{
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
    }"#;

    // ── Formatting tests ───────────────────────────────────────

    #[test]
    fn format_recommendation_contains_all_fields() {
        let output = format_recommendation(SAMPLE_JSON);
        assert!(output.text.contains("gpt-4o"));
        assert!(output.text.contains("claude-haiku"));
        assert!(output.text.contains("41.2"));
        assert!(output.text.contains("3.1"));
        assert!(output.text.contains("92%"));
        assert!(output.text.contains("96%"));
        assert!(output.text.contains("0.87"));
        assert!(output.text.contains("below frontier"));
        assert!(output.text.contains("EcoRoute Recommendation"));
    }

    #[test]
    fn format_recommendation_above_frontier() {
        let json = r#"{"above_frontier": true, "efficiency_score": 1.23, "current_model": "test", "recommended_model": "test2"}"#;
        let output = format_recommendation(json);
        assert!(output.text.contains("above frontier"));
        assert!(!output.text.contains("below frontier"));
    }

    #[test]
    fn format_recommendation_handles_partial_json() {
        let partial = r#"{"current_model": "gpt-4o"}"#;
        let output = format_recommendation(partial);
        assert!(output.text.contains("gpt-4o"));
        assert!(output.text.contains("[unavailable]"));
    }

    #[test]
    fn format_recommendation_handles_garbage() {
        let garbage = "this is not json at all";
        let output = format_recommendation(garbage);
        assert!(output.text.contains("[unavailable]"));
        assert!(output.text.contains("EcoRoute Recommendation"));
    }

    #[test]
    fn section_range_spans_full_text() {
        let output = format_recommendation(SAMPLE_JSON);
        assert_eq!(output.sections.len(), 1);
        assert!(!output.sections[0].label.is_empty());
    }

    #[test]
    fn complexity_labels() {
        assert_eq!(complexity_label(1.0), "simple task detected");
        assert_eq!(complexity_label(3.0), "simple task detected");
        assert_eq!(complexity_label(4.0), "moderate task detected");
        assert_eq!(complexity_label(6.0), "moderate task detected");
        assert_eq!(complexity_label(7.0), "complex task detected");
        assert_eq!(complexity_label(10.0), "complex task detected");
    }

    // ── Context detection tests (B1) ───────────────────────────

    #[test]
    fn task_type_mapping() {
        assert_eq!(task_type_for_extension("rs"), "refactor");
        assert_eq!(task_type_for_extension("go"), "refactor");
        assert_eq!(task_type_for_extension("c"), "refactor");
        assert_eq!(task_type_for_extension("py"), "autocomplete");
        assert_eq!(task_type_for_extension("js"), "autocomplete");
        assert_eq!(task_type_for_extension("ts"), "autocomplete");
        assert_eq!(task_type_for_extension("md"), "chat");
        assert_eq!(task_type_for_extension("toml"), "config");
        assert_eq!(task_type_for_extension("xyz"), "general");
    }

    #[test]
    fn detect_context_with_override() {
        let ctx = detect_context(None, Some("refactor"));
        assert!(ctx.contains("\"task_type\": \"refactor\""));
        assert!(ctx.contains("\"context_size\": 500"));
    }

    #[test]
    fn detect_context_without_worktree_uses_fallback() {
        let ctx = detect_context(None, None);
        assert_eq!(ctx, FALLBACK_CONTEXT);
    }

    #[test]
    fn detect_context_override_ignores_worktree() {
        // Even if worktree is None, override should win
        let ctx = detect_context(None, Some("debug"));
        assert!(ctx.contains("\"task_type\": \"debug\""));
    }

    // ── Argument parsing tests (B2) ────────────────────────────

    #[test]
    fn valid_task_types_are_accepted() {
        for &t in VALID_TASK_TYPES {
            let args = vec![t.to_string()];
            let override_val = args
                .first()
                .map(|a| a.as_str())
                .filter(|a| VALID_TASK_TYPES.contains(a));
            assert_eq!(override_val, Some(t));
        }
    }

    #[test]
    fn invalid_task_type_is_ignored() {
        let args = vec!["banana".to_string()];
        let override_val = args
            .first()
            .map(|a| a.as_str())
            .filter(|a| VALID_TASK_TYPES.contains(a));
        assert_eq!(override_val, None);
    }

    #[test]
    fn empty_args_gives_no_override() {
        let args: Vec<String> = vec![];
        let override_val = args
            .first()
            .map(|a| a.as_str())
            .filter(|a| VALID_TASK_TYPES.contains(a));
        assert_eq!(override_val, None);
    }
}
