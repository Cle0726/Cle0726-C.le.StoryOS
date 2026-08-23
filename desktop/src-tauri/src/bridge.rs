use serde::Deserialize;
use serde_json::Value;
use std::path::{Component, Path};
use std::process::Command;

const MAX_STDOUT_BYTES: usize = 32 * 1024 * 1024;
const MAX_STDERR_BYTES: usize = 16 * 1024;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum WorkspaceAction {
    Snapshot,
    Entity,
    Manuscript,
}

impl WorkspaceAction {
    fn cli_name(self) -> &'static str {
        match self {
            Self::Snapshot => "snapshot",
            Self::Entity => "entity",
            Self::Manuscript => "manuscript",
        }
    }

    fn expected_schema(self) -> &'static str {
        match self {
            Self::Snapshot => "story.authoring-workspace.v1",
            Self::Entity => "story.authoring-entity.v1",
            Self::Manuscript => "story.authoring-manuscript.v1",
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct WorkspaceRequest {
    action: WorkspaceAction,
    project: String,
    entity_id: Option<String>,
    manuscript_path: Option<String>,
    through: Option<u64>,
}

fn require_plain_text(value: &str, label: &str) -> Result<String, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err(format!("{label} cannot be empty"));
    }
    if trimmed.chars().any(|ch| matches!(ch, '\0' | '\r' | '\n')) {
        return Err(format!("{label} contains invalid control characters"));
    }
    Ok(trimmed.to_owned())
}

fn valid_typed_entity_id(value: &str) -> bool {
    let Some((prefix, suffix)) = value.split_once('_') else {
        return false;
    };
    !prefix.is_empty()
        && prefix.chars().all(|ch| ch.is_ascii_lowercase())
        && suffix.len() == 32
        && suffix.chars().all(|ch| ch.is_ascii_hexdigit() && !ch.is_ascii_uppercase())
}

fn validate_relative_manuscript_path(value: &str) -> Result<String, String> {
    let raw = require_plain_text(value, "manuscript path")?;
    let path = Path::new(&raw);
    if path.is_absolute() {
        return Err("manuscript path must be project-relative".to_owned());
    }
    if path.components().any(|component| {
        matches!(component, Component::ParentDir | Component::RootDir | Component::Prefix(_))
    }) {
        return Err("manuscript path cannot escape the project root".to_owned());
    }
    Ok(raw)
}

fn build_args(request: &WorkspaceRequest) -> Result<Vec<String>, String> {
    let project = require_plain_text(&request.project, "project path")?;
    let mut args = vec![request.action.cli_name().to_owned(), project];

    match request.action {
        WorkspaceAction::Snapshot => {
            if request.entity_id.is_some() || request.manuscript_path.is_some() {
                return Err("snapshot does not accept entityId or manuscriptPath".to_owned());
            }
        }
        WorkspaceAction::Entity => {
            if request.manuscript_path.is_some() {
                return Err("entity does not accept manuscriptPath".to_owned());
            }
            let entity_id = require_plain_text(
                request.entity_id.as_deref().ok_or("entity requires entityId")?,
                "entity ID",
            )?;
            if !valid_typed_entity_id(&entity_id) {
                return Err("entity ID has invalid typed-ID format".to_owned());
            }
            args.push(entity_id);
        }
        WorkspaceAction::Manuscript => {
            if request.entity_id.is_some() || request.through.is_some() {
                return Err("manuscript does not accept entityId or through".to_owned());
            }
            let manuscript = request
                .manuscript_path
                .as_deref()
                .ok_or("manuscript requires manuscriptPath")?;
            args.push(validate_relative_manuscript_path(manuscript)?);
        }
    }

    if let Some(through) = request.through {
        args.push("--through".to_owned());
        args.push(through.to_string());
    }
    Ok(args)
}

fn workspace_binary() -> String {
    std::env::var("STORYOS_WORKSPACE_BIN").unwrap_or_else(|_| "storyos-workspace".to_owned())
}

