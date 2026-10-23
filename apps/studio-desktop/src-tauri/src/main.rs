//! Nexus Studio Desktop Shell (Tauri 2)
//!
//! Prototype shell: backend process spawning, authenticated session setup and
//! workspace lifecycle integration remain unimplemented. Python helpers are
//! not yet wired into this native application.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicU16, Ordering};
use std::time::Duration;
use tauri::{AppHandle, Manager, WebviewUrl};

static BACKEND_PORT: AtomicU16 = AtomicU16::new(0);

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                start_backend_and_navigate(&handle);
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                // Terminate owned processes cleanly upon window closure
                let app = window.app_handle();
                app.exit(0);
            }
        })
        .run(tauri::generate_context!())
        .expect("Failed to run Nexus Studio Desktop application");
}

fn start_backend_and_navigate(app: &AppHandle) {
    // 1. Find free loopback port
    let port = find_free_port();
    BACKEND_PORT.store(port, Ordering::SeqCst);

    // 2. Poll bootstrap readiness
    let url = format!("http://127.0.0.1:{}/api/bootstrap", port);
    let mut attempts = 0;
    while attempts < 30 {
        std::thread::sleep(Duration::from_millis(500));
        if let Ok(resp) = reqwest::blocking::get(&url) {
            if resp.status().is_success() {
                break;
            }
        }
        attempts += 1;
    }

    // 3. Direct webview to local Studio URL
    if let Some(window) = app.get_webview_window("main") {
        let dest = format!("http://127.0.0.1:{}", port);
        if let Ok(parsed_url) = dest.parse() {
            let _ = window.navigate(WebviewUrl::External(parsed_url));
        }
    }
}

fn find_free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .unwrap_or(5003)
}
