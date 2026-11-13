//! Native sidecar ownership and authenticated loopback readiness checks.
use ring::{
    hmac,
    rand::{SecureRandom, SystemRandom},
};
use std::path::{Path, PathBuf};
use std::sync::{Condvar, Mutex};
use std::time::{Duration, Instant};

/// Sidecar exit status for a workspace another Studio process already serves.
pub const WORKSPACE_IN_USE_EXIT: i32 = 75;
/// Sidecar exit status for a workspace saved by a newer Studio release (left unchanged).
pub const NEWER_WORKSPACE_EXIT: i32 = 65;
/// Sidecar exit status for a folder that must not become a workspace (home, system, app bundle).
pub const UNSAFE_WORKSPACE_EXIT: i32 = 64;

pub trait ManagedChild {
    /// Ask for a graceful close; `stop` still force-cleans the process group after.
    fn request_exit(&mut self) {}
    fn stop(self) -> Result<(), String>;
}

struct State<C> {
    child: Option<C>,
    closing: bool,
    terminated: bool,
    exit_code: Option<i32>,
    quitting: bool,
}

pub struct BackendOwner<C: ManagedChild> {
    state: Mutex<State<C>>,
    exited: Condvar,
    grace: Duration,
}

impl<C: ManagedChild> Default for BackendOwner<C> {
    fn default() -> Self {
        // Longer than the backend's own 10 s bounded close, so a slow but healthy
        // close is never cut off; the group kill is only for a truly hung backend.
        Self::with_grace(Duration::from_secs(12))
    }
}

impl<C: ManagedChild> BackendOwner<C> {
    pub fn with_grace(grace: Duration) -> Self {
        Self {
            state: Mutex::new(State {
                child: None,
                closing: false,
                terminated: false,
                exit_code: None,
                quitting: false,
            }),
            exited: Condvar::new(),
            grace,
        }
    }

    pub fn install(&self, child: C) -> Result<(), String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "Backend ownership unavailable")?;
        if state.closing || state.child.is_some() {
            drop(state);
            child.stop()?;
            return Err("Backend startup was cancelled".into());
        }
        state.child = Some(child);
        Ok(())
    }

    pub fn running(&self) -> bool {
        self.state
            .lock()
            .map(|s| s.child.is_some() && !s.closing && !s.terminated)
            .unwrap_or(false)
    }

    /// Record the leader's exit; the first reported code wins.
    pub fn terminated(&self, code: Option<i32>) {
        if let Ok(mut state) = self.state.lock() {
            if !state.terminated {
                state.exit_code = code;
            }
            state.terminated = true;
            self.exited.notify_all();
        }
    }

    pub fn exit_code(&self) -> Option<i32> {
        self.state.lock().ok().and_then(|s| s.exit_code)
    }

    /// Accept a new child after `stop`, e.g. to switch workspaces; never once quitting.
    pub fn reopen(&self) -> bool {
        let Ok(mut state) = self.state.lock() else {
            return false;
        };
        if state.quitting {
            return false;
        }
        state.closing = false;
        state.terminated = false;
        state.exit_code = None;
        true
    }

    /// Stop for good: the app is exiting, so no later switch may start a backend.
    pub fn quit(&self) -> Result<(), String> {
        if let Ok(mut state) = self.state.lock() {
            state.quitting = true;
        }
        self.stop()
    }

    /// Ask the child to close its workspace cleanly, wait up to the grace period
    /// for it to exit, then kill whatever remains of its process group.
    pub fn stop(&self) -> Result<(), String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "Backend ownership unavailable")?;
        state.closing = true;
        let Some(mut child) = state.child.take() else {
            return Ok(());
        };
        child.request_exit();
        let state = self
            .exited
            .wait_timeout_while(state, self.grace, |s| !s.terminated)
            .map_err(|_| "Backend ownership unavailable")?;
        drop(state);
        child.stop()
    }
}

impl<C: ManagedChild> Drop for BackendOwner<C> {
    fn drop(&mut self) {
        if let Ok(state) = self.state.get_mut() {
            if let Some(child) = state.child.take() {
                let _ = child.stop();
            }
        }
    }
}

#[derive(Debug, PartialEq)]
pub enum StartupError {
    Exited,
    Timeout,
    Transport,
}

pub const SESSION_COOKIE: &str = "nexus_desktop_session";

/// Workspace remembered from the last verified launch.
#[derive(Debug, PartialEq)]
pub enum SavedWorkspace {
    /// No (valid) choice saved: use the default app-data workspace.
    None,
    Available(PathBuf),
    /// Saved folder is gone (moved, deleted, disconnected drive); never silently replaced.
    Missing,
}

