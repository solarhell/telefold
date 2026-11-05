"""使用真实 TL 类型和内存 RPC 状态验证预检、失败终止及分组替换顺序。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telethon import functions, types

from telefold import client as module
from telefold.client import DialogInfo


def ordinary(folder_id, title=None):
    """构造可真正序列化的普通旧分组，供快照和状态变更断言使用。"""
    return types.DialogFilter(
        id=folder_id,
        title=types.TextWithEntities(text=title or f"旧分组{folder_id}", entities=[]),
        pinned_peers=[],
        include_peers=[],
        exclude_peers=[],
    )


def shared(folder_id):
    """共享分组应保留其类型、内容和 ID，不能被普通分类覆盖。"""
    return types.DialogFilterChatlist(
        id=folder_id,
        title=types.TextWithEntities(text="共享分组", entities=[]),
        pinned_peers=[],
        include_peers=[],
    )


@pytest.fixture
def telegram(monkeypatch):
    """模拟有状态的 Telegram RPC；写入被拒绝时不改变服务器状态。"""
    state = SimpleNamespace(
        filters={0: types.DialogFilterDefault(), 2: ordinary(2), 3: ordinary(3)},
        writes=[],
        failure_id=None,
        refusal_id=None,
    )

    async def rpc(request):
        """记录真实请求，区分读取、写入和删除，允许注入单次远程失败。"""
        if isinstance(request, functions.messages.GetDialogFiltersRequest):
            return SimpleNamespace(filters=list(state.filters.values()))
        assert isinstance(request, functions.messages.UpdateDialogFilterRequest)
        state.writes.append(request)
        if request.id == state.failure_id:
            raise RuntimeError("模拟服务端失败")
        if request.id == state.refusal_id:
            return False
        if request.filter is None:
            del state.filters[request.id]
        else:
            state.filters[request.id] = request.filter
        return True

    state.client = AsyncMock(side_effect=rpc)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    return state


def prepare(telegram, folders=None, dialogs=None):
    """默认生成一个分组计划，各用例可单独替换输入和服务器状态。"""
    return asyncio.run(
        module.prepare_folder_plan(
            telegram.client,
            folders if folders is not None else [{"name": "技术", "dialog_ids": [1]}],
            dialogs
            if dialogs is not None
            else [DialogInfo(1, "技术群", "group", _peer=types.InputPeerChat(1))],
        )
    )


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
        (
            [{"name": "过长名称" * 4, "dialog_ids": [1]}],
            [DialogInfo(1, "一", "group", _peer=types.InputPeerChat(1))],
        ),
        (
            [{"name": "技术", "dialog_ids": [999]}],
            [DialogInfo(1, "一", "group", _peer=types.InputPeerChat(1))],
        ),
        ([{"name": "技术", "dialog_ids": [1]}], [DialogInfo(1, "一", "group")]),
        ([{"name": "技术", "dialog_ids": [1]}], [DialogInfo(1, "一", "group", _peer="无效 peer")]),
        (
            [{"name": "技术", "dialog_ids": [1]}],
            [DialogInfo(1, "一", "group", _peer=types.InputPeerEmpty())],
        ),
        ([], [DialogInfo(1, "一", "group", _peer=types.InputPeerChat(1))]),
    ],
)
def test_invalid_request_fails_before_reading_or_writing(folders, dialogs):
    """缺少目标、非法 peer 或不合规分组在任何 RPC 之前就被拒绝。"""
    client = AsyncMock()
    with pytest.raises(ValueError):
        asyncio.run(module.prepare_folder_plan(client, folders, dialogs))
    client.assert_not_awaited()


def test_empty_plan_preserves_every_existing_folder(telegram):
    """空输入既不清空旧分组，也不需要读取分组快照。"""
    plan = prepare(telegram, [], [])
    asyncio.run(module.apply_folders(telegram.client, plan))
    telegram.client.assert_not_awaited()
    assert set(telegram.filters) == {0, 2, 3}


def test_preflight_serializes_complete_requests_before_mutation(telegram):
    """TL 请求编码错误也在确认和任何删除之前发现。"""
    with pytest.raises(UnicodeError):
        prepare(telegram, [{"name": "\ud800", "dialog_ids": [1]}])
    assert telegram.writes == []
    assert set(telegram.filters) == {0, 2, 3}


def test_updates_finish_before_surplus_folders_are_removed(telegram):
    """复用旧 ID，写入成功后才移除多余分组，默认分组始终保留。"""
    plan = prepare(telegram)
    assert [request.id for request in plan.updates] == [2]
    assert [request.id for request in plan.removals] == [3]
    assert plan.removed_names == ("旧分组3",)
    asyncio.run(module.apply_folders(telegram.client, plan))
    assert [(request.id, request.filter is None) for request in telegram.writes] == [
        (2, False),
        (3, True),
    ]
    assert set(telegram.filters) == {0, 2}
    assert telegram.filters[2].include_peers[0].chat_id == 1


@pytest.mark.parametrize("reject", ["exception", "false"])
def test_first_update_failure_does_not_clear_old_folders(telegram, reject):
    """服务端拒绝首个更新时，程序不得提前清空任何旧分组。"""
    plan = prepare(telegram)
    before = tuple(bytes(folder) for folder in telegram.filters.values())
    if reject == "exception":
        telegram.failure_id = 2
    else:
        telegram.refusal_id = 2
    with pytest.raises(RuntimeError):
        asyncio.run(module.apply_folders(telegram.client, plan))
    assert tuple(bytes(folder) for folder in telegram.filters.values()) == before
    assert all(request.filter is not None for request in telegram.writes)


def test_later_update_failure_never_starts_removing_old_folders(telegram):
    """部分更新失败会立即停止，不会继续删除未替换的旧分组。"""
    telegram.filters[4] = ordinary(4)
    dialogs = [DialogInfo(i, "对话", "group", _peer=types.InputPeerChat(i)) for i in [1, 2]]
    plan = prepare(
        telegram,
        [{"name": "技术", "dialog_ids": [1]}, {"name": "生活", "dialog_ids": [2]}],
        dialogs,
    )
    telegram.failure_id = 3
    with pytest.raises(RuntimeError):
        asyncio.run(module.apply_folders(telegram.client, plan))
    assert set(telegram.filters) == {0, 2, 3, 4}
    assert telegram.filters[3].title.text == "旧分组3"
    assert telegram.filters[4].title.text == "旧分组4"
    assert all(request.filter is not None for request in telegram.writes)


def test_removal_refusal_is_not_reported_as_success(telegram):
    """删除多余分组返回 False 时必须报错，不伪装成整份计划成功。"""
    plan = prepare(telegram)
    telegram.refusal_id = 3
    with pytest.raises(RuntimeError, match="拒绝移除"):
        asyncio.run(module.apply_folders(telegram.client, plan))
    assert 3 in telegram.filters


def test_shared_folders_are_not_overwritten_or_removed(telegram):
    """创建普通分组时避开共享 ID，并保留共享分组的内容。"""
    telegram.filters = {0: types.DialogFilterDefault(), 2: shared(2), 3: ordinary(3)}
    before = bytes(telegram.filters[2])
    dialogs = [DialogInfo(i, "对话", "group", _peer=types.InputPeerChat(i)) for i in [1, 2]]
    plan = prepare(
        telegram,
        [{"name": "技术", "dialog_ids": [1]}, {"name": "生活", "dialog_ids": [2]}],
        dialogs,
    )
    assert [request.id for request in plan.updates] == [3, 4]
    assert plan.shared_count == 1
    asyncio.run(module.apply_folders(telegram.client, plan))
    assert bytes(telegram.filters[2]) == before
    assert set(telegram.filters) == {0, 2, 3, 4}
    assert [telegram.filters[i].include_peers[0].chat_id for i in [3, 4]] == [1, 2]
    assert [telegram.filters[i].title.text for i in [3, 4]] == ["技术", "生活"]


def test_external_change_after_preview_aborts_before_writing(telegram):
    """其他客户端修改过分组时，不能继续执行用户未预览的旧计划。"""
    plan = prepare(telegram)
    telegram.filters[2] = ordinary(2, "另一台设备已修改")
    with pytest.raises(RuntimeError, match="预览后发生变化"):
        asyncio.run(module.apply_folders(telegram.client, plan))
    assert telegram.writes == []
    assert telegram.filters[2].title.text == "另一台设备已修改"


def test_delete_failure_is_propagated_and_stops_the_batch():
    """真实删除 helper 的 RPC 异常必须向上传递，后续目标不再删除。"""
    client = AsyncMock()
    client.delete_dialog.side_effect = RuntimeError("删除失败")
    dialogs = [DialogInfo(i, "已注销", "deleted", _peer=types.InputPeerUser(i, i)) for i in [1, 2]]
    with pytest.raises(RuntimeError, match="删除失败"):
        asyncio.run(module.delete_dialogs(client, dialogs))
    assert client.delete_dialog.await_count == 1
    assert client.delete_dialog.await_args.args[0].user_id == 1


def test_delete_batch_checks_later_peers_before_first_deletion():
    """批次中的后续目标无效时，也不能先删除前面的有效目标。"""
    client = AsyncMock()
    dialogs = [
        DialogInfo(1, "已注销", "deleted", _peer=types.InputPeerUser(1, 1)),
        DialogInfo(2, "缺少 peer", "deleted"),
    ]
    with pytest.raises(ValueError, match="缺少 Telegram peer"):
        asyncio.run(module.delete_dialogs(client, dialogs))
    client.delete_dialog.assert_not_awaited()
