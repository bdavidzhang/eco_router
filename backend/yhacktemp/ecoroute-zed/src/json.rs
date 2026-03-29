// ─── Minimal JSON Parsing (Task A2) ────────────────────────────
//
// No serde_json — WASM binary size matters and it's a hackathon.
// These helpers handle the flat JSON object the backend returns.
// All functions are pure — no IO, no allocation beyond the result.

/// Locate the value portion after `"key": ` in a JSON string.
/// Returns the trimmed slice starting right after the colon.
pub fn find_key_value<'a>(json: &'a str, key: &str) -> Option<&'a str> {
    let needle = format!("\"{}\"", key);
    let mut search_from = 0;

    while search_from < json.len() {
        let offset = json[search_from..].find(&needle)?;
        let abs = search_from + offset;
        let after_key = &json[abs + needle.len()..];
        let trimmed = after_key.trim_start();

        if trimmed.starts_with(':') {
            return Some(trimmed[1..].trim_start());
        }
        search_from = abs + needle.len();
    }
    None
}

/// Extract a JSON string value: `"key": "value"` -> `Some("value")`
pub fn extract_string(json: &str, key: &str) -> Option<String> {
    let value = find_key_value(json, key)?;
    if !value.starts_with('"') {
        return None;
    }
    let end = value[1..].find('"')?;
    Some(value[1..1 + end].to_string())
}

/// Extract a JSON number value: `"key": 41.2` -> `Some(41.2)`
pub fn extract_number(json: &str, key: &str) -> Option<f64> {
    let value = find_key_value(json, key)?;
    let end = value
        .find(|c: char| c == ',' || c == '}' || c == ']' || c == '\n')
        .unwrap_or(value.len());
    value[..end].trim().parse().ok()
}

/// Extract a JSON bool value: `"key": false` -> `Some(false)`
pub fn extract_bool(json: &str, key: &str) -> Option<bool> {
    let value = find_key_value(json, key)?;
    if value.starts_with("true") {
        Some(true)
    } else if value.starts_with("false") {
        Some(false)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"{
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

    #[test]
    fn string_values() {
        assert_eq!(extract_string(SAMPLE, "current_model"), Some("gpt-4o".into()));
        assert_eq!(extract_string(SAMPLE, "recommended_model"), Some("claude-haiku".into()));
        assert_eq!(extract_string(SAMPLE, "task_type"), Some("autocomplete".into()));
    }

    #[test]
    fn number_values() {
        assert_eq!(extract_number(SAMPLE, "current_sci"), Some(41.2));
        assert_eq!(extract_number(SAMPLE, "recommended_sci"), Some(3.1));
        assert_eq!(extract_number(SAMPLE, "task_complexity"), Some(2.0));
        assert_eq!(extract_number(SAMPLE, "carbon_savings_pct"), Some(92.0));
        assert_eq!(extract_number(SAMPLE, "quality_confidence_pct"), Some(96.0));
        assert_eq!(extract_number(SAMPLE, "efficiency_score"), Some(0.87));
    }

    #[test]
    fn bool_values() {
        assert_eq!(extract_bool(SAMPLE, "above_frontier"), Some(false));
        assert_eq!(extract_bool(r#"{"above_frontier": true}"#, "above_frontier"), Some(true));
    }

    #[test]
    fn missing_key_returns_none() {
        assert_eq!(extract_string(SAMPLE, "nonexistent"), None);
        assert_eq!(extract_number(SAMPLE, "nonexistent"), None);
        assert_eq!(extract_bool(SAMPLE, "nonexistent"), None);
    }

    #[test]
    fn empty_json_returns_none() {
        assert_eq!(extract_string("{}", "key"), None);
        assert_eq!(extract_number("{}", "key"), None);
    }

    #[test]
    fn skips_substring_matches() {
        let json = r#"{"current_sci": 41.2, "sci": 99.9}"#;
        assert_eq!(extract_number(json, "sci"), Some(99.9));
        assert_eq!(extract_number(json, "current_sci"), Some(41.2));
    }
}
