//! Nexus Studio Desktop Shell (Tauri 2).
//!
//! Own the sidecar until exit and keep the startup view on any failure. Open the
//! webview only after the backend proves this launch's secret, with an HttpOnly session.
//! The sidecar holds the workspace lock; closing asks it to finish cleanly first.
//! The Workspace menu opens, imports or reveals a workspace by switching backends.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod backend;
mod updates;
use backend::{BackendOwner, ManagedChild, SavedWorkspace};
use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::webview::{cookie::SameSite, Cookie};
use tauri::{AppHandle, Manager, WebviewWindow};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_shell::{
    process::{Command, CommandChild, CommandEvent},
    ShellExt,
};

type Owner = Arc<BackendOwner<CommandChild>>;
impl ManagedChild for CommandChild {
    /// Any byte on the private stdin pipe asks the sidecar to close its workspace;
    /// pipe EOF does the same if this shell dies without stopping it.
    fn request_exit(&mut self) {
        let _ = self.write(b"stop\n");
    }

    fn stop(self) -> Result<(), String> {
        self.kill()
            .map_err(|_| "Could not stop the owned backend".into())
    }
}

/// The bundled local startup page, captured before the first navigation away from it.
struct StartupPage(tauri::Url);

/// The workspace served by the verified backend; workspace switches run one at a time.
#[derive(Default)]
struct Workspace {
    current: Mutex<Option<PathBuf>>,
    switching: Mutex<()>,
}

fn main() {
    let owner: Owner = Arc::new(BackendOwner::default());
    tauri::Builder::default()
        .manage(owner)
        .manage(Workspace::default())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let window = app
                .get_webview_window("main")
                .ok_or("Main window missing")?;
            app.manage(StartupPage(window.url()?));
            app.set_menu(workspace_menu(app.handle())?)?;
            let handle = app.handle().clone();
            std::thread::spawn(move || match initial_workspace(&handle) {
                Ok(folder) => start_backend(&handle, folder),
                Err(code) => show_failure(&handle, code),
            });
            Ok(())
        })
        .on_menu_event(|app, event| {
            let (app, id) = (app.clone(), event.id().as_ref().to_owned());
            // Dialogs block, so never on the main thread.
            std::thread::spawn(move || match id.as_str() {
                "workspace-open" => open_workspace(&app),
                "workspace-import" => import_workspace(&app),
                "workspace-reveal" => reveal_workspace(&app),
                "studio-update" => updates::check(&app),
                _ => (),
            });
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                // Hide first: the clean close may take a few seconds.
                let _ = window.hide();
                let _ = window.app_handle().state::<Owner>().quit();
                window.app_handle().exit(0);
            }
        })
        .build(tauri::generate_context!())
        .expect("Failed to build Nexus Studio Desktop application")
        .run(|app, event| {
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
                let _ = app.state::<Owner>().quit();
            }
        });
}

fn workspace_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let reveal = if cfg!(target_os = "macos") {
        "Show Workspace in Finder"
    } else {
        "Open Workspace Folder"
    };
    let workspace = Submenu::with_items(
        app,
        "Workspace",
        true,
        &[
            &MenuItem::with_id(
                app,
                "workspace-open",
                "Open Workspace…",
                true,
                Some("CmdOrCtrl+O"),
            )?,
            &MenuItem::with_id(
                app,
                "workspace-import",
                "Import Workspace…",
                true,
                None::<&str>,
            )?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "workspace-reveal", reveal, true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(
                app,
                "studio-update",
                "Check for Updates…",
                true,
                None::<&str>,
            )?,
        ],
    )?;
    let menu = Menu::default(app)?;
    // After the macOS app menu and File; after File elsewhere.
    menu.insert(&workspace, if cfg!(target_os = "macos") { 2 } else { 1 })?;
    Ok(menu)
}

fn settings_file(app: &AppHandle) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|dir| dir.join("desktop.json"))
}

fn initial_workspace(app: &AppHandle) -> Result<PathBuf, &'static str> {
    match settings_file(app).map(|file| backend::saved_workspace(&file)) {
        Some(SavedWorkspace::Available(folder)) => Ok(folder),
        Some(SavedWorkspace::Missing) => Err("workspace-missing"),
        _ => app
            .path()
            .app_data_dir()
            .ok()
            .map(|dir| dir.join("workspace"))
            .filter(|folder| std::fs::create_dir_all(folder).is_ok())
            .ok_or("workspace"),
    }
}

