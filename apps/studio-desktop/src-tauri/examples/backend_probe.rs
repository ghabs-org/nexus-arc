//! Headless acceptance consumer of the same readiness code used by the native shell.
//! Reads the launch credential from stdin, as the sidecar does; never from argv.
#[path = "../src/backend.rs"]
#[allow(dead_code)]
mod backend;
fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        std::process::exit(2);
    }
    let port: u16 = match args[1].parse() {
        Ok(port) => port,
        Err(_) => std::process::exit(2),
    };
    let mut line = String::new();
    let credential = match std::io::stdin().read_line(&mut line) {
        Ok(_) => backend::LaunchCredential::from_hex(line.trim_end_matches('\n')),
        Err(_) => None,
    };
    let Some(credential) = credential else {
        std::process::exit(2);
    };
    let result = backend::wait_for_backend(
        port,
        &args[2],
        &credential,
        std::time::Duration::from_millis(1200),
        || true,
    );
    match result {
        Ok(()) => println!("{{\"ready\":true}}"),
        Err(_) => {
            println!("{{\"ready\":false}}");
            std::process::exit(1);
        }
    }
}
