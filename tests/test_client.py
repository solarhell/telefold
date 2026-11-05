"""写入前完成输入校验，连接失败时释放会话。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from telethon import functions

from telefold import client as module
from telefold.client import DialogInfo


@pytest.mark.parametrize("failure", [RuntimeError("登录失败"), asyncio.CancelledError()])
@pytest.mark.parametrize("stage", ["connect", "start"])
def test_login_failure_disconnects(monkeypatch, failure, stage):
    client = AsyncMock()
    client.is_user_authorized.return_value = False
    getattr(client, stage).side_effect = failure
    monkeypatch.setattr(module, "TelegramClient", Mock(return_value=client))
    monkeypatch.setattr("builtins.input", lambda prompt: "+123456")
    with pytest.raises(type(failure)):
        asyncio.run(module.create_client(code="测试验证码"))
    client.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    "folders,dialogs",
    [
        ([{"name": "技术", "dialog_ids": [999]}], [DialogInfo(1, "一", "group", _peer="peer")]),
        ([{"name": "技术", "dialog_ids": [1]}], [DialogInfo(1, "一", "group")]),
        ([], [DialogInfo(1, "一", "group", _peer="peer")]),
    ],
)
def test_invalid_folder_request_does_not_delete_old_folders(folders, dialogs):
    client = AsyncMock()
    with pytest.raises(ValueError):
        asyncio.run(module.apply_folders(client, folders, dialogs))
    client.assert_not_awaited()


def test_empty_plan_does_not_delete_old_folders():
    client = AsyncMock()
    asyncio.run(module.apply_folders(client, [], []))
    client.assert_not_awaited()


def test_request_construction_failure_preserves_old_folders(monkeypatch):
    constructor = Mock(side_effect=[object(), ValueError("请求无效")])
    monkeypatch.setattr(module.types, "DialogFilter", constructor)
    client = AsyncMock()
    dialogs = [DialogInfo(i, "对话", "group", _peer=f"peer-{i}") for i in [1, 2]]
    folders = [{"name": "技术", "dialog_ids": [1]}, {"name": "生活", "dialog_ids": [2]}]
    with pytest.raises(ValueError, match="请求无效"):
        asyncio.run(module.apply_folders(client, folders, dialogs))
    client.assert_not_awaited()


def test_valid_folder_requests_are_applied_after_clearing(monkeypatch):
    client = AsyncMock()
    events = []

    async def clear(value):
        assert value is client
        events.append("清理")

    async def send(request):
        assert isinstance(request, functions.messages.UpdateDialogFilterRequest)
        assert request.filter.include_peers == [f"peer-{request.id - 1}"]
        events.append(request.id)

    monkeypatch.setattr(module, "clear_folders", clear)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    client.side_effect = send
    dialogs = [DialogInfo(i, "对话", "group", _peer=f"peer-{i}") for i in [1, 2]]
    folders = [{"name": "技术", "dialog_ids": [1]}, {"name": "生活", "dialog_ids": [2]}]
    asyncio.run(module.apply_folders(client, folders, dialogs))
    assert events == ["清理", 2, 3]


def test_clear_folders_preserves_default_folder(monkeypatch):
    from types import SimpleNamespace

    from telethon import types

    client = AsyncMock()
    custom = types.DialogFilter(
        id=2,
        title=types.TextWithEntities(text="自定义", entities=[]),
        pinned_peers=[],
        include_peers=[],
        exclude_peers=[],
    )
    client.side_effect = [SimpleNamespace(filters=[types.DialogFilterDefault(), custom]), True]
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    asyncio.run(module.clear_folders(client))
    calls = client.await_args_list
    assert len(calls) == 2
    assert isinstance(calls[0].args[0], functions.messages.GetDialogFiltersRequest)
    assert calls[1].args[0].id == 2
    assert calls[1].args[0].filter is None
