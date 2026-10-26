import argparse

from nexus.translators.to_copilot import translate_agent_to_copilot
from nexus.translators.to_markdown import translate_agent_to_markdown
from nexus.translators.to_n8n import translate_workflow_to_n8n
from nexus.translators.to_python import translate_agent_to_python


def build_parser():
    parser = argparse.ArgumentParser(prog="nexus", description="Nexus ARC CLI")
    subparsers = parser.add_subparsers(dest="command")
    from nexus.core.studio_authoring_skills import add_parser as add_authoring_parser
    from nexus.core.studio_toolset_cli import add_parser as add_toolset_parser
    from nexus.core.studio_desktop_cli import add_parser as add_desktop_parser
    from nexus.core.studio_host_checks_cli import add_parser as add_host_check_parser

    add_authoring_parser(subparsers)
    add_toolset_parser(subparsers)
    add_desktop_parser(subparsers)
    add_host_check_parser(subparsers)
    chat_parser = subparsers.add_parser(
        "chat", help="Chat through an authenticated Studio workspace"
    )
    chat_parser.add_argument("message", nargs="*", help="One message, or omit for interactive chat")
    chat_parser.add_argument("--url", default="http://127.0.0.1:5003", help="Running Studio origin")
    chat_parser.add_argument(
        "--session-env", help="Environment reference for an existing Nexus session"
    )
    chat_parser.add_argument("--session-id", help="Continue an existing conversation")
    chat_parser.add_argument(
        "--new-session",
        action="store_true",
        help="Start interactive chat without restoring the saved selection",
    )
    chat_parser.add_argument("--run-id", help="Inspect an existing run without submitting anything")
    chat_parser.add_argument("--agent", help="Agent ID")
    chat_parser.add_argument("--provider", help="Explicit provider override")
    chat_parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable Markdown rendering and enhanced terminal input",
    )
    chat_parser.add_argument(
        "--artifact-id",
        action="append",
        default=[],
        help="Attach a saved Studio file to the next message (repeat, up to five)",
    )
    chat_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print one structured result; requires a message or --run-id",
    )
    chat_parser.add_argument(
        "--timeout", type=int, default=600, help="Maximum polling duration in seconds"
    )
    config_parser = subparsers.add_parser(
        "config", help="Validate, review and apply Studio configuration"
    )
    config_parser.add_argument(
        "operation",
        choices=[
            "validate",
            "plan",
            "apply",
            "export",
            "convert-interop",
            "export-interop",
            "convert-manifest",
            "export-manifest",
        ],
    )
    config_parser.add_argument(
        "file", nargs="?", help="Configuration document, reviewed plan, or manifest YAML"
    )
    config_parser.add_argument(
        "--url", default="http://127.0.0.1:5003", help="Running Studio origin"
    )
    config_parser.add_argument(
        "--session-env", help="Environment reference for an existing Nexus session"
    )
    config_parser.add_argument("--output", "-o", help="Write result to a private JSON file")

    config_parser.add_argument(
        "--bindings",
        help="Explicit external model/agent/tool mappings as JSON (convert-interop only)",
    )
    config_parser.add_argument(
        "--target",
        choices=["desktop", "personal-vm"],
        help="Target environment (convert-manifest and export-manifest)",
    )
    config_parser.add_argument(
        "--env-file",
        help="YAML or JSON file providing variables for interpolation (convert-manifest)",
    )

    # Translate
    translate_parser = subparsers.add_parser("translate", help="Translate definitions")
    translate_sub = translate_parser.add_subparsers(dest="subcommand")

    # Translate to Markdown
    md_parser = translate_sub.add_parser("to-markdown", help="Convert YAML to Markdown")
    md_parser.add_argument("file", help="YAML file to translate")

    # Translate to Copilot
    copilot_parser = translate_sub.add_parser(
        "to-copilot", help="Convert YAML to Copilot Instructions"
    )
    copilot_parser.add_argument("file", help="YAML file to translate")

    # Translate to Python
    python_parser = translate_sub.add_parser("to-python", help="Convert YAML to Python class")
    python_parser.add_argument("file", help="YAML file to translate")

    # Translate workflow to n8n
    n8n_parser = translate_sub.add_parser("to-n8n", help="Convert workflow YAML to n8n JSON")
    n8n_parser.add_argument("file", help="Workflow YAML file to translate")
    n8n_parser.add_argument(
        "--workflow-type",
        default="",
        help="Optional workflow type/tier to resolve before conversion",
    )
    n8n_parser.add_argument(
        "--bridge-url",
        default="http://nexus-bridge-1:8091",
        help="Base URL for the Nexus command bridge from n8n",
    )
    n8n_parser.add_argument(
        "-o",
        "--output",
        default="",
        help="Write JSON to this file instead of stdout",
    )

    bridge_parser = subparsers.add_parser("command-bridge", help="Run the Nexus command bridge")
    bridge_parser.add_argument("--host", default=None, help="Bridge host")
    bridge_parser.add_argument("--port", type=int, default=None, help="Bridge port")
    bridge_parser.add_argument(
        "--auth-token",
        default=None,
        help="Shared bearer token used by bridge clients",
    )

    studio_parser = subparsers.add_parser("studio", help="Run the local agent workspace")
    studio_parser.add_argument("--dir", default=".", help="Workspace with agents/ and workflows/")
    studio_parser.add_argument("--port", type=int, default=5003, help="Workspace port")
    studio_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Listen host; public binding requires team configuration",
    )
    studio_parser.add_argument(
        "--team-config", help="Administrator-owned Nexus team configuration outside the workspace"
    )
    studio_parser.add_argument(
        "--state-dir", help="Private state directory; required outside workspace in team mode"
    )
    studio_parser.add_argument(
        "--runtime-config", help="Private administrator runtime configuration"
    )
    studio_parser.add_argument(
        "--worker-only", action="store_true", help="Consume SQL jobs without an HTTP listener"
    )
    studio_parser.add_argument(
        "--desktop-launch-stdin",
        action="store_true",
        help="Read the native desktop shell's per-launch credential from standard input",
    )

    init_parser = subparsers.add_parser("init", help="Scaffold a new Nexus project")
    init_parser.add_argument(
        "--dir",
        default=".",
        help="Target directory for scaffold files (default: current dir)",
    )
    init_parser.add_argument(
        "--yes",
        action="store_true",
        help="Take all defaults without prompting",
    )
    init_parser.add_argument(
        "--gui",
        action="store_true",
        help="Open Nexus Studio on --dir instead (same as `nexus studio --dir`)",
    )
    init_parser.add_argument("--port", type=int, default=5003, help="Studio port for --gui")
    init_parser.add_argument("--host", default="127.0.0.1", help="Studio listen host for --gui")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "studio-host-check":
        from nexus.core.studio_host_checks_cli import host_check_command

        code = host_check_command(args)
        if code:
            parser.exit(code)
        return
    if args.command == "ai-assistance":
        from nexus.core.studio_authoring_skills import assistance_command

        try:
            assistance_command(args)
        except (ValueError, OSError) as exc:
            parser.exit(2, f"{exc}\n")
        return
    if args.command == "toolset":
        from nexus.core.studio_toolset_cli import toolset_command

        code = toolset_command(args)
        if code != 0:
            parser.exit(code)
        return
    if args.command == "desktop":
        from nexus.core.studio_desktop_cli import desktop_command

        code = desktop_command(args)
        if code != 0:
            parser.exit(code)
        return
    if args.command == "chat":
        from nexus.core.studio_chat_cli import chat_command

        chat_command(args)
        return
    if args.command == "config":
        from nexus.core.studio_config_cli import configuration_command

        if args.operation not in {"export", "export-interop", "export-manifest"} and not args.file:
            parser.error(
                "config validate/plan/apply/convert-interop/convert-manifest requires a file"
            )
        configuration_command(args)
        return

    if args.command == "translate":
        if args.subcommand == "to-markdown":
            print(translate_agent_to_markdown(args.file))
        elif args.subcommand == "to-copilot":
            print(translate_agent_to_copilot(args.file))
        elif args.subcommand == "to-python":
            print(translate_agent_to_python(args.file))
        elif args.subcommand == "to-n8n":
            rendered = translate_workflow_to_n8n(
                args.file,
                workflow_type=args.workflow_type,
                bridge_url=args.bridge_url,
            )
            if args.output:
                with open(args.output, "w", encoding="utf-8") as handle:
                    handle.write(rendered)
            else:
                print(rendered, end="")
    elif args.command == "command-bridge":
        from nexus.command_bridge_service import run_command_bridge

        run_command_bridge(
            host=args.host,
            port=args.port,
            auth_token=args.auth_token,
        )
    elif args.command == "studio":
        from nexus.studio import serve_studio

        launch, parent_input = None, None
        if args.desktop_launch_stdin:
            import sys

            from nexus.core.studio_desktop import DesktopLaunchCredential

            try:
                # Do not prefetch lifetime bytes into a BufferedReader that the
                # parent watcher cannot safely hold during interpreter shutdown.
                parent_input = getattr(sys.stdin.buffer, "raw", sys.stdin.buffer)
                launch = DesktopLaunchCredential.read(parent_input)
            except ValueError as exc:
                parser.exit(2, f"{exc}\n")
        from nexus.core.studio_desktop import UnsafeWorkspaceError, WorkspaceInUseError
        from nexus.core.studio_schema import NewerStateError

        try:
            serve_studio(
                args.dir,
                port=args.port,
                host=args.host,
                team_config=args.team_config,
                state_dir=args.state_dir,
                runtime_config=args.runtime_config,
                worker_only=args.worker_only,
                launch=launch,
            parent_input=parent_input,
            )
        except (WorkspaceInUseError, NewerStateError, UnsafeWorkspaceError) as exc:
            parser.exit(exc.exit_code, f"{exc}\n")
    elif args.command == "init":
        if args.gui:
            from nexus.studio import serve_studio

            serve_studio(args.dir, port=args.port, host=args.host)
        else:
            from nexus.init_project import run_init

            run_init(args.dir, use_defaults=args.yes)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
