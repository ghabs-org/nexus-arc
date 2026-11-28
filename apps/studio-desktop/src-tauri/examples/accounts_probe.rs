//! Local contract fixture only: no browser, Keychain or real account is touched.
#![allow(dead_code)]
#[path = "../src/accounts.rs"]
mod accounts;
use std::sync::atomic::AtomicBool;

fn main() {
    let port: u16 = std::env::args()
        .nth(1)
        .expect("fixture port")
        .parse()
        .expect("port");
    let server = reqwest::Url::parse(&format!("http://127.0.0.1:{port}/")).unwrap();
    let result = (|| -> Result<(), String> {
        // Explicit HTTP loopback test seam; the application validates HTTPS origins.
        let pending = accounts::PendingLogin::begin(server.clone())?;
        reqwest::blocking::Client::new()
            .post(server.join("auth/fixture/complete").unwrap())
            .send()
            .map_err(|_| "fixture unavailable")?;
        let credential = pending.finish(&AtomicBool::new(false))?;
        accounts::identity(&server, &credential)?;
        accounts::revoke(&server, &credential)?;
        Ok(())
    })();
    println!(
        "{}",
        serde_json::json!({"connected_and_revoked": result.is_ok()})
    );
    if result.is_err() {
        std::process::exit(1);
    }
}
