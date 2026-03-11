"""TeleFold CLI 入口。"""

from __future__ import annotations

import asyncio
from typing import Annotated, Optional

import typer

from telefold.config import Config
from telefold.client import create_client, fetch_dialogs, apply_folders, delete_dialogs
from telefold.classifier import classify

app = typer.Typer(help="LLM 驱动的 Telegram 分组自动整理工具", add_completion=False)


@app.callback()
def callback():
    """LLM 驱动的 Telegram 分组自动整理工具。"""


@app.command()
def run(
    config: Annotated[str, typer.Option("-c", "--config", help="配置文件路径")] = "config.jsonc",
    samples: Annotated[int, typer.Option("-s", "--samples", help="每个对话抓取的样本消息数")] = 5,
    types: Annotated[Optional[str], typer.Option("-t", "--types", help="按类型过滤，逗号分隔（channel,supergroup,group,user,bot）")] = None,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="打印详细对话信息")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只分类不应用")] = False,
    code: Annotated[Optional[str], typer.Option("--code", help="Telegram 登录验证码（非交互式）")] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="跳过确认提示")] = False,
):
    """抓取对话 → LLM 分类 → 创建分组。"""
    asyncio.run(_run(config, samples, types, verbose, dry_run, code, yes))


@app.command()
def clean(
    config: Annotated[str, typer.Option("-c", "--config", help="配置文件路径")] = "config.jsonc",
    code: Annotated[Optional[str], typer.Option("--code", help="Telegram 登录验证码（非交互式）")] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="跳过确认提示")] = False,
):
    """清理已注销账号的对话。"""
    asyncio.run(_clean(config, code, yes))


