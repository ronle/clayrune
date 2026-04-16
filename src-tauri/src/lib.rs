use std::sync::Mutex;
use tauri::Manager;

struct FlaskProcess(Mutex<Option<std::process::Child>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // Spawn Flask server as a child process
            let resource_dir = app
                .path()
                .resource_dir()
                .unwrap_or_else(|_| std::path::PathBuf::from("."));

            // In dev mode, server.py is at the project root (parent of src-tauri)
            let server_path = if cfg!(debug_assertions) {
                let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
                manifest_dir.parent().unwrap().join("server.py")
            } else {
                resource_dir.join("server.py")
            };

            log::info!("Starting Flask server: {:?}", server_path);

            // Inherit stdout/stderr so Flask's output lands in the Tauri parent
            // terminal. Using piped() without a reader causes a deadlock once
            // the ~64 KB pipe buffer fills (print() blocks forever, then the
            // Python process may exit on BrokenPipeError). Inherit avoids the
            // buffer entirely and also gives us live tracebacks for debugging.
            let child = std::process::Command::new("python")
                .arg(&server_path)
                .stdout(std::process::Stdio::inherit())
                .stderr(std::process::Stdio::inherit())
                .spawn();

            match child {
                Ok(child) => {
                    log::info!("Flask server started with PID: {}", child.id());
                    app.manage(FlaskProcess(Mutex::new(Some(child))));
                }
                Err(e) => {
                    log::error!("Failed to start Flask server: {}", e);
                    app.manage(FlaskProcess(Mutex::new(None)));
                }
            }

            // Give Flask a moment to start
            std::thread::sleep(std::time::Duration::from_millis(1500));

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let app = window.app_handle();
                if let Some(state) = app.try_state::<FlaskProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(ref mut child) = *guard {
                            log::info!("Killing Flask server (PID: {})", child.id());
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                        *guard = None;
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
