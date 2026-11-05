"""覆盖预览、确认、异常退出和真实 CLI 参数解析。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telethon import functions, types
from typer.testing import CliRunner

from telefold import cli
from telefold import client as client_module
from telefold.client import DialogInfo
from telefold.config import Config, LLMConfig


@pytest.fixture
def environment(monkeypatch):
    """提供完整的清理、置顶和分组输入，所有 Telegram 与 LLM 调用均由 mock 接管。"""
    cfg = Config(LLMConfig(api_key="测试密钥", model="测试模型"))
    monkeypatch.setattr(cli.Config, "load", Mock(return_value=cfg))
    client = AsyncMock()
    client.get_me.return_value = SimpleNamespace(first_name="测试用户", phone="测试号码")
    client.return_value = True
    create = AsyncMock(return_value=client)
    fetch = AsyncMock(
        return_value=[
            DialogInfo(1, "待删除", "deleted", _peer=types.InputPeerUser(1, 1)),
            DialogInfo(777000, "Telegram", "user", _peer=types.InputPeerUser(777000, 777)),
            DialogInfo(2, "技术群", "group", _peer=types.InputPeerChat(2)),
        ]
    )
    classify = AsyncMock(return_value=[{"name": "技术", "dialog_ids": [2]}])
    delete = AsyncMock()
    apply = AsyncMock()
    plan = client_module.FolderPlan(
        snapshot=(),
        updates=(
            functions.messages.UpdateDialogFilterRequest(
                id=2,
                filter=types.DialogFilter(
                    id=2,
                    title=types.TextWithEntities(text="技术", entities=[]),
                    pinned_peers=[],
                    include_peers=[types.InputPeerChat(2)],
                    exclude_peers=[],
                ),
            ),
        ),
        removals=(),
        removed_names=(),
        shared_count=0,
    )
    prepare = AsyncMock(return_value=plan)
    for name, value in {
        "create_client": create,
        "fetch_dialogs": fetch,
        "classify": classify,
        "delete_dialogs": delete,
        "apply_folders": apply,
        "prepare_folder_plan": prepare,
    }.items():
        monkeypatch.setattr(cli, name, value)
    return SimpleNamespace(
        client=client,
        create=create,
        fetch=fetch,
        classify=classify,
        delete=delete,
        apply=apply,
        prepare=prepare,
        plan=plan,
    )


def assert_no_writes(env):
    env.client.assert_not_awaited()
    env.delete.assert_not_awaited()
    env.apply.assert_not_awaited()


@pytest.mark.parametrize("yes", [False, True])
def test_run_dry_run_never_writes(environment, yes):
    """预览必须展示实际目标，即使同时指定 --yes 也不得执行内容写入。"""
    env = environment
    args = ["run", "--dry-run"] + (["--yes"] if yes else [])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert_no_writes(env)
    env.classify.assert_awaited_once()
    env.client.disconnect.assert_awaited_once()
    assert "删除 1" in result.output
    assert "置顶 1" in result.output
    assert "重建 1" in result.output
    assert "技术群 (id=2)" in result.output


def test_declining_confirmation_cancels_every_write(environment):
    env = environment
    result = CliRunner().invoke(cli.app, ["run"], input="n\n")
    assert result.exit_code == 0, result.output
    assert_no_writes(env)
    env.client.disconnect.assert_awaited_once()


@pytest.mark.parametrize("yes", [False, True])
def test_confirmed_run_applies_the_displayed_plan(environment, yes):
    """交互确认和 --yes 都只能按已展示的目标及顺序执行整份计划。"""
    env = environment
    events = []

    async def deleted(client, dialogs):
        """记录清理顺序，并检查客户端及待删除对话与计划完全一致。"""
        assert client is env.client
        assert [dialog.id for dialog in dialogs] == [1]
        events.append("删除")

    async def pinned(request):
        """只允许置顶计划中指定的官方 peer。"""
        events.append("置顶")
        assert request.peer.user_id == 777000 and request.pinned is True
        return True

    async def applied(client, plan):
        """只允许将计划中的普通对话写入已展示的分组。"""
        events.append("分组")
        assert client is env.client
        assert plan is env.plan

    env.delete.side_effect = deleted
    env.client.side_effect = pinned
    env.apply.side_effect = applied
    result = CliRunner().invoke(cli.app, ["run"] + (["-y"] if yes else []), input=" y \n")
    assert result.exit_code == 0, result.output
    assert events == ["分组", "置顶", "删除"]
    env.client.disconnect.assert_awaited_once()


@pytest.mark.parametrize("stage", ["get_me", "fetch", "classify", "prepare", "apply", "delete"])
def test_failure_always_disconnects(environment, stage):
    """读取或分类失败不得写入，任何执行阶段失败也必须断开连接。"""
    env = environment
    target = env.client.get_me if stage == "get_me" else getattr(env, stage)
    target.side_effect = RuntimeError("模拟失败")
    result = CliRunner().invoke(cli.app, ["run", "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    env.client.disconnect.assert_awaited_once()
    if stage in {"get_me", "fetch", "classify", "prepare"}:
        assert_no_writes(env)
    elif stage == "apply":
        env.client.assert_not_awaited()
        env.delete.assert_not_awaited()


def test_invalid_classification_prevents_all_writes(environment):
    env = environment
    env.classify.return_value = [{"name": "技术", "dialog_ids": [999]}]
    result = CliRunner().invoke(cli.app, ["run", "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)
    assert_no_writes(env)
    env.client.disconnect.assert_awaited_once()


def test_no_dialogs_does_not_clear_existing_folders(environment):
    env = environment
    env.fetch.return_value = []
    result = CliRunner().invoke(cli.app, ["run", "--yes"])
    assert result.exit_code == 0, result.output
    env.classify.assert_not_awaited()
    assert_no_writes(env)
    env.client.disconnect.assert_awaited_once()


def test_cleanup_only_does_not_clear_existing_folders(environment):
    env = environment
    env.fetch.return_value = [DialogInfo(1, "待删除", "deleted", _peer=types.InputPeerUser(1, 1))]
    result = CliRunner().invoke(cli.app, ["run", "--yes"])
    assert result.exit_code == 0, result.output
    env.delete.assert_awaited_once()
    env.apply.assert_not_awaited()
    env.classify.assert_not_awaited()
    env.client.assert_not_awaited()


def test_types_are_filtered_before_classification(environment):
    """类型选项允许空白，但模型输入只包含过滤后实际需要分类的对话。"""
    env = environment
    result = CliRunner().invoke(cli.app, ["run", "--dry-run", "-t", " channel , group "])
    assert result.exit_code == 0, result.output
    assert [d.id for d in env.classify.await_args.args[1]] == [2]
    assert_no_writes(env)


@pytest.mark.parametrize("args", [["-t", "unknown"], ["-t", ""], ["-t", "group,"], ["-s", "-1"]])
def test_invalid_options_do_not_connect(environment, args):
    result = CliRunner().invoke(cli.app, ["run", *args])
    assert result.exit_code == 2, result.output
    environment.create.assert_not_awaited()


@pytest.mark.parametrize("yes", [False, True])
def test_clean_dry_run_never_deletes(environment, yes):
    """清理命令的预览优先于 --yes，并在退出时释放连接。"""
    env = environment
    args = ["clean", "--dry-run"] + (["--yes"] if yes else [])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert_no_writes(env)
    env.client.disconnect.assert_awaited_once()


@pytest.mark.parametrize("answer,expected", [("n", False), ("y", True)])
def test_clean_requires_confirmation(environment, answer, expected):
    env = environment
    result = CliRunner().invoke(cli.app, ["clean"], input=answer + "\n")
    assert result.exit_code == 0, result.output
    assert env.delete.await_count == int(expected)
    env.client.disconnect.assert_awaited_once()


def test_clean_failure_always_disconnects(environment):
    env = environment
    env.fetch.side_effect = RuntimeError("读取失败")
    result = CliRunner().invoke(cli.app, ["clean", "--yes"])
    assert result.exit_code != 0
    env.client.disconnect.assert_awaited_once()


def test_cancellation_always_disconnects(environment):
    env = environment
    env.classify.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cli._run("config.jsonc", 5, None, False, False, None, True))
    assert_no_writes(env)
    env.client.disconnect.assert_awaited_once()


@pytest.mark.parametrize("command", [["run", "--dry-run", "--verbose"], ["clean", "--dry-run"]])
def test_untrusted_titles_cannot_forge_preview_lines(environment, command):
    """预览、详细输出和清理列表中的换行、ANSI、双向控制字符均显示为转义文本。"""
    env = environment
    env.fetch.return_value[0].title = "删除目标\n伪造计划\x1b[2J\u202e"
    env.fetch.return_value[2].title = "技术群\r覆盖\u200d"
    env.fetch.return_value[2].description = "简介\x1b[H"
    env.classify.return_value = [{"name": "组\x1b[2J", "dialog_ids": [2]}]
    result = CliRunner().invoke(cli.app, command, color=True)
    assert result.exit_code == 0, result.output
    assert "删除目标\\n伪造计划\\x1b[2J\\u202e" in result.output
    assert all(character not in result.output for character in ["\x1b", "\r", "\u202e", "\u200d"])
    if command[0] == "run":
        assert "技术群\\r覆盖\\u200d" in result.output
        assert "组\\x1b[2J" in result.output
    assert_no_writes(env)


@pytest.mark.parametrize("name", ["NSFW", "陌生人", "过长名称" * 4])
def test_invalid_name_cancels_cleanup_and_pinning(environment, name):
    """保留或过长名称在完整计划确认前拒绝，也不得触发删除和置顶。"""
    env = environment
    env.classify.return_value = [{"name": name, "dialog_ids": [2]}]
    result = CliRunner().invoke(cli.app, ["run", "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)
    assert_no_writes(env)
    env.client.disconnect.assert_awaited_once()


def test_group_count_violation_cancels_all_writes(environment):
    """即使对话覆盖完整，九个普通对话只有一个分组也不得进入任何写入。"""
    env = environment
    env.fetch.return_value = env.fetch.return_value[:2] + [
        DialogInfo(index, "技术群", "group", _peer=types.InputPeerChat(index))
        for index in range(2, 11)
    ]
    env.classify.return_value = [{"name": "技术", "dialog_ids": list(range(2, 11))}]
    result = CliRunner().invoke(cli.app, ["run", "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)
    assert "5-8" in str(result.exception)
    assert_no_writes(env)
    env.client.disconnect.assert_awaited_once()


def test_invalid_folder_peer_is_detected_before_any_write(environment, monkeypatch):
    """使用真实预检函数，验证缺少分组 peer 时连删除和置顶也不能先执行。"""
    env = environment
    env.fetch.return_value[2]._peer = None
    monkeypatch.setattr(cli, "prepare_folder_plan", client_module.prepare_folder_plan)
    result = CliRunner().invoke(cli.app, ["run", "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)
    assert_no_writes(env)
    env.client.disconnect.assert_awaited_once()


@pytest.mark.parametrize("command", ["run", "clean"])
def test_real_delete_failure_cannot_report_success(environment, monkeypatch, command):
    """实际删除 helper 的错误必须影响 CLI 退出状态，不能打印完成后静默成功。"""
    env = environment
    env.client.delete_dialog.side_effect = RuntimeError("实际删除被拒绝")
    monkeypatch.setattr(cli, "delete_dialogs", client_module.delete_dialogs)
    result = CliRunner().invoke(cli.app, [command, "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert "完成！" not in result.output
    env.client.disconnect.assert_awaited_once()


@pytest.mark.parametrize("failure", [False, RuntimeError("置顶失败")])
def test_pin_failure_stops_before_irreversible_cleanup(environment, failure):
    """置顶返回 False 不是成功，必须停止后续对话删除并非零退出。"""
    env = environment
    if isinstance(failure, Exception):
        env.client.side_effect = failure
    else:
        env.client.return_value = failure
    result = CliRunner().invoke(cli.app, ["run", "--yes"])
    assert result.exit_code != 0
    env.apply.assert_awaited_once()
    env.delete.assert_not_awaited()
    assert "完成！" not in result.output
    env.client.disconnect.assert_awaited_once()


@pytest.mark.parametrize("mode", ["confirm", "dry-run", "decline"])
def test_real_preflight_and_apply_complete_before_deleting_dialogs(environment, monkeypatch, mode):
    """串联真实 helper，验证确认后的执行顺序以及预览、取消时只有只读请求。"""
    env = environment
    old = types.DialogFilter(
        id=2,
        title=types.TextWithEntities(text="旧分组", entities=[]),
        pinned_peers=[],
        include_peers=[],
        exclude_peers=[],
    )
    filters = {2: old}
    events = []

    async def rpc(request):
        """记录实际 RPC，只有更新成功后才更改模拟服务器中的分组。"""
        if isinstance(request, functions.messages.GetDialogFiltersRequest):
            return SimpleNamespace(filters=list(filters.values()))
        if isinstance(request, functions.messages.UpdateDialogFilterRequest):
            assert request.filter is not None
            events.append("分组")
            filters[request.id] = request.filter
        else:
            assert isinstance(request, functions.messages.ToggleDialogPinRequest)
            events.append("置顶")
        return True

    async def deleted(peer):
        """不可逆删除必须位于分组和置顶操作之后。"""
        assert peer.user_id == 1
        events.append("删除")

    env.client.side_effect = rpc
    env.client.delete_dialog.side_effect = deleted
    monkeypatch.setattr(cli, "prepare_folder_plan", client_module.prepare_folder_plan)
    monkeypatch.setattr(cli, "apply_folders", client_module.apply_folders)
    monkeypatch.setattr(cli, "delete_dialogs", client_module.delete_dialogs)
    monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock())
    args = ["run"] + ([] if mode == "decline" else ["--yes"])
    if mode == "dry-run":
        args.append("--dry-run")
    result = CliRunner().invoke(cli.app, args, input="n\n")
    assert result.exit_code == 0, result.output
    assert events == (["分组", "置顶", "删除"] if mode == "confirm" else [])
    assert filters[2].title.text == ("技术" if mode == "confirm" else "旧分组")
    env.client.disconnect.assert_awaited_once()
