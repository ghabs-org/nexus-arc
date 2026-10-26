"""Verify real Docker mutual TLS through an isolated filtered-socket gateway.

Only the disposable initializer sees both client/server volumes. The CA private
keys exist only in its temporary filesystem, never in either runtime mount.
The gateway forwards bytes, not simulated Docker responses.
"""

import argparse
import asyncio
import json
import os
import shutil
import ssl
import subprocess
from pathlib import Path

from studio_proxy_ssh import exercise

CLIENT, SERVER = Path("/tls-client"), Path("/tls-server")
SOCKET = "/var/lib/nexus-studio/docker-proxy/docker.sock"
CONTEXT = "nexus-tls-check"


def initialize():
    os.umask(0o077)

    def openssl(*args):
        subprocess.run(["openssl", *args], check=True, capture_output=True, timeout=15)

    for directory in (CLIENT, SERVER):
        directory.chmod(0o700)
    for authority in ("trusted", "untrusted"):
        openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=nexus-test-" + authority,
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout",
            "/tmp/" + authority + ".key",
            "-out",
            "/tmp/" + authority + ".pem",
        )
    for name, authority, directory, usage in (
        ("server", "trusted", SERVER, "serverAuth"),
        ("client", "trusted", CLIENT, "clientAuth"),
        ("rogue", "untrusted", CLIENT, "clientAuth"),
    ):
        openssl(
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=" + name,
            "-keyout",
            str(directory / (name + ".key")),
            "-out",
            "/tmp/" + name + ".csr",
        )
        extensions = Path("/tmp/" + name + ".ext")
        extensions.write_text(
            "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage="
            + usage
            + "\n"
            + ("subjectAltName=DNS:gateway\n" if name == "server" else "")
        )
        openssl(
            "x509",
            "-req",
            "-days",
            "1",
            "-sha256",
            "-in",
            "/tmp/" + name + ".csr",
            "-CA",
            "/tmp/" + authority + ".pem",
            "-CAkey",
            "/tmp/" + authority + ".key",
            "-CAcreateserial",
            "-extfile",
            str(extensions),
            "-out",
            str(directory / (name + ".pem")),
        )
    for directory in (CLIENT, SERVER):
        shutil.copyfile("/tmp/trusted.pem", directory / "ca.pem")
    shutil.copyfile("/tmp/untrusted.pem", CLIENT / "wrong-ca.pem")
    for directory in (CLIENT, SERVER):
        for path in directory.iterdir():
            path.chmod(0o600)


async def gateway():
    assert not Path("/var/run/docker.sock").exists()
    assert not Path("/var/lib/nexus-proxy-private/ownership.sqlite").exists()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(SERVER / "server.pem", SERVER / "server.key")
    context.load_verify_locations(SERVER / "ca.pem")
    context.verify_mode = ssl.CERT_REQUIRED
    loop = asyncio.get_running_loop()
    # asyncio reports failed incoming TLS handshakes through the loop's error
    # handler in debug mode. Retain the actual TLS reason, not only a client reset.
    loop.set_debug(True)

    def handshake_error(loop, details):
        error = details.get("exception")
        if isinstance(error, ssl.SSLError):
            print("NEXUS_TLS_REJECT " + json.dumps({"reason": str(error)}), flush=True)
        else:
            loop.default_exception_handler(details)

    loop.set_exception_handler(handshake_error)

    async def pipe(reader, writer):
        try:
            while chunk := await reader.read(65536):
                writer.write(chunk)
                await writer.drain()
        finally:
            if writer.can_write_eof():
                writer.write_eof()
            else:
                # TLS cannot half-close. Upstream completion must also end the
                # client's read, without waiting for its still-open input pipe.
                writer.close()

    async def handle(reader, writer):
        upstream = None
        try:
            up_reader, upstream = await asyncio.open_unix_connection(SOCKET)
            await asyncio.gather(pipe(reader, upstream), pipe(up_reader, writer))
        except (ConnectionError, OSError):
            pass
        finally:
            if upstream:
                upstream.close()
            writer.close()

    async with await asyncio.start_server(
        handle,
        "0.0.0.0",
        2376,
        ssl=context,
        ssl_handshake_timeout=3,
    ) as server:
        await server.serve_forever()


def certificate_checks():
    rejected = []
    for case in ("missing-client", "untrusted-client", "untrusted-server", "wrong-hostname"):
        command = [
            "docker",
            "--config",
            "/tmp/no-client-config",
            "--tlsverify",
            "--tlscacert",
            str(CLIENT / ("wrong-ca.pem" if case == "untrusted-server" else "ca.pem")),
            "--host",
            "tcp://" + ("wrong-name" if case == "wrong-hostname" else "gateway") + ":2376",
        ]
        if case != "missing-client":
            name = "rogue" if case == "untrusted-client" else "client"
            command += [
                "--tlscert",
                str(CLIENT / (name + ".pem")),
                "--tlskey",
                str(CLIENT / (name + ".key")),
            ]
        checked = subprocess.run([*command, "version"], capture_output=True, timeout=10)
        assert checked.returncode != 0, case + " was accepted"
        error = checked.stderr.decode().lower()
        if case in {"missing-client", "untrusted-client"}:
            # TLS 1.3 can surface the server's alert as EOF or reset in Docker.
            # The outer harness requires the precise server-side SSL reasons.
            assert error, case + " returned no diagnostic"
            rejected.append(case)
            continue
        expected = {
            "untrusted-server": ("unknown authority",),
            "wrong-hostname": ("not wrong-name",),
        }
        assert any(text in error for text in expected[case]), (case, error)
        rejected.append(case)
    return rejected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["initialize", "gateway", "exercise"], required=True)
    parser.add_argument("--image")
    args = parser.parse_args()
    if args.phase == "initialize":
        initialize()
    elif args.phase == "gateway":
        asyncio.run(gateway())
    else:
        rejected = []
        endpoint = "tcp://gateway:2376,ca=/tls-client/ca.pem,cert=/tls-client/client.pem,key=/tls-client/client.key"
        result = exercise(
            args.image,
            context=CONTEXT,
            endpoint=endpoint,
            preflight=lambda: rejected.extend(certificate_checks()),
        )
        result.update(mutual_tls=True, rejected_certificates=rejected)
        print("NEXUS_TLS_ACCEPTANCE " + json.dumps(result), flush=True)