fn show_failure(app: &AppHandle, code: &str) {
    let _ = app.state::<Owner>().stop();
    let mut startup = app.state::<StartupPage>().0.clone();
    startup.set_query(Some(&format!("error={code}")));
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.navigate(startup);
    }
}

fn pick_folder(app: &AppHandle, title: &str) -> Option<PathBuf> {
    app.dialog()
        .file()
        .set_title(title)
        .blocking_pick_folder()?
        .as_path()
        .map(Path::to_path_buf)
}

/// Stop the current backend cleanly, then serve `folder`. The choice is remembered
/// only once the new backend is verified (see `start_backend`).
fn switch_workspace(app: &AppHandle, folder: PathBuf) {
    let state = app.state::<Workspace>();
    let Ok(_one_at_a_time) = state.switching.try_lock() else {
        return;
    };
    let owner = app.state::<Owner>().inner().clone();
    let _ = owner.stop();
    if !owner.reopen() {
        return; // The app is quitting.
    }
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.navigate(app.state::<StartupPage>().0.clone());
    }
    start_backend(app, folder);
}

fn open_workspace(app: &AppHandle) {
    if let Some(folder) = pick_folder(app, "Open Workspace") {
        switch_workspace(app, folder);
    }
}

/// Copy an existing workspace into the app's own folder (the original is never
/// modified), using the framework's import checks, then open the copy.
fn import_workspace(app: &AppHandle) {
    let Some(source) = pick_folder(app, "Import Workspace") else {
        return;
    };
    let Ok(base) = app.path().app_data_dir().map(|dir| dir.join("workspaces")) else {
        return;
    };
    let target = backend::import_destination(&base, &source);
    match run_import(app, &source, &target) {
        Ok(()) => switch_workspace(app, target),
        Err(message) => {
            app.dialog()
                .message(message)
                .kind(MessageDialogKind::Error)
                .title("Import failed")
                .blocking_show();
        }
    }
}

fn run_import(app: &AppHandle, source: &Path, target: &Path) -> Result<(), String> {
    let command = backend_command(app)
        .map_err(|_| "The bundled backend is missing; reinstall Nexus Studio.".to_string())?
        .args([
            OsStr::new("--nexus-desktop"),
            OsStr::new("import"),
            OsStr::new("--from"),
            source.as_os_str(),
            OsStr::new("--dir"),
            target.as_os_str(),
            OsStr::new("--json"),
        ]);
    let output = tauri::async_runtime::block_on(command.output())
        .map_err(|_| "The import could not start.".to_string())?;
    let result = serde_json::from_slice::<serde_json::Value>(&output.stdout).unwrap_or_default();
    if output.status.success() && result.get("success") == Some(&serde_json::Value::Bool(true)) {
        return Ok(());
    }
    Err(result
        .get("error")
        .and_then(|error| error.as_str())
        .map(|error| error.chars().take(300).collect())
        .unwrap_or_else(|| "The import did not complete.".into()))
}

fn reveal_workspace(app: &AppHandle) {
    let current = app
        .state::<Workspace>()
        .current
        .lock()
        .ok()
        .and_then(|c| c.clone());
    let Some(folder) = current else {
        return;
    };
    let program = if cfg!(target_os = "macos") {
        "open"
    } else if cfg!(windows) {
        "explorer"
    } else {
        "xdg-open"
    };
    let _ = std::process::Command::new(program).arg(folder).status();
}

/// Give only the verified backend origin its session, and confirm before navigating.
fn install_session(window: &WebviewWindow, value: &str) -> bool {
    let cookie = Cookie::build((backend::SESSION_COOKIE, value.to_owned()))
        .domain("127.0.0.1")
        .path("/")
        .http_only(true)
        .same_site(SameSite::Strict)
        .build();
    let desktop_cookie = Cookie::build(("nexus_desktop", "1"))
        .domain("127.0.0.1")
        .path("/")
        .http_only(false)
        .same_site(SameSite::Strict)
        .build();
    let _ = window.set_cookie(desktop_cookie);
    window.set_cookie(cookie).is_ok()
        // wry's cookies_for_url never matches IP hosts, so read the store directly.
        && window.cookies().is_ok_and(|cookies| {
            cookies.iter().any(|c| {
                c.name() == backend::SESSION_COOKIE
                    && c.value() == value
                    && c.domain() == Some("127.0.0.1")
            })
        })
}

