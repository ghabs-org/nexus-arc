//! Opt-in synthetic Keychain round trip; never touches a real server/account.
#![allow(dead_code)]
#[path = "../src/accounts.rs"]
mod accounts;
fn main() -> Result<(), String> {
    let server = accounts::server_origin("https://nexus-desktop-keychain-fixture.invalid")?;
    if accounts::load(&server)?.is_some() {
        return Err("Fixture entry already exists; refusing to replace it.".into());
    }
    let credential = accounts::Credential {
        token: "x".repeat(43),
        device_id: "synthetic-device".into(),
        nexus_id: "synthetic-user".into(),
        expires_at: 1.0,
    };
    accounts::save(&server, &credential)?;
    let loaded = accounts::load(&server);
    // Always remove only this newly created synthetic entry, even on assertion failure.
    let removed = accounts::forget(&server);
    let restored = loaded?.ok_or("Fixture credential missing")?;
    removed?;
    if restored.token != credential.token
        || restored.device_id != credential.device_id
        || accounts::load(&server)?.is_some()
    {
        return Err("Keychain round trip failed.".into());
    }
    println!("{{\"keychain_round_trip\":true,\"synthetic_entry_removed\":true}}");
    Ok(())
}
