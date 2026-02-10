# tests/integration/test_chat_endpoints.py
"""
Integration tests for Chat endpoints.

All tests use httpx.AsyncClient matching the async FastAPI app.
"""

import pytest
import uuid

from app.models.chat import ChatSession


class TestChatCreation:
    @pytest.mark.asyncio
    async def test_create_chat_success(self, client, auth_headers):
        response = await client.post(
            "/api/v1/chats",
            headers=auth_headers,
            json={"title": "My New Chat"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "My New Chat"
        assert data["message_count"] == 0
        assert data["is_deleted"] is False

    @pytest.mark.asyncio
    async def test_create_chat_default_title(self, client, auth_headers):
        response = await client.post(
            "/api/v1/chats", headers=auth_headers, json={}
        )
        assert response.status_code == 201
        assert response.json()["title"] == "New Chat"

    @pytest.mark.asyncio
    async def test_create_chat_no_auth(self, client):
        response = await client.post("/api/v1/chats", json={"title": "Test"})
        assert response.status_code in [401, 403]


class TestChatList:
    @pytest.mark.asyncio
    async def test_list_chats(self, client, auth_headers):
        response = await client.get("/api/v1/chats", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "chats" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data

    @pytest.mark.asyncio
    async def test_list_chats_with_data(self, client, test_chat, auth_headers):
        response = await client.get("/api/v1/chats", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_chats_pagination(self, client, auth_headers):
        for i in range(5):
            await client.post(
                "/api/v1/chats", headers=auth_headers, json={"title": f"Chat {i}"}
            )
        response = await client.get(
            "/api/v1/chats?skip=0&limit=2", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["chats"]) == 2
        assert data["skip"] == 0
        assert data["limit"] == 2

    @pytest.mark.asyncio
    async def test_list_excludes_deleted(self, client, deleted_chat, auth_headers):
        response = await client.get("/api/v1/chats", headers=auth_headers)
        ids = [c["id"] for c in response.json()["chats"]]
        assert deleted_chat.id not in ids


class TestGetChat:
    @pytest.mark.asyncio
    async def test_get_chat_success(self, client, test_chat, auth_headers):
        response = await client.get(
            f"/api/v1/chats/{test_chat.id}", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_chat.id
        assert data["title"] == test_chat.title
        assert "messages" in data

    @pytest.mark.asyncio
    async def test_get_chat_with_messages(self, client, test_chat_with_messages, auth_headers):
        response = await client.get(
            f"/api/v1/chats/{test_chat_with_messages.id}",
            headers=auth_headers,
        )
        data = response.json()
        assert len(data["messages"]) >= 2

    @pytest.mark.asyncio
    async def test_get_chat_messages_ordered(self, client, test_chat_with_messages, auth_headers):
        """Verify messages are returned in correct order_index order."""
        response = await client.get(
            f"/api/v1/chats/{test_chat_with_messages.id}",
            headers=auth_headers,
        )
        messages = response.json()["messages"]
        for i in range(len(messages) - 1):
            assert messages[i]["order_index"] < messages[i + 1]["order_index"]

    @pytest.mark.asyncio
    async def test_get_chat_not_found(self, client, auth_headers):
        response = await client.get(
            f"/api/v1/chats/{uuid.uuid4()}", headers=auth_headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_other_user_chat_returns_404(self, client, other_user_chat, auth_headers):
        response = await client.get(
            f"/api/v1/chats/{other_user_chat.chat.id}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_metadata_hidden_for_regular_user(self, client, test_chat_with_messages, auth_headers):
        response = await client.get(
            f"/api/v1/chats/{test_chat_with_messages.id}",
            headers=auth_headers,
        )
        for msg in response.json()["messages"]:
            assert msg.get("metadata", {}) == {} or msg.get("metadata") is None


class TestUpdateChat:
    @pytest.mark.asyncio
    async def test_update_title(self, client, test_chat, auth_headers):
        response = await client.patch(
            f"/api/v1/chats/{test_chat.id}",
            headers=auth_headers,
            json={"title": "Updated Title"},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Updated Title"

    @pytest.mark.asyncio
    async def test_update_not_found(self, client, auth_headers):
        response = await client.patch(
            f"/api/v1/chats/{uuid.uuid4()}",
            headers=auth_headers,
            json={"title": "New Title"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_other_user_chat(self, client, other_user_chat, auth_headers):
        response = await client.patch(
            f"/api/v1/chats/{other_user_chat.chat.id}",
            headers=auth_headers,
            json={"title": "Hacked"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_empty_title_rejected(self, client, test_chat, auth_headers):
        response = await client.patch(
            f"/api/v1/chats/{test_chat.id}",
            headers=auth_headers,
            json={"title": ""},
        )
        assert response.status_code == 422


class TestDeleteChat:
    @pytest.mark.asyncio
    async def test_delete_success(self, client, test_chat, auth_headers):
        response = await client.delete(
            f"/api/v1/chats/{test_chat.id}", headers=auth_headers
        )
        assert response.status_code == 200

        # Verify soft-deleted — should not appear in list
        get_resp = await client.get("/api/v1/chats", headers=auth_headers)
        ids = [c["id"] for c in get_resp.json()["chats"]]
        assert test_chat.id not in ids

    @pytest.mark.asyncio
    async def test_delete_not_found(self, client, auth_headers):
        response = await client.delete(
            f"/api/v1/chats/{uuid.uuid4()}", headers=auth_headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_other_user_chat(self, client, other_user_chat, auth_headers):
        response = await client.delete(
            f"/api/v1/chats/{other_user_chat.chat.id}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_already_deleted(self, client, deleted_chat, auth_headers):
        response = await client.delete(
            f"/api/v1/chats/{deleted_chat.id}", headers=auth_headers
        )
        assert response.status_code == 404


class TestRestoreChat:
    @pytest.mark.asyncio
    async def test_restore_requires_admin(self, client, deleted_chat, auth_headers):
        response = await client.post(
            f"/api/v1/chats/{deleted_chat.id}/restore",
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_empty_message_rejected(self, client, test_chat, auth_headers):
        response = await client.post(
            f"/api/v1/chats/{test_chat.id}/messages",
            headers=auth_headers,
            json={"content": ""},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_send_to_nonexistent_chat(self, client, auth_headers):
        response = await client.post(
            f"/api/v1/chats/{uuid.uuid4()}/messages",
            headers=auth_headers,
            json={"content": "Hello"},
        )
        assert response.status_code in [400, 404]

    @pytest.mark.asyncio
    async def test_send_to_other_user_chat(self, client, other_user_chat, auth_headers):
        response = await client.post(
            f"/api/v1/chats/{other_user_chat.chat.id}/messages",
            headers=auth_headers,
            json={"content": "Hacked"},
        )
        assert response.status_code in [400, 404]


class TestChatMemory:
    @pytest.mark.asyncio
    async def test_memory_admin_only(self, client, test_chat, auth_headers):
        response = await client.get(
            f"/api/v1/chats/{test_chat.id}/memory",
            headers=auth_headers,
        )
        assert response.status_code == 403
