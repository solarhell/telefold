"""覆盖预览、确认、异常退出和真实 CLI 参数解析。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from typer.testing import CliRunner

from telefold import cli
from telefold.client import DialogInfo
from telefold.config import Config, LLMConfig


@pytest.fixture
def environment(monkeypatch):
    cfg = Config(LLMConfig(api_key="测试密钥", model="测试模型"))
    monkeypatch.setattr(cli.Config, "load", Mock(return_value=cfg))
    client = AsyncMock()
    client.get_me.return_value = SimpleNamespace(first_name="测试用户", phone="测试号码")
    create = AsyncMock(return_value=client)
    fetch = AsyncMock(
        return_value=[
            DialogInfo(1, "待删除", "deleted", _peer="deleted-peer"),
            DialogInfo(777000, "Telegram", "user", _peer="official-peer"),
            DialogInfo(2, "技术群", "group", _peer="group-peer"),
        ]
    )
    classify = AsyncMock(return_value=[{"name": "技术", "dialog_ids": [2]}])
    delete = AsyncMock()
    apply = AsyncMock()
    for name, value in {
        "create_client": create,
        "fetch_dialogs": fetch,
        "classify": classify,
        "delete_dialogs": delete,
        "apply_folders": apply,
    }.items():
        monkeypatch.setattr(cli, name, value)
    return SimpleNamespace(
        client=client, create=create, fetch=fetch, classify=classify, delete=delete, apply=apply
    )


def assert_no_writes(env):
    env.client.assert_not_awaited()
    env.delete.assert_not_awaited()
    env.apply.assert_not_awaited()


@pytest.mark.parametrize("yes", [False, True])
def test_run_dry_run_never_writes(environment, yes):
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
    env = environment
    events = []

    async def deleted(*args):
        events.append("删除")

    async def pinned(request):
        events.append("置顶")
        assert request.peer == "official-peer" and request.pinned is True

    async def applied(client, folders, dialogs):
        events.append("分组")
        assert folders == [{"name": "技术", "dialog_ids": [2]}]
        assert [d.id for d in dialogs] == [2]

    env.delete.side_effect = deleted
    env.client.side_effect = pinned
    env.apply.side_effect = applied
    result = CliRunner().invoke(cli.app, ["run"] + (["-y"] if yes else []), input=" y \n")
    assert result.exit_code == 0, result.output
    assert events == ["删除", "置顶", "分组"]
    env.client.disconnect.assert_awaited_once()


@pytest.mark.parametrize("stage", ["get_me", "fetch", "classify", "delete", "apply"])
def test_failure_always_disconnects(environment, stage):
    env = environment
    target = env.client.get_me if stage == "get_me" else getattr(env, stage)
    target.side_effect = RuntimeError("模拟失败")
    result = CliRunner().invoke(cli.app, ["run", "--yes"])
    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    env.client.disconnect.assert_awaited_once()
    if stage in {"get_me", "fetch", "classify"}:
        assert_no_writes(env)


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
    env.fetch.return_value = [DialogInfo(1, "待删除", "deleted")]
    result = CliRunner().invoke(cli.app, ["run", "--yes"])
    assert result.exit_code == 0, result.output
    env.delete.assert_awaited_once()
    env.apply.assert_not_awaited()
    env.classify.assert_not_awaited()
    env.client.assert_not_awaited()


def test_types_are_filtered_before_classification(environment):
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
