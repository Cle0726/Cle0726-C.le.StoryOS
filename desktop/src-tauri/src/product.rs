use serde::Deserialize;
use serde_json::Value;
use std::process::Command;

const MAX_STDOUT_BYTES: usize = 16 * 1024 * 1024;
const MAX_STDERR_CHARS: usize = 16 * 1024;
const MAX_TEXT_CHARS: usize = 4096;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
enum ProductAction {
    #[serde(rename = "project-create")]
    ProjectCreate,
    #[serde(rename = "manuscript-create")]
    ManuscriptCreate,
    #[serde(rename = "claim-review")]
    ClaimReview,
    #[serde(rename = "claim-decide")]
    ClaimDecide,
    #[serde(rename = "materialization-plan")]
    MaterializationPlan,
    #[serde(rename = "materialization-stage")]
    MaterializationStage,
    #[serde(rename = "canon-commit-plan")]
    CanonCommitPlan,
    #[serde(rename = "canon-commit")]
    CanonCommit,
}

impl ProductAction {
    fn cli_name(self) -> &'static str {
        match self {
            Self::ProjectCreate => "project-create",
            Self::ManuscriptCreate => "manuscript-create",
            Self::ClaimReview => "claim-review",
            Self::ClaimDecide => "claim-decide",
            Self::MaterializationPlan => "materialization-plan",
            Self::MaterializationStage => "materialization-stage",
            Self::CanonCommitPlan => "canon-commit-plan",
            Self::CanonCommit => "canon-commit",
        }
    }

    fn expected_schema(self) -> &'static str {
        match self {
            Self::ProjectCreate => "story.project-create.v1",
            Self::ManuscriptCreate => "story.manuscript-create.v1",
            Self::ClaimReview => "story.claim-review-queue.v1",
            Self::ClaimDecide => "story.claim-review-result.v1",
            Self::MaterializationPlan => "story.materialization-plan.v1",
            Self::MaterializationStage => "story.materialization-result.v1",
            Self::CanonCommitPlan => "story.canon-commit-plan.v1",
            Self::CanonCommit => "story.canon-commit-command-result.v1",
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProductRequest {
    action: ProductAction,
    project: String,
    name: Option<String>,
    language: Option<String>,
    title: Option<String>,
    season: Option<u32>,
    episode: Option<u32>,
    claim_id: Option<String>,
    decision: Option<String>,
    normalized_predicate: Option<String>,
    normalized_value: Option<Value>,
    note: Option<String>,
    replace: Option<bool>,
    confirm_sha256: Option<String>,
    actor: Option<String>,
}

fn plain(value: &str, label: &str, max_chars: usize) -> Result<String, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err(format!("{label} cannot be empty"));
    }
    if trimmed.chars().count() > max_chars {
        return Err(format!("{label} is too long"));
    }
    if trimmed.chars().any(|ch| matches!(ch, '\0' | '\r' | '\n')) {
        return Err(format!("{label} contains invalid control characters"));
    }
    Ok(trimmed.to_owned())
}

fn note(value: Option<&str>) -> Result<Option<String>, String> {
    match value {
        None => Ok(None),
        Some(raw) => {
            if raw.contains('\0') || raw.chars().count() > MAX_TEXT_CHARS {
                return Err("note contains invalid content".to_owned());
            }
            Ok(Some(raw.to_owned()))
        }
    }
}

fn valid_claim_id(value: &str) -> bool {
    let Some(body) = value.strip_prefix("clm_") else {
        return false;
    };
    body.len() == 32
        && body
            .chars()
            .all(|ch| ch.is_ascii_hexdigit() && !ch.is_ascii_uppercase())
}

fn claim_id(value: Option<&str>) -> Result<String, String> {
    let raw = plain(value.ok_or("claimId is required")?, "claim ID", 64)?;
    if !valid_claim_id(&raw) {
        return Err("claim ID has invalid typed-ID format".to_owned());
    }
    Ok(raw)
}

