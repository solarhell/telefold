"""生成并校验执行计划；此模块不执行 Telegram 请求。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telefold.client import DialogInfo


NSFW_KEYWORDS = [
    "反差",
    "少妇",
    "萝莉",
    "白丝",
    "内射",
    "口交",
    "吞精",
    "自慰",
    "无码",
    "蜜桃臀",
    "骚穴",
    "酥胸",
    "操骚",
    "调教",
    "母狗",
    "约炮",
    "福利姬",
    "裸舞",
    "色色",
    "吃瓜",
    "性欲",
    "erotic",
    "哺乳",
    "18+",
    "🔞",
    # escort / 外围
    "酒店預約",
    "酒店预约",
    "上门",
    "上門",
    "外围",
    "外圍",
    "楼凤",
    "樓鳳",
    "会所",
    "會所",
    "全套",
    "預約",
    # 色情内容
    "阅涩",
    "美女圖",
    "美女图",
    "睇圖",
    "睇图",
    "包养",
    "伴游",
    "防失联",
    "banana",
    # 行业暗语
    "修车俱乐部",
    "修车茶楼",
    "课表",
    "同城头条",
]

# 仅匹配 title（不匹配 description，避免群规提到 NSFW 导致误报）
NSFW_TITLE_KEYWORDS = [
    "nsfw",
    "av",
]

# restricted 但不是 NSFW 的白名单（按小写 title 匹配）
RESTRICTED_WHITELIST = [
    "openai",
]


@dataclass
class DialogGroups:
    to_delete: list[DialogInfo]
    official: list[DialogInfo]
    regular: list[DialogInfo]
    nsfw: list[DialogInfo]
    strangers: list[DialogInfo]

    @property
    def folder_dialogs(self) -> list[DialogInfo]:
        return self.regular + self.nsfw + self.strangers


def group_dialogs(dialogs: list[DialogInfo]) -> DialogGroups:
    groups = DialogGroups([], [], [], [], [])
    for dialog in dialogs:
        if dialog.dialog_type == "deleted" or dialog.scam or dialog.fake:
            groups.to_delete.append(dialog)
        elif dialog.id == 777000:
            groups.official.append(dialog)
        elif _is_nsfw(dialog):
            groups.nsfw.append(dialog)
        elif dialog.dialog_type == "stranger":
            groups.strangers.append(dialog)
        else:
            groups.regular.append(dialog)
    return groups


def _is_nsfw(dialog: DialogInfo) -> bool:
    title = dialog.title.lower()
    text = (dialog.title + " " + (dialog.description or "")).lower()
    return (
        (dialog.restricted and title not in RESTRICTED_WHITELIST)
        or any(keyword in title for keyword in NSFW_TITLE_KEYWORDS)
        or any(keyword in text for keyword in NSFW_KEYWORDS)
    )


def build_folders(groups: DialogGroups, classified: list[dict]) -> list[dict]:
    folders = list(classified)
    if groups.nsfw:
        folders.append({"name": "NSFW", "dialog_ids": [d.id for d in groups.nsfw]})
    if groups.strangers:
        folders.append({"name": "陌生人", "dialog_ids": [d.id for d in groups.strangers]})
    validate_folders(folders, groups.folder_dialogs)
    return folders


def validate_folders(folders: list[dict], dialogs: list[DialogInfo]) -> None:
    """在任何写入之前拒绝未知、重复、遗漏的对话和无效分组。"""
    expected = {dialog.id for dialog in dialogs}
    seen: set[int] = set()
    names: set[str] = set()
    for folder in folders:
        name = folder.get("name")
        ids = folder.get("dialog_ids")
        if not isinstance(name, str) or not name.strip() or not isinstance(ids, list) or not ids:
            raise ValueError("分类结果包含空名称或空分组，已取消执行。")
        normalized = name.strip().casefold()
        if normalized in names:
            raise ValueError(f"分类结果包含重复分组名称：{name}，已取消执行。")
        names.add(normalized)
        for dialog_id in ids:
            if type(dialog_id) is not int or dialog_id not in expected:
                raise ValueError(f"分类结果包含未知对话：{dialog_id}，已取消执行。")
            if dialog_id in seen:
                raise ValueError(f"分类结果包含重复对话：{dialog_id}，已取消执行。")
            seen.add(dialog_id)
    if seen != expected:
        raise ValueError("分类结果遗漏了待处理对话，已取消执行。")
