import argparse

from nexus.translators.to_copilot import translate_agent_to_copilot
from nexus.translators.to_markdown import translate_agent_to_markdown
from nexus.translators.to_n8n import translate_workflow_to_n8n
from nexus.translators.to_python import translate_agent_to_python


def main():
    parser = argparse.ArgumentParser(description="Nexus ARC CLI")
    subparsers = parser.add_subparsers(dest="command")
    chat_parser = subparsers.add_parser(
        "chat", help="Chat through an authenticated Studio workspace"
    )
    chat_parser.add_argument("message", nargs="*", help="One message, or omit for interactive chat")
    chat_parser.add_argument("--url", default="http://127.0.0.1:5003", help="Running Studio origin")
    chat_parser.add_argument(
        "--session-env", help="Environment reference for an existing Nexus session"
    )
    chat_parser.add_argument("--session-id", help="Continue an existing conversation")
    chat_parser.add_argument("--run-id", help="Inspect an existing run without submitting anything")
    chat_parser.add_argument("--agent", help="Agent ID")
    chat_parser.add_argument("--provider", help="Explicit provider override")
    chat_parser.add_argument(
        "--timeout", type=int, default=600, help="Maximum polling duration in seconds"
    )
    config_parser = subparsers.add_parser(
        "config", help="Validate, review and apply Studio configuration"
    )
    config_parser.add_argument(
        "operation", choices=["validate", "plan", "apply", "export", "convert-interop", "export-interop"]
    )
    config_parser.add_argument(
        "file", nargs="?", help="Configuration document or reviewed plan JSON"
    )
    config_parser.add_argument(
        "--url", default="http://127.0.0.1:5003", help="Running Studio origin"
    )
    config_parser.add_argument(
        "--session-env", help="Environment reference for an existing Nexus session"
    )
    config_parser.add_argument("--output", "-o", help="Write result to a private JSON file")

    config_parser.add_argument(
        "--bindings", help="Explicit external model/agent/tool mappings as JSON (convert-interop only)"
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

    args = parser.parse_args()
    if args.command == "chat":
        from nexus.core.studio_chat_cli import chat_command

        chat_command(args)
        return
    if args.command == "config":
        from nexus.core.studio_config_cli import configuration_command

        if args.operation not in {"export", "export-interop"} and not args.file:
            parser.error("config validate/plan/apply/convert-interop requires a file")
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

        serve_studio(
            args.dir,
            port=args.port,
            host=args.host,
            team_config=args.team_config,
            state_dir=args.state_dir,
            runtime_config=args.runtime_config,
            worker_only=args.worker_only,
        )
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
