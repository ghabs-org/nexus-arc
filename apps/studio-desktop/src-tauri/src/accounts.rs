//! Optional Nexus identity, separate from the local desktop launch credential.
//! Only single-use PKCE codes pass through the loopback listener. Nexus device
//! tokens remain in native memory and the OS credential store, never the webview.

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use reqwest::blocking::{Client, Response};
use reqwest::{redirect::Policy, Url};
use ring::{
    digest,
    rand::{SecureRandom, SystemRandom},
};
use serde::{Deserialize, Serialize};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

const PATH: &str = "/nexus/desktop/callback";
const KEYCHAIN_SERVICE: &str = "com.nexusarc.studio.account.v1";
const MAX_REPLY: u64 = 16 * 1024;

pub fn server_origin(value: &str) -> Result<Url, String> {
    let url = Url::parse(value).map_err(|_| "Choose an HTTPS Nexus server.")?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("Choose an HTTPS Nexus server origin without a path or credentials.".into());
    }
    Ok(url)
}

fn client() -> Result<Client, String> {
    Client::builder()
        .redirect(Policy::none())
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|_| "Could not prepare the Nexus connection.".into())
}

fn random() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    SystemRandom::new()
        .fill(&mut bytes)
        .map_err(|_| "Could not secure the sign-in attempt.")?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

fn json<T: for<'a> Deserialize<'a>>(reply: Response) -> Result<T, String> {
    if !reply.status().is_success() {
        return Err(if reply.status().as_u16() == 401 {
            "Nexus session expired or was revoked. Sign in again."
        } else {
            "Nexus could not complete this request. Start sign-in again."
        }
        .into());
    }
    let mut bytes = Vec::new();
    reply
        .take(MAX_REPLY + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "Nexus response was interrupted.")?;
    if bytes.len() as u64 > MAX_REPLY {
        return Err("Nexus response is too large.".into());
    }
    serde_json::from_slice(&bytes).map_err(|_| "Nexus returned an invalid account response.".into())
}

#[derive(Deserialize)]
struct StartReply {
    attempt_id: String,
    authorization_url: String,
}

#[derive(Serialize, Deserialize)]
pub struct Credential {
    pub token: String,
    pub device_id: String,
    pub nexus_id: String,
    pub expires_at: f64,
}

#[derive(Deserialize)]
pub struct Identity {
    pub account: String,
    pub nexus_id: String,
    pub device_id: String,
    pub expires_at: f64,
    pub projects: Vec<String>,
}

pub struct PendingLogin {
    listener: TcpListener,
    client: Client,
    server: Url,
    redirect: String,
    state: String,
    verifier: String,
    attempt: String,
    pub authorization_url: Url,
}

impl PendingLogin {
    pub fn begin(server: Url) -> Result<Self, String> {
        let listener = TcpListener::bind("127.0.0.1:0")
            .map_err(|_| "Could not open the local sign-in listener.")?;
        listener
            .set_nonblocking(true)
            .map_err(|_| "Could not secure the local sign-in listener.")?;
        let port = listener
            .local_addr()
            .map_err(|_| "Could not read the local listener.")?
            .port();
        let redirect = format!("http://127.0.0.1:{port}{PATH}");
        let (state, verifier, client) = (random()?, random()?, client()?);
        let challenge =
            URL_SAFE_NO_PAD.encode(digest::digest(&digest::SHA256, verifier.as_bytes()));
        let reply: StartReply = json(
            client
                .post(server.join("auth/desktop/start").unwrap())
                .json(&serde_json::json!({"redirect_uri":redirect,"state":state,
                "code_challenge":challenge,"device_name":"Nexus Studio desktop"}))
                .send()
                .map_err(|_| "Could not reach the Nexus account server.")?,
        )?;
        let authorization_url =
            Url::parse(&reply.authorization_url).map_err(|_| "Invalid Nexus sign-in address.")?;
        if authorization_url.origin() != server.origin()
            || authorization_url.path() != "/auth/desktop/authorize"
            || !authorization_url.username().is_empty()
            || authorization_url.password().is_some()
            || authorization_url.fragment().is_some()
            || authorization_url.query_pairs().collect::<Vec<_>>()
                != [("attempt_id".into(), reply.attempt_id.clone().into())]
        {
            return Err("Nexus returned an unexpected sign-in destination.".into());
        }
        Ok(Self {
            listener,
            client,
            server,
            redirect,
            state,
            verifier,
            attempt: reply.attempt_id,
            authorization_url,
        })
    }

