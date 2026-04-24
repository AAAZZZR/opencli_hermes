"""REST tests for /api/v1/nodes/install/agent.sh + security hardening."""

from __future__ import annotations

import shlex

import pytest
from httpx import AsyncClient


async def _create(client: AsyncClient, label: str) -> dict:
    resp = await client.post("/api/v1/nodes", json={"label": label})
    resp.raise_for_status()
    return resp.json()


async def test_installer_for_known_node(client: AsyncClient) -> None:
    node = await _create(client, "alice-mbp")
    resp = await client.get("/api/v1/nodes/install/agent.sh", params={"label": "alice-mbp"})
    assert resp.status_code == 200
    body = resp.text
    # Token embedded (safe: simple value, shlex.quote leaves it bare)
    assert node["token"] in body
    # Bare-placeholder pattern: `NODE_TOKEN=<value>` with no surrounding quotes
    # from the template (shlex.quote adds its own if needed).
    assert f"NODE_TOKEN={node['token']}" in body
    assert "NODE_LABEL=alice-mbp" in body
    # Template should be fully rendered — no placeholders left behind.
    assert "__NODE_TOKEN__" not in body
    assert "__NODE_LABEL__" not in body
    assert "__CENTRAL_URL__" not in body


async def test_installer_unknown_label_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/nodes/install/agent.sh", params={"label": "nobody"})
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "bad_label",
    [
        'a"; rm -rf /; #',       # shell injection via `"` break-out
        "bob'evil",              # apostrophe
        "alice space",           # whitespace
        "name$(whoami)",         # command substitution attempt
        "../etc/passwd",         # path traversal
        "with\nnewline",         # newline — would split a config.env line
        "tab\there",             # tab
        "",                      # empty
        "x" * 65,                # over 64-char limit
    ],
)
async def test_create_rejects_unsafe_label(client: AsyncClient, bad_label: str) -> None:
    resp = await client.post("/api/v1/nodes", json={"label": bad_label})
    assert resp.status_code == 422, (
        f"expected 422 for unsafe label {bad_label!r}, got {resp.status_code}"
    )


@pytest.mark.parametrize(
    "bad_label",
    [
        'a"; rm -rf /; #',
        "alice space",
        "x" * 65,
    ],
)
async def test_installer_rejects_unsafe_label_query(
    client: AsyncClient, bad_label: str
) -> None:
    # Even if somehow a bad label got into the DB, the GET route validates
    # the same regex on the query parameter to stay defence-in-depth.
    resp = await client.get("/api/v1/nodes/install/agent.sh", params={"label": bad_label})
    assert resp.status_code == 422


async def test_render_shell_safety_via_shlex(client: AsyncClient) -> None:
    """Regression: characters that break naked `.replace` must survive quoting."""
    # Pick safe labels (schema rejects unsafe ones) but check that the
    # internal _render helper uses shlex.quote on each substitution.
    from fleet_hub.api.install import _render

    tpl = (
        "CENTRAL_URL=__CENTRAL_URL__\n"
        "NODE_TOKEN=__NODE_TOKEN__\n"
        "NODE_LABEL=__NODE_LABEL__\n"
        "OPENCLI_NPM_SPEC=__OPENCLI_NPM_SPEC__\n"
        "FLEET_AGENT_INSTALL_SPEC=__FLEET_AGENT_INSTALL_SPEC__\n"
    )
    # Use a fake token with a single-quote (we generate URL-safe-base64 in
    # practice so this shouldn't happen, but this locks the behavior).
    out = _render(tpl, central_url="https://a.b", token="tok'en", label="alice-mbp")
    # shlex.quote on "tok'en" yields "'tok'\\''en'" — round-trip via shlex.split
    # should preserve the original string.
    for line in out.splitlines():
        key, _, value = line.partition("=")
        parsed = shlex.split(value)
        assert len(parsed) == 1, f"{key} did not parse as a single bash token: {value!r}"
    # Specifically, token should survive intact
    for line in out.splitlines():
        if line.startswith("NODE_TOKEN="):
            parsed_value = shlex.split(line.split("=", 1)[1])[0]
            assert parsed_value == "tok'en"
            break