pub fn saved_workspace(settings: &Path) -> SavedWorkspace {
    let saved = std::fs::read_to_string(settings)
        .ok()
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .and_then(|value| value.get("workspace")?.as_str().map(PathBuf::from));
    match saved {
        Some(path) if path.is_absolute() && path.is_dir() => SavedWorkspace::Available(path),
        Some(path) if path.is_absolute() => SavedWorkspace::Missing,
        _ => SavedWorkspace::None,
    }
}

pub fn save_workspace(settings: &Path, workspace: &Path) -> std::io::Result<()> {
    if let Some(folder) = settings.parent() {
        std::fs::create_dir_all(folder)?;
    }
    let staged = settings.with_extension("json.tmp");
    std::fs::write(
        &staged,
        serde_json::json!({ "workspace": workspace }).to_string(),
    )?;
    std::fs::rename(staged, settings)
}

/// A new folder under `base` named after the imported source; never an existing one.
pub fn import_destination(base: &Path, source: &Path) -> PathBuf {
    let name: String = source
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
        .chars()
        .filter(|c| c.is_alphanumeric() || matches!(c, ' ' | '-' | '_' | '.'))
        .collect();
    let name = name.trim_matches(|c: char| c == '.' || c.is_whitespace());
    let name = if name.is_empty() { "workspace" } else { name };
    let mut candidate = base.join(name);
    let mut suffix = 2;
    while candidate.exists() {
        candidate = base.join(format!("{name} {suffix}"));
        suffix += 1;
    }
    candidate
}
const MAX_HANDSHAKE_BYTES: u64 = 4096;

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn decode_hex32(text: &str) -> Option<[u8; 32]> {
    if text.len() != 64 || !text.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
        return None;
    }
    let mut out = [0u8; 32];
    for (i, byte) in out.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&text[2 * i..2 * i + 2], 16).ok()?;
    }
    Some(out)
}

/// Per-launch secret shared only with the owned sidecar over its stdin pipe.
/// It is never sent over HTTP, placed in a URL or logged; no `Debug` on purpose.
pub struct LaunchCredential([u8; 32]);

impl LaunchCredential {
    pub fn generate() -> Option<Self> {
        let mut secret = [0u8; 32];
        SystemRandom::new().fill(&mut secret).ok()?;
        Some(Self(secret))
    }

    #[allow(dead_code)] // Headless probe and tests receive the credential as text.
    pub fn from_hex(text: &str) -> Option<Self> {
        decode_hex32(text).map(Self)
    }

    pub fn stdin_line(&self) -> String {
        hex(&self.0) + "\n"
    }

    fn key(&self) -> hmac::Key {
        hmac::Key::new(hmac::HMAC_SHA256, &self.0)
    }

    /// Constant-time check of the backend's answer to a fresh challenge. Workspace
    /// and port are bound so a proof cannot be relayed from another instance.
    pub fn verify_proof(&self, proof: &str, challenge: &str, workspace: &str, port: u16) -> bool {
        let message = format!("nexus-desktop-launch/v1\0{challenge}\0{workspace}\0{port}");
        decode_hex32(proof)
            .is_some_and(|tag| hmac::verify(&self.key(), message.as_bytes(), &tag).is_ok())
    }

    /// HttpOnly webview session value; distinct from the secret and from any proof.
    pub fn session(&self) -> String {
        hex(hmac::sign(&self.key(), b"nexus-desktop-session/v1").as_ref())
    }
}

