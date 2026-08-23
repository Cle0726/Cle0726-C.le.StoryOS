use serde_json::Value;
use std::path::{Component, Path};
use std::process::Command;

const MAX_SCENE_STDOUT_BYTES: usize = 8 * 1024 * 1024;
const MAX_SCENE_STDERR_CHARS: usize = 8 * 1024;

fn workspace_binary() -> String {
    std::env::var("STORYOS_WORKSPACE_BIN").unwrap_or_else(|_| "storyos-workspace".to_owned())
}

fn plain(value: &str, label: &str) -> Result<String, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err(format!("{label} cannot be empty"));
    }
    if trimmed.chars().any(|ch| matches!(ch, '\0' | '\r' | '\n')) {
        return Err(format!("{label} contains invalid control characters"));
    }
    Ok(trimmed.to_owned())
}

fn looks_like_windows_absolute_path(value: &str) -> bool {
    let bytes = value.as_bytes();
    (bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && matches!(bytes[2], b'\\' | b'/'))
        || value.starts_with("\\\\")
        || value.starts_with("//")
}

fn manuscript_path(value: &str) -> Result<String, String> {
    let raw = plain(value, "manuscript path")?;
    if looks_like_windows_absolute_path(&raw) {
        return Err("manuscript path must be project-relative".to_owned());
    }
    let path = Path::new(&raw);
    if path.is_absolute()
        || path.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
        || raw.split(['/', '\\']).any(|part| part == "..")
    {
        return Err("manuscript path cannot escape the project root".to_owned());
    }
    Ok(raw)
}

fn typed_character_id(value: &str) -> Result<String, String> {
    let id = plain(value, "POV entity ID")?;
    let Some((prefix, suffix)) = id.split_once('_') else {
        return Err("POV entity ID has invalid typed-ID format".to_owned());
    };
    if prefix != "chr"
        || suffix.len() != 32
        || !suffix
            .chars()
            .all(|ch| ch.is_ascii_hexdigit() && !ch.is_ascii_uppercase())
    {
        return Err("POV entity ID must be a character typed ID".to_owned());
    }
    Ok(id)
}

fn scene_args(
    project: &str,
    path: &str,
    through: Option<u64>,
    pov_entity_id: Option<&str>,
) -> Result<Vec<String>, String> {
    let mut args = vec![
        "scene".to_owned(),
        plain(project, "project path")?,
        manuscript_path(path)?,
    ];
    if let Some(sequence) = through {
        args.push("--through".to_owned());
        args.push(sequence.to_string());
    }
    if let Some(pov) = pov_entity_id {
        args.push("--pov".to_owned());
        args.push(typed_character_id(pov)?);
    }
    Ok(args)
}

fn verify_scene_response(value: &Value) -> Result<(), String> {
    if value.get("schema").and_then(Value::as_str)
        != Some("story.authoring-scene-workspace.v1")
    {
        return Err("scene workspace response schema mismatch".to_owned());
    }

    let policy = value
        .get("policy")
        .and_then(Value::as_object)
        .ok_or("scene workspace response is missing policy")?;
    if policy.get("read_only").and_then(Value::as_bool) != Some(true) {
        return Err("scene workspace response is not read-only".to_owned());
    }
    for key in [
        "manuscript_mutation",
        "history_mutation",
        "recovery_mutation",
        "canonical_mutation",
        "staging_mutation",
    ] {
        if policy.get(key).and_then(Value::as_bool) != Some(false) {
            return Err(format!("scene workspace response violated {key} policy"));
        }
    }
    if policy
        .get("manuscript_content_included")
        .and_then(Value::as_bool)
        != Some(false)
    {
        return Err("scene workspace response did not guarantee content exclusion".to_owned());
    }

    let manuscript = value
        .get("manuscript")
        .and_then(Value::as_object)
        .ok_or("scene workspace response is missing manuscript metadata")?;
    if manuscript.contains_key("content") {
        return Err("scene workspace response leaked manuscript content".to_owned());
    }

    let mode = value
        .get("mode")
        .and_then(Value::as_str)
        .ok_or("scene workspace response is missing mode")?;
    if mode == "pov" {
        if policy.get("pov_safe").and_then(Value::as_bool) != Some(true)
            || policy
                .get("other_character_state_exposed")
                .and_then(Value::as_bool)
                != Some(false)
            || policy
                .get("global_canon_conflicts_exposed")
                .and_then(Value::as_bool)
                != Some(false)
            || policy
                .get("global_plot_threads_exposed")
                .and_then(Value::as_bool)
                != Some(false)
        {
            return Err("POV scene workspace response violated leakage policy".to_owned());
        }

        let pov_id = value
            .get("pov")
            .and_then(Value::as_object)
            .and_then(|row| row.get("id"))
            .and_then(Value::as_str)
            .ok_or("POV scene workspace response is missing POV identity")?;
        let characters = value
            .get("characters")
            .and_then(Value::as_array)
            .ok_or("POV scene workspace response is missing characters")?;
        if characters.len() != 1
            || characters[0].get("id").and_then(Value::as_str) != Some(pov_id)
        {
            return Err("POV scene workspace response exposed another character state".to_owned());
        }
        if value
            .get("canon_conflicts")
            .and_then(Value::as_array)
            .is_some_and(|rows| !rows.is_empty())
            || value
                .get("open_plots")
                .and_then(Value::as_array)
                .is_some_and(|rows| !rows.is_empty())
            || value
                .get("workflow_attention")
                .and_then(Value::as_object)
                .is_some_and(|rows| !rows.is_empty())
        {
            return Err("POV scene workspace response leaked author-only context".to_owned());
        }
    } else if mode != "author" {
        return Err("scene workspace response has unsupported mode".to_owned());
    }

    Ok(())
}

