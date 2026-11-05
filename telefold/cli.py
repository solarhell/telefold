"""TeleFold CLI 入口：先预览完整计划，再统一确认所有写入。"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from telethon import functions

from telefold.classifier import classify
from telefold.client import apply_folders, create_client, delete_dialogs, fetch_dialogs
from telefold.config import Config
from telefold.display import display_text
from telefold.planner import build_folders, group_dialogs

app = typer.Typer(
    help="LLM 驱动的 Telegram 分组自动整理工具",
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
DIALOG_TYPES = {"channel", "supergroup", "group", "user", "bot", "stranger", "deleted"}


@app.callback()
def callback():
    """LLM 驱动的 Telegram 分组自动整理工具。"""


@app.command()
def run(
    config: Annotated[str, typer.Option("-c", "--config", help="配置文件路径")] = "config.jsonc",
    samples: Annotated[
        int, typer.Option("-s", "--samples", min=0, help="每个对话抓取的样本消息数")
    ] = 5,
    types: Annotated[str | None, typer.Option("-t", "--types", help="按类型过滤，逗号分隔")] = None,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="打印详细对话信息")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="仅预览，不删除、置顶或修改分组")
    ] = False,
    code: Annotated[
        str | None, typer.Option("--code", help="Telegram 登录验证码（非交互式）")
    ] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="确认执行计划中的全部操作")] = False,
):
    """抓取对话 → LLM 分类 → 预览计划 → 确认后应用。"""
    asyncio.run(_run(config, samples, types, verbose, dry_run, code, yes))


@app.command()
def clean(
    config: Annotated[str, typer.Option("-c", "--config", help="配置文件路径")] = "config.jsonc",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="仅预览待删除对话")] = False,
    code: Annotated[
        str | None, typer.Option("--code", help="Telegram 登录验证码（非交互式）")
    ] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="确认删除已注销账号的对话")] = False,
):
    """预览并清理已注销账号的对话。"""
    asyncio.run(_clean(config, code, yes, dry_run))


def _parse_types(value: str | None) -> set[str] | None:
    """在连接之前校验类型选项，忽略分隔符周围的空白。"""
    if value is None:
        return None
    selected = {item.strip() for item in value.split(",")}
    if not selected or selected - DIALOG_TYPES:
        raise typer.BadParameter(
            "对话类型须为：" + ", ".join(sorted(DIALOG_TYPES)), param_hint="--types"
        )
    return selected


async def _run(
    config: str,
    samples: int,
    types: str | None,
    verbose: bool,
    dry_run: bool,
    code: str | None,
    yes: bool,
) -> None:
    """构造并展示完整计划；预览或拒绝确认时不执行任何内容写入。"""
    selected_types = _parse_types(types)
    cfg = Config.load(config)
    print(":: 连接 Telegram...")
    client = await create_client(code=code)
    try:
        me = await client.get_me()
        print(f"  已登录: {display_text(me.first_name)} ({display_text(me.phone)})")
        print(f":: 抓取对话 (sample_count={samples})...")
        dialogs = await fetch_dialogs(client, sample_count=samples)
        print(f"  共 {len(dialogs)} 个对话")
        if verbose:
            for dialog in dialogs:
                print(f"    [{dialog.dialog_type}] {display_text(dialog.title)} (id={dialog.id})")
                print(
                    f"      用户名: {display_text(dialog.username)}，"
                    f"简介: {display_text(dialog.description)}"
                )
                print(
                    f"      成员数: {dialog.participants_count}，"
                    f"样本: {display_text(dialog.sample_messages)}"
                )
                print(
                    f"      标记: scam={dialog.scam} fake={dialog.fake} restricted={dialog.restricted}"
                )
        if selected_types is not None:
            dialogs = [d for d in dialogs if d.dialog_type in selected_types]
            print(f"  类型过滤后剩余 {len(dialogs)} 个对话")

        groups = group_dialogs(dialogs)
        if groups.regular:
            print(":: LLM 分类中...")
            classified = await classify(cfg.llm, groups.regular)
        else:
            classified = []
        folders = build_folders(groups, classified)

        print(":: 执行计划：")
        print(f"  删除 {len(groups.to_delete)} 个已注销、scam 或 fake 对话")
        for dialog in groups.to_delete:
            print(f"    删除: {display_text(dialog.title)} (id={dialog.id})")
        print(f"  置顶 {len(groups.official)} 个 Telegram 官方对话")
        if folders:
            print(f"  重建 {len(folders)} 个分组（会先删除已有自定义分组）")
            dialog_map = {dialog.id: dialog for dialog in groups.folder_dialogs}
            for folder in folders:
                print(f"    {display_text(folder['name'])} ({len(folder['dialog_ids'])} 个对话)")
                for dialog_id in folder["dialog_ids"]:
                    print(f"      {display_text(dialog_map[dialog_id].title)} (id={dialog_id})")
        else:
            print("  没有待创建的分组，保留已有分组")

        if dry_run:
            print(":: 预览模式，不执行删除、置顶或分组修改。")
            return
        if not (groups.to_delete or groups.official or folders):
            print(":: 没有需要执行的操作。")
            return
        if not yes and input("\n执行以上全部操作？[y/N] ").strip().lower() != "y":
            print("  已取消全部操作。")
            return

        if groups.to_delete:
            await delete_dialogs(client, groups.to_delete)
        for dialog in groups.official:
            try:
                await client(
                    functions.messages.ToggleDialogPinRequest(peer=dialog._peer, pinned=True)
                )
                print(f"  已置顶: Telegram (id={dialog.id})")
            except Exception as exc:
                print(f"  置顶 Telegram 失败: {display_text(exc)}")
        if folders:
            await apply_folders(client, folders, groups.folder_dialogs)
        print("  完成！")
    finally:
        await client.disconnect()


async def _clean(config: str, code: str | None, yes: bool, dry_run: bool) -> None:
    """预览或确认删除已注销对话，在成功、取消和异常路径都释放连接。"""
    Config.load(config)
    client = await create_client(code=code)
    try:
        dialogs = await fetch_dialogs(client, sample_count=0)
        deleted = [d for d in dialogs if d.dialog_type == "deleted"]
        if not deleted:
            print("未找到已注销账号的对话。")
            return
        print(f"找到 {len(deleted)} 个已注销账号的对话:")
        for dialog in deleted:
            print(f"  - {display_text(dialog.title)} (id={dialog.id})")
        if dry_run:
            print(":: 预览模式，不执行删除。")
            return
        if yes or input(f"\n永久删除这 {len(deleted)} 个对话？[y/N] ").strip().lower() == "y":
            await delete_dialogs(client, deleted)
            print("完成！")
        else:
            print("已取消删除。")
    finally:
        await client.disconnect()


def main():
    app()


if __name__ == "__main__":
    main()