fn sha256(value: Option<&str>) -> Result<String, String> {
    let raw = plain(
        value.ok_or("confirmSha256 is required")?,
        "candidate SHA-256",
        64,
    )?;
    if raw.len() != 64
        || !raw
            .chars()
            .all(|ch| ch.is_ascii_hexdigit() && !ch.is_ascii_uppercase())
    {
        return Err("candidate SHA-256 must be 64 lowercase hexadecimal characters".to_owned());
    }
    Ok(raw)
}

fn require_absent(request: &ProductRequest, allowed: &[&str]) -> Result<(), String> {
    let fields = [
        ("name", request.name.is_some()),
        ("language", request.language.is_some()),
        ("title", request.title.is_some()),
        ("season", request.season.is_some()),
        ("episode", request.episode.is_some()),
        ("claimId", request.claim_id.is_some()),
        ("decision", request.decision.is_some()),
        ("normalizedPredicate", request.normalized_predicate.is_some()),
        ("normalizedValue", request.normalized_value.is_some()),
        ("note", request.note.is_some()),
        ("replace", request.replace.is_some()),
        ("confirmSha256", request.confirm_sha256.is_some()),
        ("actor", request.actor.is_some()),
    ];
    for (name, present) in fields {
        if present && !allowed.contains(&name) {
            return Err(format!("{} does not accept {name}", request.action.cli_name()));
        }
    }
    Ok(())
}

fn build_args(request: &ProductRequest) -> Result<Vec<String>, String> {
    let project = plain(&request.project, "project path", 4096)?;
    let mut args = vec![request.action.cli_name().to_owned(), project];

    match request.action {
        ProductAction::ProjectCreate => {
            require_absent(request, &["name", "language"])?;
            args.push("--name".to_owned());
            args.push(plain(
                request.name.as_deref().ok_or("project-create requires name")?,
                "project name",
                120,
            )?);
            if let Some(language) = request.language.as_deref() {
                args.push("--language".to_owned());
                args.push(plain(language, "project language", 32)?);
            }
        }
        ProductAction::ManuscriptCreate => {
            require_absent(request, &["title", "season", "episode"])?;
            let season = request.season.ok_or("manuscript-create requires season")?;
            let episode = request.episode.ok_or("manuscript-create requires episode")?;
            if !(1..=9999).contains(&season) || !(1..=9999).contains(&episode) {
                return Err("season and episode must be between 1 and 9999".to_owned());
            }
            args.push("--title".to_owned());
            args.push(plain(
                request.title.as_deref().ok_or("manuscript-create requires title")?,
                "manuscript title",
                120,
            )?);
            args.extend(["--season".to_owned(), season.to_string()]);
            args.extend(["--episode".to_owned(), episode.to_string()]);
        }
        ProductAction::ClaimReview
        | ProductAction::MaterializationPlan
        | ProductAction::CanonCommitPlan => {
            require_absent(request, &["claimId"])?;
            if let Some(raw) = request.claim_id.as_deref() {
                args.push("--claim".to_owned());
                args.push(claim_id(Some(raw))?);
            }
        }
        ProductAction::ClaimDecide => {
            require_absent(
                request,
                &[
                    "claimId",
                    "decision",
                    "normalizedPredicate",
                    "normalizedValue",
                    "note",
                    "replace",
                ],
            )?;
            args.push(claim_id(request.claim_id.as_deref())?);
            let decision = plain(
                request.decision.as_deref().ok_or("claim-decide requires decision")?,
                "review decision",
                64,
            )?;
            let accepts = matches!(
                decision.as_str(),
                "accept_event_candidate" | "accept_fact_candidate"
            );
            if !accepts && !matches!(decision.as_str(), "reject" | "defer") {
                return Err("unsupported claim review decision".to_owned());
            }
            args.extend(["--decision".to_owned(), decision]);
            if accepts {
                let predicate = plain(
                    request
                        .normalized_predicate
                        .as_deref()
                        .ok_or("accepted claim decision requires normalizedPredicate")?,
                    "normalized predicate",
                    256,
                )?;
                let value = request
                    .normalized_value
                    .as_ref()
                    .ok_or("accepted claim decision requires normalizedValue")?;
                args.extend(["--predicate".to_owned(), predicate]);
                args.extend([
                    "--value-json".to_owned(),
                    serde_json::to_string(value)
                        .map_err(|error| format!("failed to encode normalized value: {error}"))?,
                ]);
            } else if request.normalized_predicate.is_some() || request.normalized_value.is_some() {
                return Err("reject/defer decisions cannot include normalized target data".to_owned());
            }
            if let Some(value) = note(request.note.as_deref())? {
                args.extend(["--note".to_owned(), value]);
            }
            if request.replace.unwrap_or(false) {
                args.push("--replace".to_owned());
            }
        }
        ProductAction::MaterializationStage => {
            require_absent(request, &["claimId"])?;
            args.push(claim_id(request.claim_id.as_deref())?);
        }
        ProductAction::CanonCommit => {
            require_absent(request, &["claimId", "confirmSha256", "actor", "note"])?;
            args.push(claim_id(request.claim_id.as_deref())?);
            args.extend(["--confirm-sha256".to_owned(), sha256(request.confirm_sha256.as_deref())?]);
            args.extend([
                "--actor".to_owned(),
                plain(
                    request.actor.as_deref().ok_or("canon-commit requires actor")?,
                    "actor",
                    120,
                )?,
            ]);
            if let Some(value) = note(request.note.as_deref())? {
                args.extend(["--note".to_owned(), value]);
            }
        }
    }
    Ok(args)
}