    pub fn finish(&self, cancel: &AtomicBool) -> Result<Credential, String> {
        let code = self.wait_for_code(cancel, Duration::from_secs(600))?;
        if cancel.load(Ordering::SeqCst) {
            return Err("Sign-in cancelled.".into());
        }
        // Never retry code exchange: a lost response may already have consumed it.
        let credential: Credential = json(
            self.client
                .post(self.server.join("auth/desktop/exchange").unwrap())
                .json(&serde_json::json!({"attempt_id":self.attempt,"code":code,
                "code_verifier":self.verifier,"redirect_uri":self.redirect}))
                .send()
                .map_err(|_| "Nexus code exchange was interrupted. Start sign-in again.")?,
        )?;
        if credential.token.len() != 43
            || credential.nexus_id.is_empty()
            || credential.device_id.is_empty()
        {
            return Err("Nexus returned an invalid device credential.".into());
        }
        let identity = match identity(&self.server, &credential) {
            Ok(identity) => identity,
            Err(error) => {
                let _ = revoke(&self.server, &credential);
                return Err(error);
            }
        };
        if identity.nexus_id != credential.nexus_id || identity.device_id != credential.device_id {
            let _ = revoke(&self.server, &credential);
            return Err("Nexus returned a different device identity.".into());
        }
        if cancel.load(Ordering::SeqCst) {
            let _ = revoke(&self.server, &credential);
            return Err("Sign-in cancelled.".into());
        }
        Ok(credential)
    }

