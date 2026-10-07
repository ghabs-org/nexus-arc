import argparse

from nexus.translators.to_copilot import translate_agent_to_copilot
from nexus.translators.to_markdown import translate_agent_to_markdown
from nexus.translators.to_n8n import translate_workflow_to_n8n
from nexus.translators.to_python import translate_agent_to_python

def main():
    parser = argparse.ArgumentParser(description="Nexus ARC CLI")
    subparsers = parser.add_subparsers(dest="command")

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
        help="Start browser UI for authoring agents/workflows instead",
    )
    init_parser.add_argument(
        "--port",
        type=int,
        default=5002,
        help="Port for --gui (localhost only)",
    )
    init_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for --gui (use 0.0.0.0 only on trusted networks)",
    )
    init_parser.add_argument(
        "--config",
        default=None,
        help="project_config.yaml path enabling Apply-to-bot-config (optional)",
    )

    args = parser.parse_args()

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
    elif args.command == "init":
        if args.gui:
            from nexus.init_gui import serve_gui

            serve_gui(args.dir, port=args.port, host=args.host, project_config_path=args.config)
        else:
            from nexus.init_project import run_init

            run_init(args.dir, use_defaults=args.yes)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
