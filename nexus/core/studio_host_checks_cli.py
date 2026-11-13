"""Read-only operator entry point; never starts or repairs a deployment."""

import json


def add_parser(subparsers):
    parser = subparsers.add_parser(
        "studio-host-check", help="Check Studio/proxy mounts on the Docker daemon host"
    )
    parser.add_argument("--studio-container", required=True)
    parser.add_argument("--proxy-container", required=True)
    parser.add_argument("--docker-context", required=True)
    parser.add_argument("--private-path", default="/var/lib/nexus-proxy-private")
    parser.add_argument("--upstream-socket", default="/var/run/docker.sock")


def host_check_command(args):
    from nexus.core.studio_host_checks import verify_proxy_layout

    try:
        report = verify_proxy_layout(
            args.studio_container,
            args.proxy_container,
            docker_context=args.docker_context,
            private_path=args.private_path,
            upstream_socket=args.upstream_socket,
        )
    except ValueError:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error": "Choose explicit container/context names and canonical absolute proxy paths.",
                }
            )
        )
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1
