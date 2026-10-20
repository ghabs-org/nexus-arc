"""Framework auth domain and manager interfaces."""

__all__ = [
    "AuthManager",
    "auth_manager",
    "check_project_access",
    "build_setup_completed_chat_message",
    "complete_github_oauth",
    "complete_gitlab_oauth",
    "complete_linkedin_oauth",
    "complete_x_oauth",
    "complete_meta_oauth",
    "create_login_session_for_user",
    "format_login_session_ref",
    "get_provider_account_login_status",
    "get_latest_login_session_status",
    "get_linkedin_auth_status",
    "get_linkedin_profile_me",
    "import_openclaw_local_provider_credentials",
    "get_session_and_setup_status",
    "get_setup_status",
    "has_project_access",
    "relay_provider_account_login_callback",
    "submit_provider_account_login_code",
    "register_onboarding_message",
    "refresh_stale_access_grants",
    "resolve_login_session_id",
    "start_provider_account_login",
    "start_provider_account_login_for_nexus",
    "start_oauth_flow",
    "store_ai_provider_keys",
]


def __getattr__(name):
    # Credential crypto must remain usable by local Studio without initializing
    # the bot's account manager or requiring its project configuration.
    if name in __all__:
        from importlib import import_module

        value = getattr(import_module("nexus.core.auth.manager"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
