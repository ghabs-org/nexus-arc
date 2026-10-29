//! Explicit offline installer drill, restricted to a disposable task fixture.
use std::{env, fs, path::PathBuf};
use tauri_plugin_updater::UpdaterExt;

fn install() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if args.len() != 3 {
        return Err("Supply manifest, package and disposable application paths".into());
    }
    let target = PathBuf::from(&args[2]).canonicalize()?;
    let temporary = PathBuf::from("/private/tmp");
    let relative = target.strip_prefix(&temporary)?;
    let first = relative.components().next().ok_or("Missing fixture path")?;
    if !first
        .as_os_str()
        .to_string_lossy()
        .starts_with("nexus-update-acceptance-")
        || target.file_name().and_then(|value| value.to_str()) != Some("Nexus Studio.app")
        || !target.join("Contents/Info.plist").is_file()
    {
        return Err("Installer drill requires its own disposable fixture".into());
    }
    let config: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json"))?;
    let mut context = tauri::test::mock_context(tauri::test::noop_assets());
    context.config_mut().version = Some("1.0.1".into());
    context
        .config_mut()
        .plugins
        .0
        .insert("updater".into(), config["plugins"]["updater"].clone());
    let app = tauri::test::mock_builder()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .build(context)?;
    let updater = app
        .updater_builder()
        .target("darwin-aarch64")
        .executable_path(target.join("Contents/MacOS/nexus-studio-desktop"))
        .configure_client(|_| panic!("offline fixture must not use network"))
        .build()?;
    let metadata = serde_json::from_slice(&fs::read(PathBuf::from(&args[0]))?)?;
    let update = updater
        .restore_update(metadata)?
        .ok_or("No newer candidate")?;
    update.install(fs::read(PathBuf::from(&args[1]))?)?;
    Ok(())
}

fn main() {
    if install().is_err() {
        eprintln!("Disposable update installation failed.");
        std::process::exit(1);
    }
    println!("Verified update installed only into the disposable fixture.");
}