/// The bundled one-folder backend ships as the `sidecar` resource. Only development
/// builds may fall back to the source-checkout wrapper; a release never does.
fn backend_command(app: &AppHandle) -> Result<Command, tauri_plugin_shell::Error> {
    let name = if cfg!(windows) {
        "nexus-sidecar.exe"
    } else {
        "nexus-sidecar"
    };
    if let Ok(bundled) = app
        .path()
        .resource_dir()
        .map(|d| d.join("sidecar").join(name))
    {
        if bundled.is_file() {
            return Ok(app.shell().command(bundled));
        }
    }
    if cfg!(debug_assertions) {
        return app.shell().sidecar("nexus-sidecar");
    }
    Err(std::io::Error::from(std::io::ErrorKind::NotFound).into())
}

fn start_backend(app: &AppHandle, folder: PathBuf) {
    let owner = app.state::<Owner>().inner().clone();
    let Ok(workspace) = folder.canonicalize() else {
        show_failure(app, "workspace");
        return;
    };
    // A bind race is handled by failed readiness, never by selecting an unrelated service.
    let port = match std::net::TcpListener::bind("127.0.0.1:0").and_then(|l| l.local_addr()) {
        Ok(address) => address.port(),
        Err(_) => {
            show_failure(app, "port");
            return;
        }
    };
    let Some(credential) = backend::LaunchCredential::generate() else {
        show_failure(app, "session");
        return;
    };
    let spawned = backend_command(app).and_then(|command| {
        command
            .args([
                "--dir",
                workspace.to_string_lossy().as_ref(),
                "--port",
                &port.to_string(),
                "--host",
                "127.0.0.1",
                "--desktop-launch-stdin",
            ])
            .set_process_group(true)
            .spawn()
    });
    let (mut events, mut child) = match spawned {
        Ok(pair) => pair,
        Err(_) => {
            show_failure(app, "spawn");
            return;
        }
    };
    // The launch secret travels only over the owned child's stdin pipe.
    if child.write(credential.stdin_line().as_bytes()).is_err() {
        let _ = child.kill();
        show_failure(app, "spawn");
        return;
    }
    if owner.install(child).is_err() {
        return;
    }
    let event_owner = owner.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            // Drain stdout/stderr so the child cannot block on full output pipes.
            // Do not copy raw provider output or credential-bearing errors into the UI.
            match event {
                CommandEvent::Terminated(payload) => {
                    event_owner.terminated(payload.code);
                    break;
                }
                CommandEvent::Error(_) => {
                    event_owner.terminated(None);
                    break;
                }
                _ => (),
            }
        }
        event_owner.terminated(None);
    });
    let workspace_text = workspace.to_string_lossy();
    if backend::wait_for_backend(
        port,
        &workspace_text,
        &credential,
        Duration::from_secs(20),
        || owner.running(),
    )
    .is_err()
    {
        let code = match owner.exit_code() {
            Some(backend::WORKSPACE_IN_USE_EXIT) => "in-use",
            Some(backend::NEWER_WORKSPACE_EXIT) => "newer-workspace",
            Some(backend::UNSAFE_WORKSPACE_EXIT) => "unsafe-workspace",
            _ => "readiness",
        };
        show_failure(app, code);
        return;
    }
    if !owner.running() {
        show_failure(app, "readiness");
        return;
    }
    let Some(window) = app.get_webview_window("main") else {
        let _ = owner.stop();
        return;
    };
    let Ok(url) = format!("http://127.0.0.1:{port}/?desktop=1").parse::<tauri::Url>() else {
        show_failure(app, "navigation");
        return;
    };
    if !install_session(&window, &credential.session()) {
        show_failure(app, "session");
        return;
    }
    if window.navigate(url).is_err() {
        show_failure(app, "navigation");
        return;
    }
    // Remember only a verified workspace; a failed save just means no memory next time.
    if let Some(file) = settings_file(app) {
        let _ = backend::save_workspace(&file, &workspace);
    }
    if let Ok(mut current) = app.state::<Workspace>().current.lock() {
        *current = Some(workspace);
    }
}
