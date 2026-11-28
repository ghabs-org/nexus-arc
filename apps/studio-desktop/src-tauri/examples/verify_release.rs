//! Explicit offline acceptance consumer of the production updater verifier.
//! It verifies signatures and version binding; it never installs or uses a network.
use std::{env, fs, path::PathBuf};
use tauri_plugin_updater::UpdaterExt;

fn verify() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if args.len() != 2 {
        return Err("Supply updater manifest and package paths".into());
    }
    let config: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json"))?;
    let mut context = tauri::test::mock_context(tauri::test::noop_assets());
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
        .executable_path("offline-acceptance/Nexus Studio.app/Contents/MacOS/studio")
        .configure_client(|_| panic!("offline verification must not use network"))
        .build()?;
    let metadata = serde_json::from_slice(&fs::read(PathBuf::from(&args[0]))?)?;
    let update = updater
        .restore_update(metadata)?
        .ok_or("No newer candidate")?;
    update.verify(&fs::read(PathBuf::from(&args[1]))?)?;
    Ok(())
}

fn main() {
    if verify().is_err() {
        eprintln!("Update signature or version verification failed.");
        std::process::exit(1);
    }
    println!("Update signature and signed version verified; nothing installed.");
}
