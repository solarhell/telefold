"""生成并校验执行计划；此模块不执行 Telegram 请求。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from telefold.display import display_text

if TYPE_CHECKING:
    from telefold.client import DialogInfo


FOLDER_TITLE_MAX_LENGTH = 12
MAX_CLASSIFIED_FOLDERS = 8
RESERVED_FOLDER_NAMES = {"nsfw", "陌生人"}

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
    """按处理方式互斥分组，删除和官方对话不进入模型分类。"""

    to_delete: list[DialogInfo]
    official: list[DialogInfo]
    regular: list[DialogInfo]
    nsfw: list[DialogInfo]
    strangers: list[DialogInfo]

    @property
    def folder_dialogs(self) -> list[DialogInfo]:
        """返回最终分组中应恰好出现一次的全部对话。"""
        return self.regular + self.nsfw + self.strangers


def group_dialogs(dialogs: list[DialogInfo]) -> DialogGroups:
    """依次处理清理、官方账号、敏感内容和陌生人，其余对话交给模型。"""
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
    """结合受限标记和关键词判断敏感内容，保留既有标题匹配和白名单规则。"""
    title = dialog.title.lower()
    text = (dialog.title + " " + (dialog.description or "")).lower()
    return (
        (dialog.restricted and title not in RESTRICTED_WHITELIST)
        or any(keyword in title for keyword in NSFW_TITLE_KEYWORDS)
        or any(keyword in text for keyword in NSFW_KEYWORDS)
    )


def classification_limits(dialogs: list[DialogInfo]) -> tuple[int, int]:
    """按待分类对话数计算可行范围；空输入不需要模型分组。"""
    count = len(dialogs)
    if count == 0:
        return 0, 0
    return (5 if count >= 5 else 1), min(MAX_CLASSIFIED_FOLDERS, count)


def validate_classification(folders: list[dict], dialogs: list[DialogInfo]) -> None:
    """统一校验模型分组数量、保留名称及完整且互斥的对话覆盖。"""
    minimum, maximum = classification_limits(dialogs)
    if not minimum <= len(folders) <= maximum:
        raise ValueError(
            f"分类结果必须包含 {minimum}-{maximum} 个分组，当前返回 {len(folders)} 个，已取消执行。"
        )
    for folder in folders:
        name = folder.get("name")
        if isinstance(name, str) and name.strip().casefold() in RESERVED_FOLDER_NAMES:
            raise ValueError(f"分类结果使用了保留分组名称：{display_text(name)}，已取消执行。")
    validate_folders(folders, dialogs)


def build_folders(groups: DialogGroups, classified: list[dict]) -> list[dict]:
    """校验模型分类，再加入固定分组并检查完整执行计划。"""
    validate_classification(classified, groups.regular)
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
        if len(name) > FOLDER_TITLE_MAX_LENGTH:
            raise ValueError(
                f"分组名称超过 Telegram 的 {FOLDER_TITLE_MAX_LENGTH} 字符限制："
                f"{display_text(name)}，已取消执行。"
            )
        normalized = name.strip().casefold()
        if normalized in names:
            raise ValueError(f"分类结果包含重复分组名称：{display_text(name)}，已取消执行。")
        names.add(normalized)
        for dialog_id in ids:
            if type(dialog_id) is not int or dialog_id not in expected:
                raise ValueError(f"分类结果包含未知对话：{display_text(dialog_id)}，已取消执行。")
            if dialog_id in seen:
                raise ValueError(f"分类结果包含重复对话：{dialog_id}，已取消执行。")
            seen.add(dialog_id)
    if seen != expected:
        raise ValueError("分类结果遗漏了待处理对话，已取消执行。")
