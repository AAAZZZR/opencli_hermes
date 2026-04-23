"""REST tests for /api/v1/nodes endpoints."""

from __future__ import annotations

from httpx import AsyncClient


async def test_create_node_returns_token(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/nodes", json={"label": "alice-mbp"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["label"] == "alice-mbp"
    assert body["status"] == "offline"
    assert len(body["token"]) >= 32  # base64url of 32 bytes ≈ 43 chars
    assert body["id"]


async def test_create_duplicate_label_conflicts(client: AsyncClient) -> None:
    await client.post("/api/v1/nodes", json={"label": "dup"})
    resp = await client.post("/api/v1/nodes", json={"label": "dup"})
    assert resp.status_code == 409


async def test_list_nodes_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/nodes")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_nodes_returns_nodes_without_token(client: AsyncClient) -> None:
    await client.post("/api/v1/nodes", json={"label": "n1"})
    await client.post("/api/v1/nodes", json={"label": "n2"})
    resp = await client.get("/api/v1/nodes")
    assert resp.status_code == 200
    nodes = resp.json()
    assert len(nodes) == 2
    assert {n["label"] for n in nodes} == {"n1", "n2"}
    assert all("token" not in n for n in nodes)


async def test_get_node_by_label_and_id(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/nodes", json={"label": "alice"})).json()

    by_label = await client.get("/api/v1/nodes/alice")
    by_id = await client.get(f"/api/v1/nodes/{created['id']}")

    assert by_label.status_code == 200
    assert by_id.status_code == 200
    assert by_label.json()["id"] == created["id"]
    assert by_id.json()["label"] == "alice"


async def test_get_unknown_node_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/nodes/ghost")
    assert resp.status_code == 404


async def test_delete_node(client: AsyncClient) -> None:
    await client.post("/api/v1/nodes", json={"label": "dead"})
    resp = await client.delete("/api/v1/nodes/dead")
    assert resp.status_code == 204

    follow = await client.get("/api/v1/nodes/dead")
    assert follow.status_code == 404


async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