/// Wait until the owned backend proves it holds this launch's credential.
/// Any other loopback service, including another Studio, cannot answer and
/// learns nothing reusable: each attempt sends only a fresh random challenge.
pub fn wait_for_backend(
    port: u16,
    workspace: &str,
    credential: &LaunchCredential,
    timeout: Duration,
    running: impl Fn() -> bool,
) -> Result<(), StartupError> {
    let client = reqwest::blocking::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(1))
        .build()
        .map_err(|_| StartupError::Transport)?;
    let random = SystemRandom::new();
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if !running() {
            return Err(StartupError::Exited);
        }
        let mut nonce = [0u8; 32];
        random
            .fill(&mut nonce)
            .map_err(|_| StartupError::Transport)?;
        let challenge = hex(&nonce);
        let remaining = deadline.saturating_duration_since(Instant::now());
        if let Ok(response) = client
            .get(format!("http://127.0.0.1:{port}/api/desktop/handshake"))
            .header("X-Nexus-Desktop-Challenge", &challenge)
            .timeout(remaining.min(Duration::from_secs(1)))
            .send()
        {
            if response.status() == reqwest::StatusCode::OK {
                use std::io::Read;
                let mut bytes = Vec::new();
                if response
                    .take(MAX_HANDSHAKE_BYTES + 1)
                    .read_to_end(&mut bytes)
                    .is_ok()
                    && bytes.len() as u64 <= MAX_HANDSHAKE_BYTES
                {
                    if let Ok(body) = serde_json::from_slice::<serde_json::Value>(&bytes) {
                        let proof = body.get("proof").and_then(|v| v.as_str()).unwrap_or("");
                        if credential.verify_proof(proof, &challenge, workspace, port) {
                            return if running() {
                                Ok(())
                            } else {
                                Err(StartupError::Exited)
                            };
                        }
                    }
                }
            }
        }
        std::thread::sleep(
            deadline
                .saturating_duration_since(Instant::now())
                .min(Duration::from_millis(100)),
        );
    }
    Err(StartupError::Timeout)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };
    struct Child(Arc<AtomicUsize>);
    impl ManagedChild for Child {
        fn stop(self) -> Result<(), String> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }
    }
    fn quick_owner<C: ManagedChild>() -> BackendOwner<C> {
        BackendOwner::with_grace(Duration::from_millis(20))
    }
    /// Counts graceful exit requests and forced stops.
    struct Graceful(Arc<AtomicUsize>, Arc<AtomicUsize>);
    impl ManagedChild for Graceful {
        fn request_exit(&mut self) {
            self.0.fetch_add(1, Ordering::SeqCst);
        }
        fn stop(self) -> Result<(), String> {
            self.1.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }
    }
    #[test]
    fn stop_requests_graceful_exit_and_waits_before_group_cleanup() {
        let (requests, stops) = (Arc::new(AtomicUsize::new(0)), Arc::new(AtomicUsize::new(0)));
        let owner = Arc::new(BackendOwner::with_grace(Duration::from_secs(5)));
        owner
            .install(Graceful(requests.clone(), stops.clone()))
            .unwrap();
        let (watch, seen, killed) = (owner.clone(), requests.clone(), stops.clone());
        let child = std::thread::spawn(move || {
            while seen.load(Ordering::SeqCst) == 0 {
                std::thread::sleep(Duration::from_millis(5));
            }
            std::thread::sleep(Duration::from_millis(50));
            assert_eq!(
                killed.load(Ordering::SeqCst),
                0,
                "killed before it could close"
            );
            watch.terminated(Some(0));
        });
        let started = Instant::now();
        owner.stop().unwrap();
        child.join().unwrap();
        assert!(started.elapsed() < Duration::from_secs(2));
        assert_eq!(requests.load(Ordering::SeqCst), 1);
        assert_eq!(stops.load(Ordering::SeqCst), 1);
    }
    #[test]
    fn hung_child_is_force_stopped_after_grace() {
        let (requests, stops) = (Arc::new(AtomicUsize::new(0)), Arc::new(AtomicUsize::new(0)));
        let owner = BackendOwner::with_grace(Duration::from_millis(50));
        owner
            .install(Graceful(requests.clone(), stops.clone()))
            .unwrap();
        let started = Instant::now();
        owner.stop().unwrap();
        assert!(started.elapsed() >= Duration::from_millis(50));
        assert_eq!(
            (
                requests.load(Ordering::SeqCst),
                stops.load(Ordering::SeqCst)
            ),
            (1, 1)
        );
    }
    #[test]
    fn switching_reopens_but_quitting_never_does() {
        let count = Arc::new(AtomicUsize::new(0));
        let owner = quick_owner();
        owner.install(Child(count.clone())).unwrap();
        owner.stop().unwrap();
        assert!(owner.reopen());
        owner.install(Child(count.clone())).unwrap();
        assert!(owner.running());
        owner.quit().unwrap();
        assert!(!owner.reopen());
        assert!(owner.install(Child(count.clone())).is_err());
        assert_eq!(count.load(Ordering::SeqCst), 3);
    }
    #[test]
    fn saved_workspace_is_remembered_and_never_silently_replaced() {
        let root = std::env::temp_dir().join(format!("nexus-settings-{}", std::process::id()));
        let settings = root.join("config").join("desktop.json");
        assert_eq!(saved_workspace(&settings), SavedWorkspace::None);
        let folder = root.join("Projects").join("Studio");
        std::fs::create_dir_all(&folder).unwrap();
        save_workspace(&settings, &folder).unwrap();
        assert_eq!(
            saved_workspace(&settings),
            SavedWorkspace::Available(folder.clone())
        );
        std::fs::remove_dir_all(&folder).unwrap();
        assert_eq!(saved_workspace(&settings), SavedWorkspace::Missing);
        std::fs::write(&settings, "{not json").unwrap();
        assert_eq!(saved_workspace(&settings), SavedWorkspace::None);
        std::fs::write(&settings, r#"{"workspace":"relative/path"}"#).unwrap();
        assert_eq!(saved_workspace(&settings), SavedWorkspace::None);
        std::fs::remove_dir_all(&root).unwrap();
    }
    #[test]
    fn import_destination_is_new_and_named_after_source() {
        let base = std::env::temp_dir().join(format!("nexus-imports-{}", std::process::id()));
        std::fs::create_dir_all(base.join("Old Studio")).unwrap();
        let next = import_destination(&base, Path::new("/somewhere/Old Studio"));
        assert_eq!(next, base.join("Old Studio 2"));
        assert_eq!(
            import_destination(&base, Path::new("/x/../..")),
            base.join("workspace")
        );
        assert_eq!(
            import_destination(&base, Path::new("/x/a\\b:c")),
            base.join("abc")
        );
        std::fs::remove_dir_all(&base).unwrap();
    }
    #[test]
    fn first_exit_code_is_kept_for_the_startup_screen() {
        let owner: BackendOwner<Child> = quick_owner();
        owner.terminated(Some(WORKSPACE_IN_USE_EXIT));
        owner.terminated(None);
        assert_eq!(owner.exit_code(), Some(WORKSPACE_IN_USE_EXIT));
    }
    #[test]
    fn close_kills_owned_child_once() {
        let count = Arc::new(AtomicUsize::new(0));
        let owner = quick_owner();
        owner.install(Child(count.clone())).unwrap();
        assert!(owner.running());
        owner.stop().unwrap();
        owner.stop().unwrap();
        assert!(!owner.running());
        drop(owner);
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }
    #[test]
    fn closing_before_spawn_kills_late_child() {
        let count = Arc::new(AtomicUsize::new(0));
        let owner = quick_owner();
        owner.stop().unwrap();
        assert!(owner.install(Child(count.clone())).is_err());
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }
    #[test]
    fn duplicate_spawn_does_not_replace_owned_process() {
        let count = Arc::new(AtomicUsize::new(0));
        let owner = quick_owner();
        owner.install(Child(count.clone())).unwrap();
        assert!(owner.install(Child(count.clone())).is_err());
        assert!(owner.running());
        owner.stop().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 2);
    }
    #[test]
    fn terminated_leader_keeps_handle_for_group_cleanup() {
        let count = Arc::new(AtomicUsize::new(0));
        let owner = quick_owner();
        owner.install(Child(count.clone())).unwrap();
        owner.terminated(Some(0));
        assert!(!owner.running());
        drop(owner);
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }
    // Shared with tests/test_studio_desktop.py so both HMAC implementations agree.
    const SECRET: &str = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f";
    const PROOF: &str = "4b7a52c3560283554eb58b5d08d9b702439989429f5764056d46f5b9ea4a51ce";
    const SESSION: &str = "1b21ee8c700106024d6068c1c6d0152b72063952162b9f2aa880a8114189c0f5";
    #[test]
    fn proof_matches_backend_and_binds_challenge_workspace_and_port() {
        let credential = LaunchCredential::from_hex(SECRET).unwrap();
        let challenge = "ab".repeat(32);
        assert!(credential.verify_proof(PROOF, &challenge, "/private/workspace", 43123));
        assert!(!credential.verify_proof(PROOF, &"cd".repeat(32), "/private/workspace", 43123));
        assert!(!credential.verify_proof(PROOF, &challenge, "/other", 43123));
        assert!(!credential.verify_proof(PROOF, &challenge, "/private/workspace", 43124));
        assert!(!credential.verify_proof(
            &PROOF.to_uppercase(),
            &challenge,
            "/private/workspace",
            43123
        ));
        assert!(!credential.verify_proof("", &challenge, "/private/workspace", 43123));
        let impostor = LaunchCredential::generate().unwrap();
        assert!(!impostor.verify_proof(PROOF, &challenge, "/private/workspace", 43123));
    }
    #[test]
    fn session_is_derived_and_never_the_secret() {
        let credential = LaunchCredential::from_hex(SECRET).unwrap();
        assert_eq!(credential.session(), SESSION);
        assert_eq!(credential.stdin_line(), format!("{SECRET}\n"));
        assert!(LaunchCredential::from_hex(&SECRET.to_uppercase()).is_none());
        assert!(LaunchCredential::from_hex(&SECRET[2..]).is_none());
    }
    #[test]
    fn exited_child_never_waits_for_or_navigates_to_server() {
        assert_eq!(
            wait_for_backend(
                1,
                "/unused",
                &LaunchCredential::generate().unwrap(),
                Duration::from_secs(1),
                || false
            ),
            Err(StartupError::Exited)
        );
    }
}