fn workspace_binary() -> String {
    std::env::var("STORYOS_WORKSPACE_BIN").unwrap_or_else(|_| "storyos-workspace".to_owned())
}

fn run_product(args: &[String]) -> Result<std::process::Output, String> {
    Command::new(workspace_binary())
        .args(args)
        .output()
        .map_err(|error| format!("failed to start trusted storyos-workspace: {error}"))
}

fn verify_policy(action: ProductAction, value: &Value) -> Result<(), String> {
    let schema = value
        .get("schema")
        .and_then(Value::as_str)
        .ok_or("product response is missing schema")?;
    if schema != action.expected_schema() {
        return Err(format!(
            "product response schema mismatch; expected {}",
            action.expected_schema()
        ));
    }

    let policy = value
        .get("policy")
        .and_then(Value::as_object)
        .ok_or("product response is missing policy")?;

    match action {
        ProductAction::ProjectCreate => {
            if policy.get("project_mutation").and_then(Value::as_bool) != Some(true)
                || policy.get("create_only").and_then(Value::as_bool) != Some(true)
                || policy.get("canonical_mutation").and_then(Value::as_bool) != Some(false)
                || policy.get("staging_mutation").and_then(Value::as_bool) != Some(false)
            {
                return Err("project-create response violated create-only policy".to_owned());
            }
        }
        ProductAction::ManuscriptCreate => {
            if policy.get("manuscript_mutation").and_then(Value::as_bool) != Some(true)
                || policy.get("create_only").and_then(Value::as_bool) != Some(true)
                || policy.get("canonical_mutation").and_then(Value::as_bool) != Some(false)
                || policy.get("staging_mutation").and_then(Value::as_bool) != Some(false)
            {
                return Err("manuscript-create response violated create-only policy".to_owned());
            }
        }
        ProductAction::ClaimReview => {
            if policy
                .get("review_decisions_are_noncanonical")
                .and_then(Value::as_bool)
                != Some(true)
                || policy.get("materialization_is_separate").and_then(Value::as_bool)
                    != Some(true)
            {
                return Err("claim-review response violated review policy".to_owned());
            }
        }
        ProductAction::ClaimDecide => {
            if policy.get("review_mutation").and_then(Value::as_bool) != Some(true)
                || policy.get("canonical_mutation").and_then(Value::as_bool) != Some(false)
                || policy.get("staging_mutation").and_then(Value::as_bool) != Some(true)
            {
                return Err("claim-decide response violated non-canonical review policy".to_owned());
            }
        }
        ProductAction::MaterializationPlan => {
            if policy.get("quarantine_only").and_then(Value::as_bool) != Some(true)
                || policy.get("canonical_mutation").and_then(Value::as_bool) != Some(false)
                || policy.get("commit_required").and_then(Value::as_bool) != Some(true)
            {
                return Err("materialization-plan response violated quarantine policy".to_owned());
            }
        }
        ProductAction::MaterializationStage => {
            if policy.get("materialization_mutation").and_then(Value::as_bool) != Some(true)
                || policy.get("quarantine_only").and_then(Value::as_bool) != Some(true)
                || policy.get("canonical_mutation").and_then(Value::as_bool) != Some(false)
                || policy.get("staging_mutation").and_then(Value::as_bool) != Some(true)
            {
                return Err("materialization-stage response violated quarantine policy".to_owned());
            }
        }
        ProductAction::CanonCommitPlan => {
            if policy.get("canonical_mutation").and_then(Value::as_bool) == Some(true)
                || policy
                    .get("explicit_candidate_sha256_confirmation")
                    .and_then(Value::as_bool)
                    == Some(false)
            {
                return Err("canon-commit-plan response violated read-only commit planning policy".to_owned());
            }
        }
        ProductAction::CanonCommit => {
            if policy.get("canonical_mutation").and_then(Value::as_bool) != Some(true)
                || policy.get("canonical_create_only").and_then(Value::as_bool) != Some(true)
                || policy.get("canonical_overwrite").and_then(Value::as_bool) != Some(false)
                || policy
                    .get("explicit_candidate_sha256_confirmation")
                    .and_then(Value::as_bool)
                    != Some(true)
            {
                return Err("canon-commit response violated explicit create-only policy".to_owned());
            }
        }
    }
    Ok(())
}

