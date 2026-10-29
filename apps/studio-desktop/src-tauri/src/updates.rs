//! Updates originate only from a native menu, never a remote webview command.
use crate::{backend_command, start_backend, Owner, Workspace};
use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

fn notice(app: &AppHandle, message: &str) {
    app.dialog()
        .message(message)
        .title("Studio updates")
        .blocking_show();
}

fn application_path() -> Result<PathBuf, &'static str> {
    let executable = std::env::current_exe().map_err(|_| "Application path unavailable")?;
    executable
        .ancestors()
        .find(|path| path.extension() == Some(OsStr::new("app")))
        .map(Path::to_path_buf)
        .ok_or("Updates require an installed macOS application")
}

fn backup(app: &AppHandle, workspace: &Path, application: &Path) -> Result<(), &'static str> {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "Clock unavailable")?
        .as_nanos();
    let destination = app
        .path()
        .app_data_dir()
        .map_err(|_| "Backup folder unavailable")?
        .join("updates")
        .join(format!("before-{}-{stamp}", app.package_info().version));
    let command = backend_command(app)
        .map_err(|_| "Bundled backup tool unavailable")?
        .args([
            OsStr::new("--nexus-desktop"),
            OsStr::new("prepare-update"),
            OsStr::new("--dir"),
            workspace.as_os_str(),
            OsStr::new("--output"),
            destination.as_os_str(),
            OsStr::new("--application"),
            application.as_os_str(),
        ]);
    let output =
        tauri::async_runtime::block_on(command.output()).map_err(|_| "Backup could not start")?;
    let result: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap_or_default();
    if !output.status.success() || result.get("success") != Some(&serde_json::Value::Bool(true)) {
        return Err("Backup failed. The update was not installed.");
    }
    Ok(())
}

pub fn check(app: &AppHandle) {
    if !cfg!(target_os = "macos") {
        notice(app, "Installed updates on this platform await native release validation. Download a tested installer instead.");
        return;
    }
    let state = app.state::<Workspace>();
    let Ok(_exclusive) = state.switching.try_lock() else {
        return;
    };
    let workspace = state
        .current
        .lock()
        .ok()
        .and_then(|current| current.clone());
    let Some(workspace) = workspace else {
        notice(
            app,
            "Open a healthy local workspace before checking for updates.",
        );
        return;
    };
    let Ok(application) = application_path() else {
        notice(app, "Updates require an installed macOS application.");
        return;
    };
    let update = tauri::async_runtime::block_on(async {
        app.updater_builder()
            .timeout(Duration::from_secs(30))
            .build()?
            .check()
            .await
    });
    let update = match update {
        Ok(Some(update)) => update,
        Ok(None) => {
            notice(app, "You have the latest available release.");
            return;
        }
        Err(_) => {
            notice(
                app,
                "The release feed is unavailable. No update was installed.",
            );
            return;
        }
    };
    // The upstream plugin warns about plaintext artifact URLs; Nexus rejects them.
    if update.download_url.scheme() != "https" {
        notice(
            app,
            "The update download must use HTTPS. No update was installed.",
        );
        return;
    }
    let notes: String = update
        .body
        .as_deref()
        .unwrap_or("No release notes provided.")
        .chars()
        .take(4000)
        .collect();
    let confirmed = app.dialog().message(format!(
        "Studio {} is available.\n\n{notes}\n\nFinish active work first. Studio will verify the download, stop the local server, and preserve your workspace and current application before installing. Keep the backup until the new version is tested.", update.version
    )).title("Update Nexus Studio").kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom("Download and update".into(), "Cancel".into())).blocking_show();
    if !confirmed {
        return;
    }
    let bytes = match tauri::async_runtime::block_on(update.download(|_, _| {}, || {})) {
        Ok(bytes) => bytes,
        Err(_) => {
            notice(app, "Download or signature verification failed. The current application was not changed.");
            return;
        }
    };
    let owner = app.state::<Owner>().inner().clone();
    if owner.stop().is_err() {
        notice(app, "Studio could not confirm server shutdown. No update was installed. Reopen Studio to continue.");
        return;
    }
    if !owner.reopen() {
        return;
    }
    let installed = backup(app, &workspace, &application).and_then(|_| {
        update.install(&bytes).map_err(|_| "Update installation failed. Your workspace backup and previous application were retained.")
    });
    match installed {
        Ok(()) => app.restart(),
        Err(message) => {
            notice(app, message);
            start_backend(app, workspace);
        }
    }
}

#[cfg(test)]
mod tests {
    use base64::Engine;
    use serde_json::json;
    use tauri_plugin_updater::UpdaterExt;

    // Public, throwaway upstream test vector; never a production signing key.
    const KEY: &str = "untrusted comment: minisign public key: A49EF4746FA6E6D7\nRWTX5qZvdPSepDGWg2bz8n6ArQnBMwgQK1geVosorcbLzcunD+KJ+cYe";
    const SIGNATURE: &str = "untrusted comment: signature from tauri secret key\nRUTX5qZvdPSepPF2zsjvP/L/TQtBLRnWRvaq+4omK1UEugtQp/USrGiI1v762sDy3id3aor7bRC9ETyeot6lMHHrWoZ5hjUM1Qc=\ntrusted comment: timestamp:1791649051\tfile:payload\tversion:2.0.0\nDDq2CLOEl5cQmC2eJRHlnZPVViOZsu1CjpPKQoei4A/XBfkDqGO0B+QMrcrHgEQ5qV3mne75tdFWhJpqUXh9AA==";

    fn verify(version: &str, payload: &[u8], signature: &str) -> bool {
        let mut context = tauri::test::mock_context(tauri::test::noop_assets());
        context.config_mut().plugins.0.insert(
            "updater".into(),
            json!({
                "pubkey": base64::engine::general_purpose::STANDARD.encode(KEY),
                "requireSignedVersion": true,
                "endpoints": ["https://example.invalid/update"]
            }),
        );
        let app = tauri::test::mock_builder()
            .plugin(tauri_plugin_updater::Builder::new().build())
            .build(context)
            .unwrap();
        let updater = app
            .updater_builder()
            .target("test-target")
            .executable_path("runtime-path/app")
            .configure_client(|_| panic!("offline verification must not use network"))
            .build()
            .unwrap();
        let update = updater.restore_update(json!({
            "version": version,
            "platforms": {"test-target": {"url": "https://example.invalid/package", "signature": base64::engine::general_purpose::STANDARD.encode(signature)}}
        })).unwrap().unwrap();
        update.verify(payload).is_ok()
    }

    #[test]
    fn official_verifier_accepts_signed_bytes_and_rejects_corruption() {
        assert!(verify("2.0.0", b"test", SIGNATURE));
        assert!(!verify("2.0.0", b"tampered", SIGNATURE));
    }

    #[test]
    fn announced_new_version_cannot_relabel_an_older_signed_release() {
        assert!(!verify("9.0.0", b"test", SIGNATURE));
        assert!(!verify("2.0.0", b"test", "invalid"));
    }
}
