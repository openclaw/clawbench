from report_client import (
    API_PATH,
    REQUIRED_HEADERS,
    RATE_LIMIT_PER_MINUTE,
    MAX_PAYLOAD_BYTES,
)


def test_reporting_api_path_is_v2():
    # v1 is deprecated (sunset 2026-07-01), v3 is beta — current is v2.
    assert API_PATH == "/v2/reports"


def test_workspace_header_is_required():
    assert "X-Workspace-Id" in REQUIRED_HEADERS


def test_authorization_header_is_required():
    # Bearer token is required per the docs.
    assert "Authorization" in REQUIRED_HEADERS


def test_admin_token_is_not_a_required_header():
    # X-Admin-Token is only for /v2/admin — sending it on /v2/reports returns 400.
    # Distractor — the agent must correctly scope required headers.
    assert "X-Admin-Token" not in REQUIRED_HEADERS


def test_rate_limit_matches_docs():
    # 120 requests per minute per workspace.
    assert RATE_LIMIT_PER_MINUTE == 120


def test_max_payload_size_matches_docs():
    # 10 MiB = 10 * 1024 * 1024 bytes.
    assert MAX_PAYLOAD_BYTES == 10 * 1024 * 1024