fn parse_output(action: ProductAction, output: std::process::Output) -> Result<Value, String> {
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let mut message: String = stderr.chars().take(MAX_STDERR_CHARS).collect();
        if message.trim().is_empty() {
            message = format!("storyos-workspace exited with {}", output.status);
        }
        return Err(message.trim().to_owned());
    }
    if output.stdout.len() > MAX_STDOUT_BYTES {
        return Err("product response exceeded the desktop safety limit".to_owned());
    }
    let value: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("product response was not valid JSON: {error}"))?;
    verify_policy(action, &value)?;
    Ok(value)
}

#[tauri::command]
pub fn storyos_product(request: ProductRequest) -> Result<Value, String> {
    let action = request.action;
    let args = build_args(&request)?;
    parse_output(action, run_product(&args)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(action: ProductAction) -> ProductRequest {
        ProductRequest {
            action,
            project: "/tmp/project".to_owned(),
            name: None,
            language: None,
            title: None,
            season: None,
            episode: None,
            claim_id: None,
            decision: None,
            normalized_predicate: None,
            normalized_value: None,
            note: None,
            replace: None,
            confirm_sha256: None,
            actor: None,
        }
    }

    #[test]
    fn manuscript_create_requires_bounded_story_numbers() {
        let mut row = request(ProductAction::ManuscriptCreate);
        row.title = Some("Opening".to_owned());
        row.season = Some(1);
        row.episode = Some(0);
        assert!(build_args(&row).unwrap_err().contains("between 1 and 9999"));
    }

    #[test]
    fn accepted_claim_decision_requires_normalized_value() {
        let mut row = request(ProductAction::ClaimDecide);
        row.claim_id = Some(format!("clm_{}", "a".repeat(32)));
        row.decision = Some("accept_fact_candidate".to_owned());
        row.normalized_predicate = Some("identity.role".to_owned());
        assert!(build_args(&row)
            .unwrap_err()
            .contains("requires normalizedValue"));
    }

    #[test]
    fn canon_commit_requires_exact_sha() {
        let mut row = request(ProductAction::CanonCommit);
        row.claim_id = Some(format!("clm_{}", "a".repeat(32)));
        row.confirm_sha256 = Some("abc".to_owned());
        row.actor = Some("author".to_owned());
        assert!(build_args(&row).unwrap_err().contains("64 lowercase"));
    }
}
