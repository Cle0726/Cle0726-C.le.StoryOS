use serde_json::Value;
use std::process::Command;

const MAX_SESSION_STDOUT_BYTES: usize = 4 * 1024 * 1024;
const MAX_SESSION_STDERR_CHARS: usize = 8 * 1024;

fn workspace_binary() -> String {
    std::env::var("STORYOS_WORKSPACE_BIN").unwrap_or_else(|_| "storyos-workspace".to_owned())
}

fn validate_project_path(value: &str) -> Result<String, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("project path cannot be empty".to_owned());
    }
    if trimmed.chars().any(|ch| matches!(ch, '\0' | '\r' | '\n')) {
        return Err("project path contains invalid control characters".to_owned());
    }
    Ok(trimmed.to_owned())
}

fn session_args(project: &str) -> Result<Vec<String>, String> {
    Ok(vec!["session".to_owned(), validate_project_path(project)?])
}

fn verify_session_response(value: &Value) -> Result<(), String> {
    if value.get("schema").and_then(Value::as_str)
        != Some("story.authoring-project-session.v1")
    {
        return Err("project session response schema mismatch".to_owned());
    }
    let policy = value
        .get("policy")
        .and_then(Value::as_object)
        .ok_or("project session response is missing policy")?;
    for key in [
        "manuscript_mutation",
        "history_mutation",
        "recovery_mutation",
        "canonical_mutation",
        "staging_mutation",
    ] {
        if policy.get(key).and_then(Value::as_bool) != Some(false) {
            return Err(format!("project session response violated {key} policy"));
        }
    }
    if policy.get("read_only").and_then(Value::as_bool) != Some(true) {
        return Err("project session response is not read-only".to_owned());
    }

    let recoveries = value
        .get("recoveries")
        .and_then(Value::as_array)
        .ok_or("project session response is missing recoveries")?;
    if recoveries.iter().any(|row| row.get("content").is_some()) {
        return Err("project session response leaked recovery draft content".to_owned());
    }
    Ok(())
}

#[tauri::command]
pub fn storyos_project_session(project: String) -> Result<Value, String> {
    let args = session_args(&project)?;
    let output = Command::new(workspace_binary())
        .args(&args)
        .output()
        .map_err(|error| format!("failed to start storyos-workspace: {error}"))?;

    if output.stdout.len() > MAX_SESSION_STDOUT_BYTES {
        return Err("project session response exceeded the desktop safety limit".to_owned());
    }
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        let detail = detail.chars().take(MAX_SESSION_STDERR_CHARS).collect::<String>();
        return Err(format!("storyos-workspace session failed: {}", detail.trim()));
    }

    let value: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("project session returned invalid JSON: {error}"))?;
    verify_session_response(&value)?;
    Ok(value)
}

#[tauri::command]
pub fn pick_project_directory() -> Result<Option<String>, String> {
    Ok(rfd::FileDialog::new()
        .set_title("选择 C.le. StoryOS 项目目录")
        .pick_folder()
        .map(|path| path.to_string_lossy().into_owned()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_args_are_fixed_and_project_is_one_plain_argument() {
        assert_eq!(
            session_args("C:/stories/断弦之歌").unwrap(),
            vec!["session", "C:/stories/断弦之歌"]
        );
        assert!(session_args("project\ncanon-commit").is_err());
        assert!(session_args("\0bad").is_err());
    }

    #[test]
    fn session_response_must_be_strictly_read_only_and_content_free() {
        let safe = serde_json::json!({
            "schema": "story.authoring-project-session.v1",
            "project": {"id": "p", "name": "Story", "language": "zh-CN"},
            "summary": {"manuscripts": 1, "recovery_slots": 1, "recoverable_drafts": 1, "stale_base_drafts": 0},
            "recoveries": [{"path": "manuscript/S01/EP01.txt", "draft_sha256": "a"}],
            "policy": {
                "read_only": true,
                "manuscript_mutation": false,
                "history_mutation": false,
                "recovery_mutation": false,
                "canonical_mutation": false,
                "staging_mutation": false
            }
        });
        assert!(verify_session_response(&safe).is_ok());

        let mut unsafe_policy = safe.clone();
        unsafe_policy["policy"]["recovery_mutation"] = Value::Bool(true);
        assert!(verify_session_response(&unsafe_policy).is_err());

        let mut leaked = safe;
        leaked["recoveries"][0]["content"] = Value::String("draft".to_owned());
        assert!(verify_session_response(&leaked).is_err());
    }

    #[test]
    fn session_command_cannot_be_repurposed_as_a_workspace_mutation() {
        let args = session_args("/stories/project").unwrap();
        assert_eq!(args[0], "session");
        assert!(!args.iter().any(|arg| matches!(
            arg.as_str(),
            "claim-decide" | "materialization-stage" | "canon-commit" | "manuscript-save"
        )));
    }
}
