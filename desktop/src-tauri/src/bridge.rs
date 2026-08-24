use serde::Deserialize;
use serde_json::Value;
use std::io::Write;
use std::path::{Component, Path};
use std::process::{Command, Output, Stdio};

const MAX_STDOUT_BYTES: usize = 32 * 1024 * 1024;
const MAX_STDERR_CHARS: usize = 16 * 1024;
const MAX_MANUSCRIPT_INPUT_BYTES: usize = 16 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
enum WorkspaceAction {
    #[serde(rename = "snapshot")]
    Snapshot,
    #[serde(rename = "entity")]
    Entity,
    #[serde(rename = "manuscript")]
    Manuscript,
    #[serde(rename = "manuscript-history")]
    ManuscriptHistory,
    #[serde(rename = "manuscript-revision")]
    ManuscriptRevision,
    #[serde(rename = "manuscript-recovery")]
    ManuscriptRecovery,
    #[serde(rename = "manuscript-recovery-save")]
    ManuscriptRecoverySave,
    #[serde(rename = "manuscript-recovery-clear")]
    ManuscriptRecoveryClear,
    #[serde(rename = "manuscript-save")]
    ManuscriptSave,
}

impl WorkspaceAction {
    fn cli_name(self) -> &'static str {
        match self {
            Self::Snapshot => "snapshot",
            Self::Entity => "entity",
            Self::Manuscript => "manuscript",
            Self::ManuscriptHistory => "manuscript-history",
            Self::ManuscriptRevision => "manuscript-revision",
            Self::ManuscriptRecovery => "manuscript-recovery",
            Self::ManuscriptRecoverySave => "manuscript-recovery-save",
            Self::ManuscriptRecoveryClear => "manuscript-recovery-clear",
            Self::ManuscriptSave => "manuscript-save",
        }
    }

    fn expected_schemas(self) -> &'static [&'static str] {
        match self {
            Self::Snapshot => &["story.authoring-workspace.v1"],
            Self::Entity => &["story.authoring-entity.v1"],
            Self::Manuscript => &["story.authoring-manuscript.v1"],
            Self::ManuscriptHistory => &["story.authoring-manuscript-history.v1"],
            Self::ManuscriptRevision => &["story.authoring-manuscript-revision.v1"],
            Self::ManuscriptRecovery => &["story.authoring-manuscript-recovery.v1"],
            Self::ManuscriptRecoverySave => &["story.authoring-manuscript-recovery-save.v1"],
            Self::ManuscriptRecoveryClear => &["story.authoring-manuscript-recovery-clear.v1"],
            Self::ManuscriptSave => &[
                "story.authoring-manuscript-save.v1",
                "story.authoring-manuscript-conflict.v1",
            ],
        }
    }

    fn uses_stdin_content(self) -> bool {
        matches!(self, Self::ManuscriptSave | Self::ManuscriptRecoverySave)
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct WorkspaceRequest {
    action: WorkspaceAction,
    project: String,
    entity_id: Option<String>,
    manuscript_path: Option<String>,
    expected_sha256: Option<String>,
    revision_sha256: Option<String>,
    base_sha256: Option<String>,
    expected_draft_sha256: Option<String>,
    content: Option<String>,
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
        && suffix
            .chars()
            .all(|ch| ch.is_ascii_hexdigit() && !ch.is_ascii_uppercase())
}

fn validate_sha256(value: &str, label: &str) -> Result<String, String> {
    let hash = require_plain_text(value, label)?;
    if hash.len() != 64
        || !hash
            .chars()
            .all(|ch| ch.is_ascii_hexdigit() && !ch.is_ascii_uppercase())
    {
        return Err(format!(
            "{label} must be 64 lowercase hexadecimal characters"
        ));
    }
    Ok(hash)
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

fn validate_relative_manuscript_path(value: &str) -> Result<String, String> {
    let raw = require_plain_text(value, "manuscript path")?;
    if looks_like_windows_absolute_path(&raw) {
        return Err("manuscript path must be project-relative".to_owned());
    }
    let path = Path::new(&raw);
    if path.is_absolute() {
        return Err("manuscript path must be project-relative".to_owned());
    }
    if path.components().any(|component| {
        matches!(
            component,
            Component::ParentDir | Component::RootDir | Component::Prefix(_)
        )
    }) {
        return Err("manuscript path cannot escape the project root".to_owned());
    }
    if raw.split(['/', '\\']).any(|part| part == "..") {
        return Err("manuscript path cannot escape the project root".to_owned());
    }
    Ok(raw)
}

fn no_extra_fields(request: &WorkspaceRequest) -> bool {
    request.entity_id.is_none()
        && request.manuscript_path.is_none()
        && request.expected_sha256.is_none()
        && request.revision_sha256.is_none()
        && request.base_sha256.is_none()
        && request.expected_draft_sha256.is_none()
        && request.content.is_none()
}

fn build_args(request: &WorkspaceRequest) -> Result<Vec<String>, String> {
    let project = require_plain_text(&request.project, "project path")?;
    let mut args = vec![request.action.cli_name().to_owned(), project];

    match request.action {
        WorkspaceAction::Snapshot => {
            if !no_extra_fields(request) {
                return Err("snapshot accepts only project and optional through".to_owned());
            }
        }
        WorkspaceAction::Entity => {
            if request.manuscript_path.is_some()
                || request.expected_sha256.is_some()
                || request.revision_sha256.is_some()
                || request.base_sha256.is_some()
                || request.expected_draft_sha256.is_some()
                || request.content.is_some()
            {
                return Err("entity accepts only entityId and optional through".to_owned());
            }
            let entity_id = require_plain_text(
                request
                    .entity_id
                    .as_deref()
                    .ok_or("entity requires entityId")?,
                "entity ID",
            )?;
            if !valid_typed_entity_id(&entity_id) {
                return Err("entity ID has invalid typed-ID format".to_owned());
            }
            args.push(entity_id);
        }
        WorkspaceAction::Manuscript
        | WorkspaceAction::ManuscriptHistory
        | WorkspaceAction::ManuscriptRecovery => {
            if request.entity_id.is_some()
                || request.through.is_some()
                || request.expected_sha256.is_some()
                || request.revision_sha256.is_some()
                || request.base_sha256.is_some()
                || request.expected_draft_sha256.is_some()
                || request.content.is_some()
            {
                return Err(format!(
                    "{} accepts only manuscriptPath",
                    request.action.cli_name()
                ));
            }
            let manuscript = request
                .manuscript_path
                .as_deref()
                .ok_or("manuscript read requires manuscriptPath")?;
            args.push(validate_relative_manuscript_path(manuscript)?);
        }
        WorkspaceAction::ManuscriptRevision => {
            if request.entity_id.is_some()
                || request.through.is_some()
                || request.expected_sha256.is_some()
                || request.base_sha256.is_some()
                || request.expected_draft_sha256.is_some()
                || request.content.is_some()
            {
                return Err(
                    "manuscript-revision accepts only manuscriptPath and revisionSha256"
                        .to_owned(),
                );
            }
            let manuscript = request
                .manuscript_path
                .as_deref()
                .ok_or("manuscript-revision requires manuscriptPath")?;
            let revision = request
                .revision_sha256
                .as_deref()
                .ok_or("manuscript-revision requires revisionSha256")?;
            args.push(validate_relative_manuscript_path(manuscript)?);
            args.push(validate_sha256(revision, "revision SHA-256")?);
        }
        WorkspaceAction::ManuscriptRecoverySave => {
            if request.entity_id.is_some()
                || request.through.is_some()
                || request.expected_sha256.is_some()
                || request.revision_sha256.is_some()
                || request.expected_draft_sha256.is_some()
            {
                return Err(
                    "manuscript-recovery-save accepts only manuscriptPath, baseSha256 and content"
                        .to_owned(),
                );
            }
            let manuscript = request
                .manuscript_path
                .as_deref()
                .ok_or("manuscript-recovery-save requires manuscriptPath")?;
            let base = request
                .base_sha256
                .as_deref()
                .ok_or("manuscript-recovery-save requires baseSha256")?;
            let content = request
                .content
                .as_deref()
                .ok_or("manuscript-recovery-save requires content")?;
            if content.len() > MAX_MANUSCRIPT_INPUT_BYTES {
                return Err("recovery draft exceeded the desktop safety limit".to_owned());
            }
            if content.contains('\0') {
                return Err("recovery draft cannot contain NUL characters".to_owned());
            }
            args.push(validate_relative_manuscript_path(manuscript)?);
            args.push(validate_sha256(base, "recovery base SHA-256")?);
        }
        WorkspaceAction::ManuscriptRecoveryClear => {
            if request.entity_id.is_some()
                || request.through.is_some()
                || request.expected_sha256.is_some()
                || request.revision_sha256.is_some()
                || request.base_sha256.is_some()
                || request.content.is_some()
            {
                return Err(
                    "manuscript-recovery-clear accepts only manuscriptPath and expectedDraftSha256"
                        .to_owned(),
                );
            }
            let manuscript = request
                .manuscript_path
                .as_deref()
                .ok_or("manuscript-recovery-clear requires manuscriptPath")?;
            let expected = request
                .expected_draft_sha256
                .as_deref()
                .ok_or("manuscript-recovery-clear requires expectedDraftSha256")?;
            args.push(validate_relative_manuscript_path(manuscript)?);
            args.push(validate_sha256(expected, "recovery draft SHA-256")?);
        }
        WorkspaceAction::ManuscriptSave => {
            if request.entity_id.is_some()
                || request.through.is_some()
                || request.revision_sha256.is_some()
                || request.base_sha256.is_some()
                || request.expected_draft_sha256.is_some()
            {
                return Err(
                    "manuscript-save accepts only manuscriptPath, expectedSha256 and content"
                        .to_owned(),
                );
            }
            let manuscript = request
                .manuscript_path
                .as_deref()
                .ok_or("manuscript-save requires manuscriptPath")?;
            let expected = request
                .expected_sha256
                .as_deref()
                .ok_or("manuscript-save requires expectedSha256")?;
            let content = request
                .content
                .as_deref()
                .ok_or("manuscript-save requires content")?;
            if content.len() > MAX_MANUSCRIPT_INPUT_BYTES {
                return Err("manuscript content exceeded the desktop safety limit".to_owned());
            }
            if content.contains('\0') {
                return Err("manuscript content cannot contain NUL characters".to_owned());
            }
            args.push(validate_relative_manuscript_path(manuscript)?);
            args.push(validate_sha256(expected, "expected SHA-256")?);
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

fn run_workspace(request: &WorkspaceRequest, args: &[String]) -> Result<Output, String> {
    let mut command = Command::new(workspace_binary());
    command.args(args);
    if !request.action.uses_stdin_content() {
        return command
            .output()
            .map_err(|error| format!("failed to start storyos-workspace: {error}"));
    }

    let content = request
        .content
        .as_deref()
        .ok_or("workspace write requires content")?;
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("failed to start storyos-workspace: {error}"))?;

    let write_result = match child.stdin.take() {
        Some(mut stdin) => stdin.write_all(content.as_bytes()),
        None => return Err("failed to open storyos-workspace stdin".to_owned()),
    };
    let output = child
        .wait_with_output()
        .map_err(|error| format!("failed to wait for storyos-workspace: {error}"))?;
    if output.status.success() {
        write_result.map_err(|error| format!("failed to send manuscript content: {error}"))?;
    }
    Ok(output)
}

fn verify_response(action: WorkspaceAction, value: &Value) -> Result<(), String> {
    let schema = value
        .get("schema")
        .and_then(Value::as_str)
        .ok_or("workspace response is missing schema")?;
    if !action.expected_schemas().contains(&schema) {
        return Err(format!(
            "workspace response schema mismatch; expected one of {:?}",
            action.expected_schemas()
        ));
    }

    let policy = value
        .get("policy")
        .and_then(Value::as_object)
        .ok_or("workspace response is missing policy")?;
    if policy.get("canonical_mutation").and_then(Value::as_bool) != Some(false)
        || policy.get("staging_mutation").and_then(Value::as_bool) != Some(false)
    {
        return Err("workspace response violated Canon/staging mutation policy".to_owned());
    }

    match action {
        WorkspaceAction::ManuscriptSave => {
            if schema == "story.authoring-manuscript-conflict.v1" {
                if policy.get("read_only").and_then(Value::as_bool) != Some(true)
                    || policy.get("manuscript_mutation").and_then(Value::as_bool) != Some(false)
                    || policy.get("history_mutation").and_then(Value::as_bool) != Some(false)
                    || policy.get("recovery_mutation").and_then(Value::as_bool) == Some(true)
                {
                    return Err("manuscript conflict response violated read-only policy".to_owned());
                }
            } else if policy.get("read_only").and_then(Value::as_bool) != Some(false)
                || policy.get("manuscript_mutation").and_then(Value::as_bool) != Some(true)
                || policy.get("history_mutation").and_then(Value::as_bool) != Some(true)
                || policy.get("recovery_mutation").and_then(Value::as_bool) == Some(true)
            {
                return Err("manuscript-save response violated manuscript/history write policy".to_owned());
            }
        }
        WorkspaceAction::ManuscriptRecoverySave | WorkspaceAction::ManuscriptRecoveryClear => {
            if policy.get("read_only").and_then(Value::as_bool) != Some(false)
                || policy.get("manuscript_mutation").and_then(Value::as_bool) != Some(false)
                || policy.get("history_mutation").and_then(Value::as_bool) != Some(false)
                || policy.get("recovery_mutation").and_then(Value::as_bool) != Some(true)
            {
                return Err("recovery response violated recovery-only write policy".to_owned());
            }
        }
        _ => {
            if policy.get("read_only").and_then(Value::as_bool) != Some(true)
                || policy.get("manuscript_mutation").and_then(Value::as_bool) == Some(true)
                || policy.get("history_mutation").and_then(Value::as_bool) == Some(true)
                || policy.get("recovery_mutation").and_then(Value::as_bool) == Some(true)
            {
                return Err("workspace response violated the read-only policy".to_owned());
            }
        }
    }
    Ok(())
}

#[tauri::command]
pub fn storyos_workspace(request: WorkspaceRequest) -> Result<Value, String> {
    let args = build_args(&request)?;
    let output = run_workspace(&request, &args)?;

    if output.stdout.len() > MAX_STDOUT_BYTES {
        return Err("storyos-workspace response exceeded the desktop safety limit".to_owned());
    }
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        let detail = detail.chars().take(MAX_STDERR_CHARS).collect::<String>();
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
            expected_sha256: None,
            revision_sha256: None,
            base_sha256: None,
            expected_draft_sha256: None,
            content: None,
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
    fn manuscript_rejects_parent_traversal_and_absolute_paths_cross_platform() {
        let mut row = request(WorkspaceAction::Manuscript);
        for invalid in [
            "manuscript/../storyos.yaml",
            "manuscript\\..\\storyos.yaml",
            "/tmp/story.txt",
            "C:\\story\\EP01.txt",
            "C:/story/EP01.txt",
            "\\\\server\\share\\EP01.txt",
        ] {
            row.manuscript_path = Some(invalid.to_owned());
            assert!(build_args(&row).is_err(), "expected rejection for {invalid}");
        }
        row.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
        assert_eq!(build_args(&row).unwrap()[0], "manuscript");
    }

    #[test]
    fn history_revision_and_recovery_reads_are_path_scoped() {
        let mut history = request(WorkspaceAction::ManuscriptHistory);
        history.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
        assert_eq!(
            build_args(&history).unwrap(),
            vec!["manuscript-history", "C:/story/project", "manuscript/S01/EP01.txt"]
        );

        let mut revision = request(WorkspaceAction::ManuscriptRevision);
        revision.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
        revision.revision_sha256 = Some("b".repeat(64));
        assert_eq!(
            build_args(&revision).unwrap(),
            vec![
                "manuscript-revision",
                "C:/story/project",
                "manuscript/S01/EP01.txt",
                &"b".repeat(64),
            ]
        );
        revision.revision_sha256 = Some("BAD".to_owned());
        assert!(build_args(&revision).is_err());

        let mut recovery = request(WorkspaceAction::ManuscriptRecovery);
        recovery.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
        assert_eq!(
            build_args(&recovery).unwrap(),
            vec!["manuscript-recovery", "C:/story/project", "manuscript/S01/EP01.txt"]
        );
    }

    #[test]
    fn manuscript_save_keeps_content_off_argv_and_requires_cas_hash() {
        let mut row = request(WorkspaceAction::ManuscriptSave);
        row.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
        row.expected_sha256 = Some("a".repeat(64));
        row.content = Some("正文\n第二行".to_owned());
        let args = build_args(&row).unwrap();
        assert_eq!(args, vec![
            "manuscript-save",
            "C:/story/project",
            "manuscript/S01/EP01.txt",
            &"a".repeat(64),
        ]);
        assert!(!args.iter().any(|arg| arg.contains("正文")));

        row.expected_sha256 = Some("BAD".to_owned());
        assert!(build_args(&row).is_err());
    }

    #[test]
    fn recovery_save_keeps_content_off_argv_and_clear_requires_exact_hash() {
        let mut save = request(WorkspaceAction::ManuscriptRecoverySave);
        save.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
        save.base_sha256 = Some("c".repeat(64));
        save.content = Some("自动恢复草稿\n第二行".to_owned());
        let args = build_args(&save).unwrap();
        assert_eq!(args, vec![
            "manuscript-recovery-save",
            "C:/story/project",
            "manuscript/S01/EP01.txt",
            &"c".repeat(64),
        ]);
        assert!(!args.iter().any(|arg| arg.contains("自动恢复草稿")));

        let mut clear = request(WorkspaceAction::ManuscriptRecoveryClear);
        clear.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
        clear.expected_draft_sha256 = Some("d".repeat(64));
        assert_eq!(build_args(&clear).unwrap(), vec![
            "manuscript-recovery-clear",
            "C:/story/project",
            "manuscript/S01/EP01.txt",
            &"d".repeat(64),
        ]);
        clear.expected_draft_sha256 = Some("BAD".to_owned());
        assert!(build_args(&clear).is_err());
    }

    #[test]
    fn no_request_shape_can_name_canon_or_staging_mutation_subcommands() {
        for action in [
            WorkspaceAction::Snapshot,
            WorkspaceAction::Entity,
            WorkspaceAction::Manuscript,
            WorkspaceAction::ManuscriptHistory,
            WorkspaceAction::ManuscriptRevision,
            WorkspaceAction::ManuscriptRecovery,
            WorkspaceAction::ManuscriptRecoverySave,
            WorkspaceAction::ManuscriptRecoveryClear,
            WorkspaceAction::ManuscriptSave,
        ] {
            let mut row = request(action);
            match action {
                WorkspaceAction::Snapshot => {}
                WorkspaceAction::Entity => {
                    row.entity_id = Some("chr_0123456789abcdef0123456789abcdef".to_owned());
                }
                WorkspaceAction::Manuscript
                | WorkspaceAction::ManuscriptHistory
                | WorkspaceAction::ManuscriptRecovery => {
                    row.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
                }
                WorkspaceAction::ManuscriptRevision => {
                    row.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
                    row.revision_sha256 = Some("b".repeat(64));
                }
                WorkspaceAction::ManuscriptRecoverySave => {
                    row.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
                    row.base_sha256 = Some("c".repeat(64));
                    row.content = Some("safe recovery draft".to_owned());
                }
                WorkspaceAction::ManuscriptRecoveryClear => {
                    row.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
                    row.expected_draft_sha256 = Some("d".repeat(64));
                }
                WorkspaceAction::ManuscriptSave => {
                    row.manuscript_path = Some("manuscript/S01/EP01.txt".to_owned());
                    row.expected_sha256 = Some("a".repeat(64));
                    row.content = Some("safe draft".to_owned());
                }
            }
            let args = build_args(&row).unwrap();
            assert!(!args.iter().any(|arg| {
                matches!(
                    arg.as_str(),
                    "claim-decide"
                        | "materialization-stage"
                        | "canon-commit"
                        | "canon-commit-plan"
                        | "worldstate-claims"
                )
            }));
        }
    }

    #[test]
    fn response_policy_distinguishes_reads_manuscript_writes_recovery_writes_and_conflicts() {
        let read = serde_json::json!({
            "schema": "story.authoring-workspace.v1",
            "policy": {
                "read_only": true,
                "canonical_mutation": false,
                "staging_mutation": false
            }
        });
        assert!(verify_response(WorkspaceAction::Snapshot, &read).is_ok());

        let recovery_read = serde_json::json!({
            "schema": "story.authoring-manuscript-recovery.v1",
            "policy": {
                "read_only": true,
                "manuscript_mutation": false,
                "history_mutation": false,
                "recovery_mutation": false,
                "canonical_mutation": false,
                "staging_mutation": false
            }
        });
        assert!(verify_response(WorkspaceAction::ManuscriptRecovery, &recovery_read).is_ok());

        let recovery_write = serde_json::json!({
            "schema": "story.authoring-manuscript-recovery-save.v1",
            "policy": {
                "read_only": false,
                "manuscript_mutation": false,
                "history_mutation": false,
                "recovery_mutation": true,
                "canonical_mutation": false,
                "staging_mutation": false
            }
        });
        assert!(verify_response(WorkspaceAction::ManuscriptRecoverySave, &recovery_write).is_ok());

        let write = serde_json::json!({
            "schema": "story.authoring-manuscript-save.v1",
            "policy": {
                "read_only": false,
                "manuscript_mutation": true,
                "history_mutation": true,
                "canonical_mutation": false,
                "staging_mutation": false
            }
        });
        assert!(verify_response(WorkspaceAction::ManuscriptSave, &write).is_ok());

        let conflict = serde_json::json!({
            "schema": "story.authoring-manuscript-conflict.v1",
            "policy": {
                "read_only": true,
                "manuscript_mutation": false,
                "history_mutation": false,
                "recovery_mutation": false,
                "canonical_mutation": false,
                "staging_mutation": false
            }
        });
        assert!(verify_response(WorkspaceAction::ManuscriptSave, &conflict).is_ok());

        let unsafe_value = serde_json::json!({
            "schema": "story.authoring-manuscript-recovery-save.v1",
            "policy": {
                "read_only": false,
                "manuscript_mutation": true,
                "history_mutation": false,
                "recovery_mutation": true,
                "canonical_mutation": false,
                "staging_mutation": false
            }
        });
        assert!(verify_response(WorkspaceAction::ManuscriptRecoverySave, &unsafe_value).is_err());
    }
}