async def _run(
    config: str,
    samples: int,
    types: str | None,
    verbose: bool,
    dry_run: bool,
    code: str | None,
    yes: bool,
) -> None:
    cfg = Config.load(config)

    print(":: 连接 Telegram...")
    client = await create_client(code=code)
    me = await client.get_me()
    print(f"  已登录: {me.first_name} ({me.phone})")

    print(f":: 抓取对话 (sample_count={samples})...")
    dialogs = await fetch_dialogs(client, sample_count=samples)
    print(f"  共 {len(dialogs)} 个对话:")

    type_counts: dict[str, int] = {}
    for d in dialogs:
        type_counts[d.dialog_type] = type_counts.get(d.dialog_type, 0) + 1
    for t, c in sorted(type_counts.items()):
        print(f"    {t}: {c}")

    if verbose:
        print("\n  对话详情:")
        for d in dialogs:
            print(f"    [{d.dialog_type}] {d.title} (id={d.id})")
            if d.username:
                print(f"      @{d.username}")
            if d.description:
                print(f"      简介: {d.description[:80]}")
            if d.participants_count:
                print(f"      成员数: {d.participants_count}")
            if d.scam or d.fake or d.restricted:
                print(f"      标记: scam={d.scam} fake={d.fake} restricted={d.restricted}")
            if d.sample_messages:
                print(f"      消息: {[m[:50] for m in d.sample_messages[:2]]}")
        print()

    # 按类型过滤
    if types:
        allowed = set(types.split(","))
        dialogs = [d for d in dialogs if d.dialog_type in allowed]
        print(f"  过滤后剩余 {len(dialogs)} 个对话 (类型: {types})")

    # 自动清理: 已注销账号、scam、fake
    to_delete = [d for d in dialogs if d.dialog_type == "deleted" or d.scam or d.fake]
    dialogs = [d for d in dialogs if d not in to_delete]

    if to_delete:
        deleted_count = sum(1 for d in to_delete if d.dialog_type == "deleted")
        scam_count = sum(1 for d in to_delete if d.scam or d.fake)
        print(f"  自动清理: {deleted_count} 个已注销, {scam_count} 个 scam/fake")
        await delete_dialogs(client, to_delete)

    # 置顶 Telegram 官方账号，不参与 LLM 分类
    TELEGRAM_OFFICIAL_ID = 777000
    tg_official = [d for d in dialogs if d.id == TELEGRAM_OFFICIAL_ID]
    dialogs = [d for d in dialogs if d.id != TELEGRAM_OFFICIAL_ID]

    if tg_official:
        from telethon import functions as tg_functions
        try:
            await client(tg_functions.messages.ToggleDialogPinRequest(
                peer=tg_official[0]._peer, pinned=True,
            ))
            print(f"  已置顶: Telegram (id={TELEGRAM_OFFICIAL_ID})")
        except Exception as e:
            print(f"  置顶 Telegram 失败: {e}")

    # NSFW 过滤: restricted 标记（含白名单）+ 关键词匹配
    NSFW_KEYWORDS = [
        "反差", "少妇", "萝莉", "白丝", "内射", "口交",
        "吞精", "自慰", "无码", "蜜桃臀", "骚穴", "酥胸", "操骚",
        "调教", "母狗", "约炮", "福利姬", "裸舞", "色色", "吃瓜",
        "性欲", "erotic", "哺乳", "18+", "🔞",
        # escort / 外围
        "酒店預約", "酒店预约", "上门", "上門", "外围", "外圍",
        "楼凤", "樓鳳", "会所", "會所", "全套", "預約",
        # 色情内容
        "阅涩", "美女圖", "美女图", "睇圖", "睇图",
        "包养", "伴游", "防失联", "banana",
        # 行业暗语
        "修车俱乐部", "修车茶楼", "课表", "同城头条",
    ]

    # 仅匹配 title（不匹配 description，避免群规提到 NSFW 导致误报）
    NSFW_TITLE_KEYWORDS = [
        "nsfw", "av",
    ]

    # restricted 但不是 NSFW 的白名单（按小写 title 匹配）
    RESTRICTED_WHITELIST = [
        "openai",
    ]

    def _is_nsfw(d):
        title_lower = d.title.lower()
        text = (d.title + " " + (d.description or "")).lower()
        if d.restricted and title_lower not in RESTRICTED_WHITELIST:
            return True
        if any(kw in title_lower for kw in NSFW_TITLE_KEYWORDS):
            return True
        return any(kw in text for kw in NSFW_KEYWORDS)

    nsfw = [d for d in dialogs if _is_nsfw(d)]
    dialogs = [d for d in dialogs if not _is_nsfw(d)]

    # 陌生人单独归类
    strangers = [d for d in dialogs if d.dialog_type == "stranger"]
    dialogs = [d for d in dialogs if d.dialog_type != "stranger"]

    if not dialogs:
        print("  没有需要分类的对话。")
        folders = []
    else:
        print(":: LLM 分类中...")
        folders = await classify(cfg.llm, dialogs)

    if nsfw:
        folders.append({"name": "NSFW", "dialog_ids": [d.id for d in nsfw]})
        dialogs.extend(nsfw)
        print(f"  固定分组: NSFW ({len(nsfw)} 个对话)")

    if strangers:
        folders.append({"name": "陌生人", "dialog_ids": [d.id for d in strangers]})
        dialogs.extend(strangers)
        print(f"  固定分组: 陌生人 ({len(strangers)} 个对话)")

    print(f"  共 {len(folders)} 个分组:")
    for f in folders:
        print(f"    {f['name']} ({len(f['dialog_ids'])} 个对话)")

    if dry_run:
        print(":: 预览模式，跳过分组创建。")
    else:
        if not yes:
            confirm = input("\n应用这些分组到 Telegram？[y/N] ")
        else:
            confirm = "y"
        if confirm.lower() == "y":
            print(":: 应用分组...")
            await apply_folders(client, folders, dialogs)
            print("  完成！")
        else:
            print("  已跳过。")

    await client.disconnect()


async def _clean(config: str, code: str | None, yes: bool) -> None:
    cfg = Config.load(config)
    client = await create_client(code=code)
    dialogs = await fetch_dialogs(client, sample_count=0)
    deleted = [d for d in dialogs if d.dialog_type == "deleted"]

    if not deleted:
        print("未找到已注销账号。")
    else:
        print(f"找到 {len(deleted)} 个已注销账号:")
        for d in deleted:
            print(f"  - {d.title} (id={d.id})")
        if not yes:
            confirm = input(f"\n永久删除这 {len(deleted)} 个对话？[y/N] ")
        else:
            confirm = "y"
        if confirm.lower() == "y":
            await delete_dialogs(client, deleted)
            print("完成！")
        else:
            print("已跳过。")

    await client.disconnect()


def main():
    app()


if __name__ == "__main__":
    main()
