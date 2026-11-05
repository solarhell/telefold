"""分类计划必须完整、互斥，且不包含未知对话。"""

import pytest

from telefold.client import DialogInfo
from telefold.planner import build_folders, group_dialogs, validate_folders


def test_grouping_priority_and_fixed_folders():
    """清理与官方账号优先排除，敏感内容和陌生人进入固定分组，其余对话交给模型。"""
    dialogs = [
        DialogInfo(1, "已注销", "deleted"),
        DialogInfo(2, "可疑账号", "stranger", scam=True),
        DialogInfo(3, "仿冒账号", "user", fake=True),
        DialogInfo(777000, "Telegram", "user"),
        DialogInfo(4, "技术交流", "group", description="群规禁止 NSFW"),
        DialogInfo(5, "NSFW 内容", "stranger"),
        DialogInfo(6, "受限内容", "channel", restricted=True),
        DialogInfo(7, "OpenAI", "channel", restricted=True),
        DialogInfo(8, "新朋友", "stranger"),
    ]
    groups = group_dialogs(dialogs)
    assert [d.id for d in groups.to_delete] == [1, 2, 3]
    assert [d.id for d in groups.official] == [777000]
    assert [d.id for d in groups.regular] == [4, 7]
    assert [d.id for d in groups.nsfw] == [5, 6]
    assert [d.id for d in groups.strangers] == [8]
    folders = build_folders(groups, [{"name": "技术", "dialog_ids": [4, 7]}])
    assert folders == [
        {"name": "技术", "dialog_ids": [4, 7]},
        {"name": "NSFW", "dialog_ids": [5, 6]},
        {"name": "陌生人", "dialog_ids": [8]},
    ]


@pytest.mark.parametrize(
    "folders",
    [
        [],
        [{"name": "技术", "dialog_ids": [999]}],
        [{"name": "技术", "dialog_ids": [1, 1, 2]}],
        [{"name": "技术", "dialog_ids": [1]}],
        [{"name": "技术", "dialog_ids": [True, 2]}],
        [{"name": "技术", "dialog_ids": [1]}, {"name": " 技术 ", "dialog_ids": [2]}],
        [{"name": "  ", "dialog_ids": [1, 2]}],
        [{"name": "技术", "dialog_ids": []}],
    ],
)
def test_invalid_plan_is_rejected(folders):
    with pytest.raises(ValueError, match="已取消执行"):
        validate_folders(folders, [DialogInfo(1, "一", "group"), DialogInfo(2, "二", "group")])


def test_empty_input_preserves_existing_folders():
    assert build_folders(group_dialogs([]), []) == []


@pytest.mark.parametrize("name", ["NSFW", "nsfw", " NsFw ", "陌生人", " 陌生人 "])
def test_model_cannot_use_reserved_names_without_fixed_folders(name):
    """即使本次没有对应固定分组，模型也不能借用它的名称误标普通对话。"""
    groups = group_dialogs([DialogInfo(1, "技术群", "group")])
    with pytest.raises(ValueError, match="保留分组名称"):
        build_folders(groups, [{"name": name, "dialog_ids": [1]}])


@pytest.mark.parametrize("character", ["a", "中", "😀"])
def test_folder_title_boundary_counts_unicode_characters(character):
    """12 个 ASCII、中文或非 BMP 字符均可使用，13 个字符在写入前拒绝。"""
    dialogs = [DialogInfo(1, "技术群", "group")]
    validate_folders([{"name": character * 12, "dialog_ids": [1]}], dialogs)
    with pytest.raises(ValueError, match="12 字符限制"):
        validate_folders([{"name": character * 13, "dialog_ids": [1]}], dialogs)
