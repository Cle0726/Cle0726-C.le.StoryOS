mod bridge;
mod scene;
mod session;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
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
