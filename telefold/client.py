"""Telegram 客户端，负责抓取对话和应用分组。"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

from telethon import TelegramClient, functions, types, utils

from telefold.display import display_text
from telefold.planner import validate_folders


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
    """连接并复用或建立授权会话；返回前发生异常时负责断开连接。"""
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
    try:
        await client.connect()
        if await client.is_user_authorized():
            print("  复用已有会话，无需输入验证码")
        else:

            def code_cb():
                """优先使用显式验证码，否则交互式读取本次登录验证码。"""
                return code or input("请输入验证码（来自 Telegram 官方账号）: ")

            def password_cb():
                return input("请输入两步验证密码: ")

            await client.start(phone=phone, code_callback=code_cb, password=password_cb)
    except BaseException:
        await client.disconnect()
        raise

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

        print(f"\r  [{i}/{total}] {display_text(title[:30])}", end="", flush=True)

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
                full = await client(
                    functions.channels.GetFullChannelRequest(channel=dlg.input_entity)
                )
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
                info.sample_messages = [m.text for m in messages if m.text]
            except Exception:
                info.sample_messages = []

            # 限速：请求间短暂延迟
            await asyncio.sleep(0.3)

        result.append(info)

    print()  # 换行
    return result


@dataclass(frozen=True)
class FolderPlan:
    """保存已编码验证的写入请求和预览时的分组快照，共享分组不参与替换。"""

    snapshot: tuple[bytes, ...]
    updates: tuple[functions.messages.UpdateDialogFilterRequest, ...]
    removals: tuple[functions.messages.UpdateDialogFilterRequest, ...]
    removed_names: tuple[str, ...]
    shared_count: int


def prepare_dialog_peers(dialogs: list[DialogInfo]) -> dict[int, types.TypeInputPeer]:
    """在任何写入前校验全部目标，并将它们转换为可序列化的 InputPeer。"""
    peers = {}
    for dialog in dialogs:
        if dialog.id in peers:
            raise ValueError(f"读取到重复对话 ID：{dialog.id}，已取消执行。")
        if dialog._peer is None:
            raise ValueError(f"对话 {dialog.id} 缺少 Telegram peer，已取消执行。")
        try:
            peer = utils.get_input_peer(dialog._peer)
            if isinstance(peer, types.InputPeerEmpty):
                raise ValueError("空 peer 不能作为修改目标")
            bytes(peer)
        except (TypeError, ValueError, OverflowError, struct.error) as exc:
            raise ValueError(f"对话 {dialog.id} 的 Telegram peer 无效，已取消执行。") from exc
        peers[dialog.id] = peer
    return peers


async def delete_dialogs(client: TelegramClient, dialogs: list[DialogInfo]) -> None:
    """先检查整个删除批次，再按顺序执行；任一失败立即报错并停止后续删除。"""
    peers = prepare_dialog_peers(dialogs)
    for dialog in dialogs:
        await client.delete_dialog(peers[dialog.id])
        print(f"  x 已删除: {display_text(dialog.title)} (id={dialog.id})")
        await asyncio.sleep(0.5)


async def prepare_folder_plan(
    client: TelegramClient, folders: list[dict], dialogs: list[DialogInfo]
) -> FolderPlan:
    """只读预检并生成请求：复用普通分组 ID，保留共享分组并列出多余分组。"""
    validate_folders(folders, dialogs)
    peers = prepare_dialog_peers(dialogs)
    if not folders:
        return FolderPlan((), (), (), (), 0)
    existing = await client(functions.messages.GetDialogFiltersRequest())
    ordinary = [
        folder
        for folder in existing.filters
        if isinstance(folder, types.DialogFilter) and folder.id >= 2
    ]
    used = {
        folder.id
        for folder in existing.filters
        if isinstance(folder, (types.DialogFilter, types.DialogFilterChatlist))
    }
    shared_count = sum(
        isinstance(folder, types.DialogFilterChatlist) for folder in existing.filters
    )
    target_ids = [folder.id for folder in ordinary[: len(folders)]]
    candidate = 2
    while len(target_ids) < len(folders):
        if candidate not in used:
            target_ids.append(candidate)
            used.add(candidate)
        candidate += 1
    updates = []
    for folder, folder_id in zip(folders, target_ids, strict=True):
        prepared = types.DialogFilter(
            id=folder_id,
            title=types.TextWithEntities(text=folder["name"], entities=[]),
            pinned_peers=[],
            include_peers=[peers[dialog_id] for dialog_id in folder["dialog_ids"]],
            exclude_peers=[],
        )
        updates.append(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=prepared))
    removed = ordinary[len(folders) :]
    removals = tuple(
        functions.messages.UpdateDialogFilterRequest(id=folder.id) for folder in removed
    )
    for request in (*updates, *removals):
        bytes(request)
    return FolderPlan(
        snapshot=tuple(bytes(folder) for folder in existing.filters),
        updates=tuple(updates),
        removals=removals,
        removed_names=tuple(folder.title.text for folder in removed),
        shared_count=shared_count,
    )


async def apply_folders(client: TelegramClient, plan: FolderPlan) -> None:
    """确认快照未变化，先写入所有新分组，全部成功后才删除多余普通分组。"""
    if not (plan.updates or plan.removals):
        return
    current = await client(functions.messages.GetDialogFiltersRequest())
    if tuple(bytes(folder) for folder in current.filters) != plan.snapshot:
        raise RuntimeError("Telegram 分组在预览后发生变化，已停止执行；请重新生成计划。")
    for request in plan.updates:
        if await client(request) is not True:
            raise RuntimeError(f"Telegram 拒绝写入分组 {request.id}，已停止执行。")
        print(
            f"  + 已写入分组: {display_text(request.filter.title.text)} "
            f"({len(request.filter.include_peers)} 个对话)"
        )
        await asyncio.sleep(1)
    for request, name in zip(plan.removals, plan.removed_names, strict=True):
        if await client(request) is not True:
            raise RuntimeError(f"Telegram 拒绝移除分组 {request.id}，已停止执行。")
        print(f"  - 已移除多余分组: {display_text(name)} (id={request.id})")
        await asyncio.sleep(0.5)
