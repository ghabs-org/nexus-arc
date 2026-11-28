"""Disposable TLS identities; CA keys remain only in initializer tmpfs.

Used by the explicit host acceptance gate, never imported by product code.
"""

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

CLIENT, SERVER = Path("/tls-client"), Path("/tls-server")


def initialize():
    os.umask(0o077)

    def openssl(*args):
        subprocess.run(["openssl", *args], check=True, capture_output=True, timeout=15)

    for directory in (CLIENT, SERVER):
        directory.chmod(0o700)
    for authority in ("old", "new"):
        openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=nexus-lifecycle-" + authority,
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout",
            "/tmp/" + authority + ".key",
            "-out",
            "/tmp/" + authority + ".pem",
        )
        Path("/tmp/" + authority + ".index").touch()
        Path("/tmp/" + authority + ".serial").write_text("01\n")
        Path("/tmp/" + authority + ".conf").write_text(
            "[ca]\ndefault_ca=issuer\n[issuer]\n"
            f"database=/tmp/{authority}.index\nserial=/tmp/{authority}.serial\n"
            f"certificate=/tmp/{authority}.pem\nprivate_key=/tmp/{authority}.key\n"
            "new_certs_dir=/tmp\ndefault_md=sha256\npolicy=policy\n"
            "[policy]\ncommonName=supplied\n"
        )
        for directory in (CLIENT, SERVER):
            shutil.copyfile("/tmp/" + authority + ".pem", directory / (authority + "-ca.pem"))
    now = datetime.now(UTC)
    for name, authority, directory, expired in (
        ("old-server", "old", SERVER, False),
        ("renewed-server", "old", SERVER, False),
        ("new-server", "new", SERVER, False),
        ("expired-server", "old", SERVER, True),
        ("old-client", "old", CLIENT, False),
        ("new-client", "new", CLIENT, False),
        ("expired-client", "old", CLIENT, True),
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
        is_server = directory == SERVER
        extensions = Path("/tmp/" + name + ".ext")
        extensions.write_text(
            "[leaf]\nbasicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage="
            + ("serverAuth" if is_server else "clientAuth")
            + "\n"
            + ("subjectAltName=DNS:gateway\n" if is_server else "")
        )
        start = now - timedelta(days=2) if expired else now - timedelta(hours=1)
        end = now - timedelta(days=1) if expired else now + timedelta(days=1)
        openssl(
            "ca",
            "-batch",
            "-notext",
            "-config",
            "/tmp/" + authority + ".conf",
            "-startdate",
            start.strftime("%Y%m%d%H%M%SZ"),
            "-enddate",
            end.strftime("%Y%m%d%H%M%SZ"),
            "-extfile",
            str(extensions),
            "-extensions",
            "leaf",
            "-in",
            "/tmp/" + name + ".csr",
            "-out",
            str(directory / (name + ".pem")),
        )
    for directory in (CLIENT, SERVER):
        (directory / "both-ca.pem").write_bytes(
            (directory / "old-ca.pem").read_bytes() + (directory / "new-ca.pem").read_bytes()
        )
        for path in directory.iterdir():
            path.chmod(0o600)
    select_server("old-server", "old")


def select_server(identity, authority):
    assert identity in {"old-server", "renewed-server", "new-server", "expired-server"}
    assert authority in {"old", "new", "both"}
    for extension in ("pem", "key"):
        destination = SERVER / ("server." + extension)
        shutil.copyfile(SERVER / (identity + "." + extension), destination)
        destination.chmod(0o600)
    shutil.copyfile(SERVER / (authority + "-ca.pem"), SERVER / "ca.pem")
    (SERVER / "ca.pem").chmod(0o600)
