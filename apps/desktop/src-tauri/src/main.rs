#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use rand::{distributions::Alphanumeric, Rng};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{menu::{Menu, MenuItem}, tray::TrayIconBuilder, Manager};
use tauri_plugin_shell::{process::{CommandChild, CommandEvent}, ShellExt};

struct Backend {
    token: String,
    child: Mutex<Option<CommandChild>>,
    shutdown_ready: AtomicBool,
    shutdown_started: AtomicBool,
}

#[tauri::command]
fn backend_token(app: tauri::AppHandle, state: tauri::State<Backend>) -> Result<String, String> {
    let mut child = state.child.lock().map_err(|_| "Не удалось запустить локальный сервер")?;
    if child.is_none() {
        let storage = match std::env::var_os("STORAGE_DIR") {
            Some(path) => std::path::PathBuf::from(path),
            None => app.path().app_data_dir().map_err(|e| e.to_string())?,
        };
        std::fs::create_dir_all(&storage).map_err(|e| e.to_string())?;
        let (mut receiver, process) = app.shell().sidecar("pulse-backend").map_err(|e| e.to_string())?
            .set_process_group(true).kill_on_drop(true)
            .env("API_TOKEN", &state.token)
            .env("STORAGE_DIR", storage.to_string_lossy().to_string())
            .args(["--host", "127.0.0.1", "--port", "8765"])
            .spawn().map_err(|e| e.to_string())?;
        let process_id = process.pid();
        *child = Some(process);
        state.shutdown_ready.store(false, Ordering::SeqCst);
        state.shutdown_started.store(false, Ordering::SeqCst);
        let handle = app.clone();
        tauri::async_runtime::spawn(async move {
            while let Some(event) = receiver.recv().await {
                if matches!(event, CommandEvent::Terminated(_)) {
                    if let Some(state) = handle.try_state::<Backend>() {
                        let stopped = if let Ok(mut child) = state.child.lock() {
                            if child.as_ref().map(|process| process.pid()) == Some(process_id) {
                                drop(child.take());
                                true
                            } else { false }
                        } else { false };
                        if stopped {
                            state.shutdown_ready.store(true, Ordering::SeqCst);
                            state.shutdown_started.store(false, Ordering::SeqCst);
                            if let Some(window) = handle.get_webview_window("main") {
                                let _ = window.eval("window.dispatchEvent(new Event('local-backend-exited'))");
                            }
                        }
                    }
                    break;
                }
            }
        });
    }
    Ok(state.token.clone())
}

#[tauri::command]
async fn stop_local_backend(state: tauri::State<'_, Backend>) -> Result<(), String> {
    if state.child.lock().map_err(|_| "Ошибка состояния локального сервера")?.is_none() { return Ok(()); }
    let response = reqwest::Client::builder().timeout(std::time::Duration::from_secs(120))
        .build().map_err(|e| e.to_string())?
        .post("http://127.0.0.1:8765/api/offline/pause-local")
        .bearer_auth(&state.token).send().await
        .map_err(|_| "Не удалось остановить локальную автоматизацию. Завершите локальный сервер перед подключением к VPS.")?;
    if !response.status().is_success() {
        return Err("Локальная автоматизация не подтвердила остановку. Подключение к VPS отменено.".into());
    }
    if let Some(child) = state.child.lock().map_err(|_| "Ошибка состояния локального сервера")?.take() {
        child.kill().map_err(|e| e.to_string())?;
    }
    state.shutdown_ready.store(true, Ordering::SeqCst);
    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![backend_token, stop_local_backend])
        .setup(|app| {
            let token: String = rand::thread_rng().sample_iter(&Alphanumeric).take(64).map(char::from).collect();
            // Remote clients must not start a second copy of the business workers.
            app.manage(Backend { token, child: Mutex::new(None), shutdown_ready: AtomicBool::new(false), shutdown_started: AtomicBool::new(false) });
            let show = MenuItem::with_id(app, "show", "Открыть FunPay Plus", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            let mut tray = TrayIconBuilder::new().menu(&menu).tooltip("FunPay Plus")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => { if let Some(window) = app.get_webview_window("main") { let _ = window.show(); let _ = window.set_focus(); } }
                    "quit" => app.exit(0),
                    _ => {}
                });
            if let Some(icon) = app.default_window_icon() { tray = tray.icon(icon.clone()); }
            tray.build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("FunPay Plus failed to start");
    app.run(|app, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = &event {
            if let Some(backend) = app.try_state::<Backend>() {
                if backend.child.lock().map(|child| child.is_none()).unwrap_or(false) { return; }
                if !backend.shutdown_ready.load(Ordering::SeqCst) {
                    api.prevent_exit();
                    if backend.shutdown_started.swap(true, Ordering::SeqCst) {
                        return;
                    }
                    let handle = app.clone();
                    let token = backend.token.clone();
                    tauri::async_runtime::spawn(async move {
                        let outcome = reqwest::Client::builder()
                            .timeout(std::time::Duration::from_secs(120))
                            .build()
                            .expect("HTTP client")
                            .post("http://127.0.0.1:8765/api/offline/prepare-shutdown")
                            .bearer_auth(token)
                            .send().await;
                        if matches!(outcome, Ok(response) if response.status().is_success()) {
                            if let Some(state) = handle.try_state::<Backend>() {
                                state.shutdown_ready.store(true, Ordering::SeqCst);
                            }
                            handle.exit(0);
                        } else {
                            if let Some(state) = handle.try_state::<Backend>() {
                                state.shutdown_started.store(false, Ordering::SeqCst);
                            }
                            if let Some(window) = handle.get_webview_window("main") {
                                let _ = window.show();
                                let _ = window.set_focus();
                                let _ = window.eval("window.dispatchEvent(new Event('shutdown-error'))");
                            }
                        }
                    });
                }
            }
        }
        if let tauri::RunEvent::Exit = event {
            if let Some(backend) = app.try_state::<Backend>() {
                if let Ok(mut guard) = backend.child.lock() {
                    if let Some(child) = guard.take() { let _ = child.kill(); }
                }
            }
        }
    });
}
