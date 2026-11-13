//! Fixed provider actions from the currently verified Studio backend only.

use tauri::Url;

pub const PREFIX: &str = "/__nexus_desktop__/provider-login/";

pub fn provider_action<'a>(trusted: &Url, current: &Url, target: &'a Url) -> Option<&'a str> {
    if trusted.scheme() != "http"
        || trusted.host_str() != Some("127.0.0.1")
        || trusted.port().is_none()
        || current.origin() != trusted.origin()
        || target.origin() != trusted.origin()
        || !target.username().is_empty()
        || target.password().is_some()
        || target.query().is_some()
        || target.fragment().is_some()
    {
        return None;
    }
    match target.path().strip_prefix(PREFIX)? {
        provider @ ("codex" | "claude" | "agy") => Some(provider),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_fixed_actions_on_the_owned_backend() {
        let trusted = Url::parse("http://127.0.0.1:49742/?desktop=1").unwrap();
        for provider in ["codex", "claude", "agy"] {
            let target = trusted.join(&format!("{PREFIX}{provider}")).unwrap();
            assert_eq!(provider_action(&trusted, &trusted, &target), Some(provider));
        }
        for target in [
            "http://127.0.0.1:49743/__nexus_desktop__/provider-login/claude",
            "https://example.com/__nexus_desktop__/provider-login/claude",
            "http://user@127.0.0.1:49742/__nexus_desktop__/provider-login/claude",
            "http://127.0.0.1:49742/__nexus_desktop__/provider-login/claude?command=sh",
            "http://127.0.0.1:49742/__nexus_desktop__/provider-login/claude#extra",
            "http://127.0.0.1:49742/__nexus_desktop__/provider-login/sh",
            "http://127.0.0.1:49742/__nexus_desktop__/provider-login/claude/extra",
        ] {
            assert_eq!(
                provider_action(&trusted, &trusted, &Url::parse(target).unwrap()),
                None
            );
        }
        let external = Url::parse("https://example.com/").unwrap();
        let target = trusted.join(&format!("{PREFIX}claude")).unwrap();
        assert_eq!(provider_action(&trusted, &external, &target), None);
    }
}