fn verify_response(action: WorkspaceAction, value: &Value) -> Result<(), String> {
    if value.get("schema").and_then(Value::as_str) != Some(action.expected_schema()) {
        return Err(format!(
            "workspace response schema mismatch; expected {}",
            action.expected_schema()
        ));
    }
    let policy = value
        .get("policy")
        .and_then(Value::as_object)
        .ok_or("workspace response is missing read-only policy")?;
    if policy.get("read_only").and_then(Value::as_bool) != Some(true)
        || policy.get("canonical_mutation").and_then(Value::as_bool) != Some(false)
        || policy.get("staging_mutation").and_then(Value::as_bool) != Some(false)
    {
        return Err("workspace response violated the read-only policy".to_owned());
    }
    Ok(())
}

#[tauri::command]
pub fn storyos_workspace(request: WorkspaceRequest) -> Result<Value, String> {
    let args = build_args(&request)?;
    let output = Command::new(workspace_binary())
        .args(&args)
        .output()
        .map_err(|error| format!("failed to start storyos-workspace: {error}"))?;

    if output.stdout.len() > MAX_STDOUT_BYTES {
        return Err("storyos-workspace response exceeded the desktop safety limit".to_owned());
    }
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        let detail = detail.chars().take(MAX_STDERR_BYTES).collect::<String>();
        return Err(format!("storyos-workspace failed: {}", detail.trim()));
    }

    let value: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("storyos-workspace returned invalid JSON: {error}"))?;
    verify_response(request.action, &value)?;
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(action: WorkspaceAction) -> WorkspaceRequest {
        WorkspaceRequest {
            action,
            project: "C:/story/project".to_owned(),
            entity_id: None,
            manuscript_path: None,
            through: None,
        }
    }

    #[test]
    fn snapshot_builds_only_the_snapshot_subcommand() {
        let mut row = request(WorkspaceAction::Snapshot);
        row.through = Some(1_020_300);
        assert_eq!(
            build_args(&row).unwrap(),
            vec!["snapshot", "C:/story/project", "--through", "1020300"]
        );
    }

    #[test]
    fn entity_requires_a_typed_id() {
        let mut row = request(WorkspaceAction::Entity);
        row.entity_id = Some("claim-not-an-entity".to_owned());
        assert!(build_args(&row).unwrap_err().contains("typed-ID"));

        row.entity_id = Some("chr_0123456789abcdef0123456789abcdef".to_owned());
        assert_eq!(build_args(&row).unwrap()[0], "entity");
    }

    #[test]
    fn manuscript_rejects_parent_traversal_and_absolute_paths() {
        let mut row = request(WorkspaceAction::Manuscript);
        row.manuscript_path = Some("manuscript/../storyos.yaml".to_owned());
        assert!(build_args(&row).is_err());
        row.manuscript_path = Some("/tmp/story.txt".to_owned());
        assert!(build_args(&row).is_err());
        row.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
        assert_eq!(build_args(&row).unwrap()[0], "manuscript");
    }

    #[test]
    fn no_request_shape_can_name_a_mutation_subcommand() {
        for action in [WorkspaceAction::Snapshot, WorkspaceAction::Entity, WorkspaceAction::Manuscript] {
            let mut row = request(action);
            match action {
                WorkspaceAction::Snapshot => {}
                WorkspaceAction::Entity => {
                    row.entity_id = Some("chr_0123456789abcdef0123456789abcdef".to_owned());
                }
                WorkspaceAction::Manuscript => {
                    row.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
                }
            }
            let args = build_args(&row).unwrap();
            assert!(!args.iter().any(|arg| {
                matches!(
                    arg.as_str(),
                    "claim-decide" | "materialization-stage" | "canon-commit" | "canon-commit-plan"
                )
            }));
        }
    }

    #[test]
    fn read_only_policy_is_rechecked_in_rust() {
        let safe = serde_json::json!({
            "schema": "story.authoring-workspace.v1",
            "policy": {
                "read_only": true,
                "canonical_mutation": false,
                "staging_mutation": false
            }
        });
        assert!(verify_response(WorkspaceAction::Snapshot, &safe).is_ok());

        let unsafe_value = serde_json::json!({
            "schema": "story.authoring-workspace.v1",
            "policy": {
                "read_only": false,
                "canonical_mutation": true,
                "staging_mutation": false
            }
        });
        assert!(verify_response(WorkspaceAction::Snapshot, &unsafe_value).is_err());
    }
}
