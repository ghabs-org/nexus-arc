"""Explicit mutual-TLS authentication, stream-loss and cleanup acceptance.

Uses the same real-daemon harness as SSH, with generated disposable certificates.
No implicit pulls, service mocks, host ports, personal keys or skipped checks.
"""

from tests.studio_ssh_proxy_acceptance import exercise_transport


def test_real_mutual_tls_and_disconnect(tmp_path):
    exercise_transport(tmp_path, transport="tls")
