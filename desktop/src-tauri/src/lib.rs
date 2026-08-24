mod bridge;
mod scene;
mod session;

#[cfg(not(debug_assertions))]
fn pin_release_workspace_binary() {
    use std::path::PathBuf;

    let executable = match std::env::current_exe() {
        Ok(path) => path,
        Err(_) => return,
    };
    let Some(directory) = executable.parent() else {
        return;
    };

    #[cfg(target_os = "windows")]
    let filename = "storyos-workspace.exe";
    #[cfg(not(target_os = "windows"))]
    let filename = "storyos-workspace";

    let candidate = directory.join(filename);
    let trusted = candidate
        .symlink_metadata()
        .ok()
        .is_some_and(|metadata| metadata.file_type().is_file() && !metadata.file_type().is_symlink());

    // Release builds never inherit a caller-controlled STORYOS_WORKSPACE_BIN and never
    // fall back to PATH. When the bundled sibling is absent or unsafe, pin to a
    // deliberately missing path so commands fail closed instead of executing another
    // program named `storyos-workspace` from the user's environment.
    let pinned: PathBuf = if trusted {
        candidate
    } else {
        directory.join(".storyos-workspace-unavailable")
    };
    std::env::set_var("STORYOS_WORKSPACE_BIN", pinned);
}

#[cfg(debug_assertions)]
fn pin_release_workspace_binary() {
    // Development and Rust bridge tests intentionally keep the existing override so the
    // Python workspace CLI can be exercised without a bundled release sidecar.
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    pin_release_workspace_binary();
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            bridge::storyos_workspace,
            scene::storyos_scene_workspace,
            session::storyos_project_session,
            session::pick_project_directory,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run C.le. StoryOS desktop app");
}