#[tauri::command]
pub fn storyos_scene_workspace(
    project: String,
    manuscript_path: String,
    through: Option<u64>,
    pov_entity_id: Option<String>,
) -> Result<Value, String> {
    let args = scene_args(
        &project,
        &manuscript_path,
        through,
        pov_entity_id.as_deref(),
    )?;
    let output = Command::new(workspace_binary())
        .args(&args)
        .output()
        .map_err(|error| format!("failed to start storyos-workspace: {error}"))?;

    if output.stdout.len() > MAX_SCENE_STDOUT_BYTES {
        return Err("scene workspace response exceeded the desktop safety limit".to_owned());
    }
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        let detail = detail.chars().take(MAX_SCENE_STDERR_CHARS).collect::<String>();
        return Err(format!("storyos-workspace scene failed: {}", detail.trim()));
    }

    let value: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("scene workspace returned invalid JSON: {error}"))?;
    verify_scene_response(&value)?;
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn safe_response() -> Value {
        serde_json::json!({
            "schema": "story.authoring-scene-workspace.v1",
            "project_id": "p",
            "mode": "author",
            "pov": null,
            "manuscript": {"path": "manuscript/S01/EP01.txt", "title": "One"},
            "timeline": {"effective_through_sequence": 10},
            "navigation": {"previous": null, "next": null},
            "episode_events": [],
            "characters": [],
            "canon_conflicts": [],
            "open_plots": [],
            "workflow_attention": {},
            "actions_available": [],
            "policy": {
                "read_only": true,
                "manuscript_mutation": false,
                "history_mutation": false,
                "recovery_mutation": false,
                "canonical_mutation": false,
                "staging_mutation": false,
                "pov_safe": false,
                "other_character_state_exposed": true,
                "global_canon_conflicts_exposed": true,
                "global_plot_threads_exposed": true,
                "manuscript_content_included": false
            }
        })
    }

    #[test]
    fn scene_args_are_fixed_path_scoped_and_optionally_pov_safe() {
        let pov = "chr_0123456789abcdef0123456789abcdef";
        assert_eq!(
            scene_args(
                "C:/stories/project",
                "manuscript/S01/EP20.txt",
                Some(2030),
                Some(pov),
            )
            .unwrap(),
            vec![
                "scene",
                "C:/stories/project",
                "manuscript/S01/EP20.txt",
                "--through",
                "2030",
                "--pov",
                pov,
            ]
        );
        for invalid in [
            "manuscript/../storyos.yaml",
            "C:/story/EP01.txt",
            "\\\\server\\share\\EP01.txt",
        ] {
            assert!(scene_args("project", invalid, None, None).is_err());
        }
        assert!(scene_args("project", "manuscript/EP01.txt", None, Some("canon-commit")).is_err());
    }

    #[test]
    fn scene_response_must_be_strictly_read_only_and_content_free() {
        let safe = safe_response();
        assert!(verify_scene_response(&safe).is_ok());

        let mut unsafe_policy = safe.clone();
        unsafe_policy["policy"]["canonical_mutation"] = Value::Bool(true);
        assert!(verify_scene_response(&unsafe_policy).is_err());

        let mut leaked = safe;
        leaked["manuscript"]["content"] = Value::String("chapter text".to_owned());
        assert!(verify_scene_response(&leaked).is_err());
    }

    #[test]
    fn pov_response_rejects_other_character_or_author_only_context() {
        let pov = "chr_0123456789abcdef0123456789abcdef";
        let mut safe = safe_response();
        safe["mode"] = Value::String("pov".to_owned());
        safe["pov"] = serde_json::json!({"id": pov, "name": "Aria", "aliases": []});
        safe["characters"] = serde_json::json!([{"id": pov, "state": {}, "knowledge": {}}]);
        safe["policy"]["pov_safe"] = Value::Bool(true);
        safe["policy"]["other_character_state_exposed"] = Value::Bool(false);
        safe["policy"]["global_canon_conflicts_exposed"] = Value::Bool(false);
        safe["policy"]["global_plot_threads_exposed"] = Value::Bool(false);
        assert!(verify_scene_response(&safe).is_ok());

        let mut leaked_character = safe.clone();
        leaked_character["characters"] = serde_json::json!([
            {"id": pov},
            {"id": "chr_ffffffffffffffffffffffffffffffff"}
        ]);
        assert!(verify_scene_response(&leaked_character).is_err());

        let mut leaked_conflict = safe;
        leaked_conflict["canon_conflicts"] = serde_json::json!([{"predicate": "secret"}]);
        assert!(verify_scene_response(&leaked_conflict).is_err());
    }

    #[test]
    fn scene_command_cannot_be_repurposed_as_a_workspace_mutation() {
        let args = scene_args("/stories/project", "manuscript/S01/EP01.txt", None, None).unwrap();
        assert_eq!(args[0], "scene");
        assert!(!args.iter().any(|arg| matches!(
            arg.as_str(),
            "claim-decide" | "materialization-stage" | "canon-commit" | "manuscript-save"
        )));
    }
}
