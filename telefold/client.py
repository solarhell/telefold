"""Telegram 客户端，负责抓取对话和应用分组。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from telethon import TelegramClient, functions, types


@dataclass
class DialogInfo:
    id: int
    title: str
    dialog_type: str  # "channel", "group", "supergroup", "user", "bot", "stranger", "deleted"
    username: str | None = None
    description: str | None = None
    participants_count: int | None = None
    scam: bool = False
    fake: bool = False
    restricted: bool = False
    sample_messages: list[str] | None = None

    # 保留原始 peer 用于 API 调用
    _peer: object = None


async def create_client(code: str | None = None) -> TelegramClient:
    phone = input("请输入手机号（含国际区号，如 +86...）: ").strip()
    session_name = phone.replace("+", "").replace(" ", "")

    client = TelegramClient(
        session_name,
        2040,
        "b18441a1ff607e10a989891a5462e627",
        device_model="Desktop",
        system_version="Windows 10",
        app_version="5.12.1 x64",
        lang_code="en",
        system_lang_code="en-US",
    )
    await client.connect()

    if await client.is_user_authorized():
        print("  复用已有会话，无需输入验证码")
    else:
        code_cb = (lambda: code) if code else lambda: input("请输入验证码（来自 Telegram 官方账号）: ")
        password_cb = lambda: input("请输入两步验证密码: ")
        await client.start(phone=phone, code_callback=code_cb, password=password_cb)

    return client


async def fetch_dialogs(
    client: TelegramClient,
    sample_count: int = 5,
) -> list[DialogInfo]:
    """抓取所有对话，可选获取样本消息。"""
    dialogs = await client.get_dialogs()
    total = len(dialogs)
    result: list[DialogInfo] = []

    for i, dlg in enumerate(dialogs, 1):
        entity = dlg.entity

        scam = getattr(entity, "scam", False) or False
        fake = getattr(entity, "fake", False) or False
        restricted = getattr(entity, "restricted", False) or False

        if isinstance(entity, types.Channel):
            if entity.broadcast:
                dtype = "channel"
            else:
                dtype = "supergroup"
            username = entity.username
            title = entity.title
        elif isinstance(entity, types.Chat):
            dtype = "group"
            username = None
            title = entity.title
        elif isinstance(entity, types.User):
            if entity.deleted:
                dtype = "deleted"
                username = None
                title = "已注销帐号"
                info = DialogInfo(
                    id=dlg.id,
                    title=title,
                    dialog_type=dtype,
                    username=username,
                    _peer=dlg.input_entity,
                )
                result.append(info)
                continue
            if entity.bot:
                dtype = "bot"
            elif entity.contact:
                dtype = "user"
            else:
                dtype = "stranger"
            username = entity.username
            title = " ".join(filter(None, [entity.first_name, entity.last_name]))
        else:
            continue

        print(f"\r  [{i}/{total}] {title[:30]}", end="", flush=True)

        info = DialogInfo(
            id=dlg.id,
            title=title or "",
            dialog_type=dtype,
            username=username,
            scam=scam,
            fake=fake,
            restricted=restricted,
            _peer=dlg.input_entity,
        )

        # 获取频道/群组的简介和成员数
        if isinstance(entity, (types.Channel,)):
            try:
                full = await client(functions.channels.GetFullChannelRequest(channel=dlg.input_entity))
                info.description = full.full_chat.about or None
                info.participants_count = full.full_chat.participants_count
            except Exception:
                pass
            await asyncio.sleep(0.3)
        elif isinstance(entity, types.Chat):
            try:
                full = await client(functions.messages.GetFullChatRequest(chat_id=entity.id))
                info.description = full.full_chat.about or None
                info.participants_count = getattr(full.full_chat, "participants_count", None)
            except Exception:
                pass
            await asyncio.sleep(0.3)

        # 获取样本消息
        if sample_count > 0:
            try:
                messages = await client.get_messages(dlg.input_entity, limit=sample_count)
                info.sample_messages = [
                    m.text for m in messages if m.text
                ]
            except Exception:
                info.sample_messages = []

            # 限速：请求间短暂延迟
            await asyncio.sleep(0.3)

        result.append(info)

    print()  # 换行
    return result


async def delete_dialogs(client: TelegramClient, dialogs: list[DialogInfo]) -> None:
    """永久删除对话。"""
    for d in dialogs:
        try:
            await client.delete_dialog(d._peer)
            print(f"  x 已删除: {d.title} (id={d.id})")
        except Exception as e:
            print(f"  ! 删除失败 {d.title}: {e}")
        await asyncio.sleep(0.5)


async def clear_folders(client: TelegramClient) -> None:
    """移除所有自定义分组。"""
    existing = await client(functions.messages.GetDialogFiltersRequest())
    if hasattr(existing, "filters"):
        for f in existing.filters:
            if hasattr(f, "id") and f.id >= 2:
                await client(functions.messages.UpdateDialogFilterRequest(id=f.id))
                print(f"  - 已移除分组 (id={f.id})")
                await asyncio.sleep(0.5)


async def apply_folders(
    client: TelegramClient,
    folders: list[dict],
    dialogs: list[DialogInfo],
) -> None:
    """根据分类结果创建 Telegram 分组。"""
    dialog_map = {d.id: d for d in dialogs}

    await clear_folders(client)

    next_id = 2
    for folder in folders:
        include_peers = []
        for did in folder["dialog_ids"]:
            d = dialog_map.get(did)
            if d and d._peer:
                include_peers.append(d._peer)

        if not include_peers:
            continue

        dialog_filter = types.DialogFilter(
            id=next_id,
            title=types.TextWithEntities(
                text=folder["name"],
                entities=[],
            ),
            pinned_peers=[],
            include_peers=include_peers,
            exclude_peers=[],
        )

        await client(functions.messages.UpdateDialogFilterRequest(
            id=next_id,
            filter=dialog_filter,
        ))

        print(f"  + 已创建分组: {folder['name']} ({len(include_peers)} 个对话)")
        next_id += 1

        await asyncio.sleep(1)  # 限速
