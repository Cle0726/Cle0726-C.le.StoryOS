mod bridge;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![bridge::storyos_workspace])
        .run(tauri::generate_context!())
        .expect("failed to run C.le. StoryOS desktop app");
}