    fn wait_for_code(&self, cancel: &AtomicBool, timeout: Duration) -> Result<String, String> {
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline && !cancel.load(Ordering::SeqCst) {
            match self.listener.accept() {
                Ok((mut stream, peer)) if peer.ip().is_loopback() => {
                    if let Some(result) = callback(&mut stream, &self.redirect, &self.state) {
                        return result;
                    }
                }
                Ok(_) => (),
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => (),
                Err(_) => return Err("The local sign-in listener stopped.".into()),
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        Err(if cancel.load(Ordering::SeqCst) {
            "Sign-in cancelled."
        } else {
            "Sign-in expired. Start again."
        }
        .into())
    }
}

fn callback(
    stream: &mut TcpStream,
    redirect: &str,
    expected_state: &str,
) -> Option<Result<String, String>> {
    // macOS can inherit O_NONBLOCK from the listener. Wait for request bytes,
    // bounded by the timeouts, instead of dropping a just-accepted connection.
    stream.set_nonblocking(false).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .ok()?;
    let mut bytes = Vec::new();
    let mut chunk = [0; 1024];
    let deadline = Instant::now() + Duration::from_secs(2);
    while bytes.len() < 4096 && !bytes.windows(4).any(|part| part == b"\r\n\r\n") {
        if Instant::now() >= deadline {
            return None;
        }
        stream
            .set_read_timeout(Some(deadline.saturating_duration_since(Instant::now())))
            .ok()?;
        let length = stream.read(&mut chunk).ok()?;
        if length == 0 {
            break;
        }
        bytes.extend_from_slice(&chunk[..length]);
    }
    let expected = Url::parse(redirect).ok()?;
    let result = parse_callback(&bytes, &expected, expected_state);
    let valid = result.is_some();
    let body = if valid {
        "Return to Nexus Studio to finish sign-in. You may close this tab."
    } else {
        "This request does not match a Nexus Studio sign-in."
    };
    let status = if valid { "200 OK" } else { "400 Bad Request" };
    let reply = format!("HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n{body}",body.len());
    let _ = stream.write_all(reply.as_bytes());
    result
}

fn parse_callback(
    bytes: &[u8],
    redirect: &Url,
    expected_state: &str,
) -> Option<Result<String, String>> {
    if bytes.len() > 4096 {
        return None;
    }
    let request = std::str::from_utf8(bytes).ok()?;
    let mut lines = request.split("\r\n");
    let first = lines.next()?.split(' ').collect::<Vec<_>>();
    if first.len() != 3
        || first[0] != "GET"
        || first[2] != "HTTP/1.1"
        || !first[1].starts_with('/')
        || first[1].starts_with("//")
    {
        return None;
    }
    let hosts = lines
        .filter_map(|line| line.split_once(':'))
        .filter(|(name, _)| name.eq_ignore_ascii_case("host"))
        .map(|(_, value)| value.trim())
        .collect::<Vec<_>>();
    if hosts
        != [format!("127.0.0.1:{}", redirect.port()?)]
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>()
    {
        return None;
    }
    let url = redirect.join(first[1]).ok()?;
    if url.origin() != redirect.origin() || url.path() != PATH || url.fragment().is_some() {
        return None;
    }
    let pairs = url.query_pairs().collect::<Vec<_>>();
    let states = pairs
        .iter()
        .filter(|(k, _)| k == "state")
        .collect::<Vec<_>>();
    if states.len() != 1 || states[0].1.as_ref() != expected_state {
        return None;
    }
    let codes = pairs
        .iter()
        .filter(|(k, _)| k == "code")
        .collect::<Vec<_>>();
    let errors = pairs
        .iter()
        .filter(|(k, _)| k == "error")
        .collect::<Vec<_>>();
    if codes.len() == 1 && codes[0].1.len() == 43 && errors.is_empty() && pairs.len() == 2 {
        Some(Ok(codes[0].1.to_string()))
    } else if errors.len() == 1
        && errors[0].1 == "access_denied"
        && codes.is_empty()
        && pairs.len() == 2
    {
        Some(Err("Sign-in cancelled in the browser.".into()))
    } else {
        None
    }
}

pub fn identity(server: &Url, credential: &Credential) -> Result<Identity, String> {
    json(
        client()?
            .get(server.join("auth/desktop/session").unwrap())
            .bearer_auth(&credential.token)
            .send()
            .map_err(|_| "Could not verify the Nexus session. Check your connection.")?,
    )
}

pub fn revoke(server: &Url, credential: &Credential) -> Result<(), String> {
    let response = client()?
        .delete(server.join("auth/desktop/session").unwrap())
        .bearer_auth(&credential.token)
        .send()
        .map_err(|_| "Could not revoke the Nexus session. Reconnect and try again.")?;
    if response.status().as_u16() == 204 || response.status().as_u16() == 401 {
        Ok(())
    } else {
        Err("Nexus did not confirm sign-out. Your stored session was kept so you can retry.".into())
    }
}

#[cfg(target_os = "macos")]
pub fn load(server: &Url) -> Result<Option<Credential>, String> {
    match security_framework::passwords::get_generic_password(KEYCHAIN_SERVICE, server.as_str()) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(|_| "Stored Nexus credential is invalid.".into()),
        Err(error) if error.code() == -25300 => Ok(None),
        Err(_) => Err("Could not read the Nexus session from Keychain.".into()),
    }
}

#[cfg(target_os = "macos")]
pub fn save(server: &Url, credential: &Credential) -> Result<(), String> {
    security_framework::passwords::set_generic_password(
        KEYCHAIN_SERVICE,
        server.as_str(),
        &serde_json::to_vec(credential).map_err(|_| "Could not prepare the Nexus credential.")?,
    )
    .map_err(|_| "Keychain did not save the Nexus credential.".into())
}

#[cfg(target_os = "macos")]
pub fn forget(server: &Url) -> Result<(), String> {
    security_framework::passwords::delete_generic_password(KEYCHAIN_SERVICE, server.as_str())
        .map_err(|_| "Session revoked, but Keychain removal failed. Remove the Nexus Studio account entry in Keychain.".into())
}

#[cfg(not(target_os = "macos"))]
pub fn load(_: &Url) -> Result<Option<Credential>, String> {
    Err(
        "Native account storage is currently supported on macOS. Local Studio remains available."
            .into(),
    )
}
#[cfg(not(target_os = "macos"))]
pub fn save(_: &Url, _: &Credential) -> Result<(), String> {
    Err("OS credential storage is unavailable.".into())
}
#[cfg(not(target_os = "macos"))]
pub fn forget(_: &Url) -> Result<(), String> {
    Err("OS credential storage is unavailable.".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn request(query: &str, host: &str) -> Vec<u8> {
        format!("GET {PATH}?{query} HTTP/1.1\r\nHost: {host}\r\n\r\n").into_bytes()
    }
    #[test]
    fn opaque_server_and_credentials_rejected() {
        for url in [
            "http://nexus.test",
            "https://user@host",
            "https://host/path",
            "https://host/?token=secret",
            "https://host/#fragment",
        ] {
            assert!(server_origin(url).is_err());
        }
        assert_eq!(
            server_origin("https://arc.ghabs.me").unwrap().as_str(),
            "https://arc.ghabs.me/"
        );
    }

    #[test]
    fn cancellation_and_timeout_release_the_listener() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        listener.set_nonblocking(true).unwrap();
        let server = server_origin("https://nexus.example").unwrap();
        let pending = PendingLogin {
            listener,
            client: client().unwrap(),
            server: server.clone(),
            redirect: format!("http://{address}{PATH}"),
            state: "s".repeat(43),
            verifier: "v".repeat(43),
            attempt: "fixture".into(),
            authorization_url: server,
        };
        assert!(pending
            .wait_for_code(&AtomicBool::new(true), Duration::from_secs(30))
            .unwrap_err()
            .contains("cancelled"));
        assert!(pending
            .wait_for_code(&AtomicBool::new(false), Duration::from_millis(1))
            .unwrap_err()
            .contains("expired"));
        drop(pending);
        assert!(TcpListener::bind(address).is_ok());
    }

    #[test]
    fn callback_waits_for_request_bytes_after_accept() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        listener.set_nonblocking(true).unwrap();
        let redirect = format!("http://{address}{PATH}");
        let worker = std::thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(2);
            while Instant::now() < deadline {
                if let Ok((mut stream, _)) = listener.accept() {
                    return callback(&mut stream, &redirect, "expected");
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            None
        });
        let mut stream = TcpStream::connect(address).unwrap();
        std::thread::sleep(Duration::from_millis(100));
        let _ = stream.write_all(&request(
            &format!("state=expected&code={}", "a".repeat(43)),
            &address.to_string(),
        ));
        let result = worker.join().unwrap();
        assert!(matches!(result, Some(Ok(_))));
    }
    #[test]
    fn callback_binds_host_state_path_and_single_result() {
        let redirect = Url::parse("http://127.0.0.1:19234/nexus/desktop/callback").unwrap();
        let code = "a".repeat(43);
        assert_eq!(
            parse_callback(
                &request(&format!("state=expected&code={code}"), "127.0.0.1:19234"),
                &redirect,
                "expected"
            )
            .unwrap()
            .unwrap(),
            code
        );
        for query in [
            format!("state=wrong&code={code}"),
            format!("state=expected&state=expected&code={code}"),
            format!("state=expected&code={code}&error=access_denied"),
        ] {
            assert!(
                parse_callback(&request(&query, "127.0.0.1:19234"), &redirect, "expected")
                    .is_none()
            );
        }
        assert!(parse_callback(
            &request(&format!("state=expected&code={code}"), "evil.test"),
            &redirect,
            "expected"
        )
        .is_none());
        assert!(parse_callback(
            &request("state=expected&error=access_denied", "127.0.0.1:19234"),
            &redirect,
            "expected"
        )
        .unwrap()
        .is_err());
    }
}
