"""结果视图模型与删除计划——**纯逻辑，不含任何 Tk 代码**。

把这两块从 GUI 里剥出来，是为了让"确认先于删除""取消后零字节被删"
"勾选状态不随滚动丢失"这些安全性质可以变成普通单元测试断言，
而不用去 mock ``tkinter``。
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import Optional

from ..model import (
    DuplicateGroup,
    FileEntry,
    KeepRule,
    ScanResult,
    format_bytes,
)

# ---------------------------------------------------------------------------
# 行模型
# ---------------------------------------------------------------------------

ROW_GROUP = "group"
ROW_FILE = "file"


@dataclass(slots=True, frozen=True)
class RowSpec:
    """虚拟列表里的一行。

    ``key`` 是稳定的行标识：``"g:<组下标>"`` 或 ``"f:<组下标>:<成员下标>"``。
    勾选状态存在 :attr:`checked` 的 Python ``set`` 里，而不是 Treeview 的
    item state——后者只能看见**已物化**的行，一滚动就丢。
    """

    kind: str
    group_index: int
    file_index: int = -1
    size: int = 0
    label: str = ""
    sublabel: str = ""
    path: str = ""

    @property
    def key(self) -> str:
        if self.kind == ROW_GROUP:
            return f"g:{self.group_index}"
        return f"f:{self.group_index}:{self.file_index}"

    @property
    def is_group(self) -> bool:
        return self.kind == ROW_GROUP


class SortMode:
    """排序方式。"""

    RECLAIMABLE = "reclaimable"
    SIZE = "size"
    COUNT = "count"
    NAME = "name"
    PATH = "path"
    MTIME = "mtime"

    LABELS = {
        RECLAIMABLE: "可释放空间",
        SIZE: "单文件大小",
        COUNT: "副本数量",
        NAME: "文件名",
        PATH: "路径",
        MTIME: "修改时间",
    }


class ResultIndex:
    """持有 :class:`ScanResult`，提供过滤 / 排序 / 展平行视图。

    全部操作是纯函数式的内存计算，不碰 Tk，因此可以独立测试。
    """

    def __init__(self, result: ScanResult) -> None:
        self.result = result
        self.groups: list[DuplicateGroup] = list(result.groups)
        # _sorted 是"排序后的全集"，_view 是"排序后 + 过滤"的结果。
        # 必须分开存：若直接在 _view 上过滤，放宽条件（清空筛选、把最小
        # 体积调小）就无法把之前滤掉的组找回来。
        self._sorted: list[int] = list(range(len(self.groups)))
        self._view: list[int] = list(self._sorted)
        self._collapsed: set[int] = set()
        self._checked: set[str] = {f"g:{i}" for i in range(len(self.groups))}
        self.sort_mode: str = SortMode.RECLAIMABLE
        self.sort_reverse: bool = True
        self.filter_text: str = ""
        self.min_size: int = 0
        self.show_verified_only: bool = False
        self.resort()

    # -- 基本信息 --------------------------------------------------------
    def __len__(self) -> int:
        return len(self._view)

    @property
    def view(self) -> list[int]:
        """当前可见的组下标（已排序）。"""
        return self._view

    def group_at(self, index: int) -> DuplicateGroup:
        return self.groups[self._view[index]]

    # -- 排序 ------------------------------------------------------------
    def _key(self, index: int):
        g = self.groups[index]
        if self.sort_mode == SortMode.SIZE:
            return (g.size, g.count)
        if self.sort_mode == SortMode.COUNT:
            return (g.count, g.size)
        if self.sort_mode == SortMode.NAME:
            return (g.members[0].name.lower() if g.members else "", g.size)
        if self.sort_mode == SortMode.PATH:
            return (g.members[0].path.lower() if g.members else "", g.size)
        if self.sort_mode == SortMode.MTIME:
            return (max((m.mtime for m in g.members), default=0.0), g.size)
        return (g.wasted_bytes, g.size)

    def resort(self) -> None:
        self._sorted.sort(key=self._key, reverse=self.sort_reverse)
        self._refilter()

    def set_sort(self, mode: str, reverse: Optional[bool] = None) -> None:
        if mode not in SortMode.LABELS:
            raise ValueError(f"未知排序方式: {mode}")
        self.sort_mode = mode
        if reverse is not None:
            self.sort_reverse = reverse
        elif mode == SortMode.RECLAIMABLE:
            # "可释放空间"这个默认视角，用户想要的几乎总是"降序"
            self.sort_reverse = True
        self.resort()

    def toggle_sort_direction(self) -> None:
        self.sort_reverse = not self.sort_reverse
        self.resort()

    # -- 过滤 ------------------------------------------------------------
    def set_filter_text(self, text: str) -> None:
        self.filter_text = text or ""
        self._refilter()

    def set_min_size(self, size: int) -> None:
        self.min_size = max(0, size)
        self._refilter()

    def set_verified_only(self, value: bool) -> None:
        self.show_verified_only = bool(value)
        self._refilter()

    def _refilter(self) -> None:
        """**始终**从全集 _sorted 重新过滤，而不是在 _view 上再过一遍。"""
        needle = self.filter_text.strip().lower()
        if not needle and not self.min_size and not self.show_verified_only:
            self._view = list(self._sorted)
            return
        out: list[int] = []
        for idx in self._sorted:
            group = self.groups[idx]
            if self.min_size and group.size < self.min_size:
                continue
            if self.show_verified_only and not group.verified:
                continue
            if needle and not self._matches(group, needle):
                continue
            out.append(idx)
        self._view = out

    def _matches(self, group: DuplicateGroup, needle: str) -> bool:
        if needle.startswith("ext:"):
            ext = needle[4:].strip()
            if not ext.startswith("."):
                ext = "." + ext
            return any(m.suffix == ext for m in group.members)
        if needle.startswith("path:"):
            pat = needle[5:].strip().lower()
            return any(fnmatch.fnmatch(m.path.lower(), f"*{pat}*") for m in group.members)
        if needle.startswith("name:"):
            pat = needle[5:].strip().lower()
            return any(fnmatch.fnmatch(m.name.lower(), f"*{pat}*") for m in group.members)
        return any(needle in m.path.lower() for m in group.members)

    # -- 勾选状态（真源在 Python set 里） --------------------------------
    @property
    def checked_keys(self) -> set[str]:
        return self._checked

    def is_checked(self, key: str) -> bool:
        return key in self._checked

    def set_checked(self, key: str, value: bool) -> None:
        if value:
            self._checked.add(key)
        else:
            self._checked.discard(key)

    def check_all(self) -> None:
        self._checked = {f"g:{i}" for i in self._view}

    def check_none(self) -> None:
        self._checked.clear()

    def invert(self) -> None:
        current = set(self._checked)
        self._checked = {f"g:{i}" for i in self._view} - current

    def checked_group_count(self) -> int:
        return sum(1 for idx in self._view if f"g:{idx}" in self._checked)

    def checked_stats(self) -> tuple[int, int]:
        """返回 ``(组数, 文件数)``。"""
        groups = 0
        files = 0
        for idx in self._view:
            if f"g:{idx}" in self._checked:
                groups += 1
                files += len(self.groups[idx].members)
        return groups, files

    # -- 展开 / 折叠 -----------------------------------------------------
    def is_collapsed(self, index: int) -> bool:
        return index in self._collapsed

    def set_collapsed(self, index: int, value: bool) -> None:
        if value:
            self._collapsed.add(index)
        else:
            self._collapsed.discard(index)

    def toggle_collapsed(self, index: int) -> None:
        if index in self._collapsed:
            self._collapsed.discard(index)
        else:
            self._collapsed.add(index)

    def expand_all(self) -> None:
        self._collapsed.clear()

    def collapse_all(self) -> None:
        self._collapsed = set(self._view)

    def set_keep_rule(self, rule: KeepRule) -> None:
        self.result.config.keep_rule = rule

    # -- 行展平（虚拟列表用） -------------------------------------------
    def total_rows(self) -> int:
        """展平后总行数 = 组行 + （未折叠组的）成员行。"""
        total = 0
        for idx in self._view:
            total += 1
            if idx not in self._collapsed:
                total += len(self.groups[idx].members)
        return total

    def _prefix_counts(self) -> list[int]:
        """每个组行之前的行数前缀和，用于二分定位。"""
        counts: list[int] = []
        running = 0
        for idx in self._view:
            counts.append(running)
            running += 1
            if idx not in self._collapsed:
                running += len(self.groups[idx].members)
        return counts

    def locate(self, row: int) -> Optional[RowSpec]:
        """把展平行号映射成 :class:`RowSpec`。"""
        row = max(0, row)
        counts = self._prefix_counts()
        # 二分找最后一个 prefix <= row
        lo, hi = 0, len(counts) - 1
        pos = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if counts[mid] <= row:
                pos = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if pos < 0:
            return None
        group_index = self._view[pos]
        group = self.groups[group_index]
        offset = row - counts[pos]
        if offset == 0:
            return RowSpec(
                kind=ROW_GROUP,
                group_index=group_index,
                size=group.size,
                label=f"{group.count} 个相同文件",
                sublabel=format_bytes(group.size),
                path=group.members[0].path if group.members else "",
            )
        # 折叠的组不贡献成员行，offset > 0 落到这里说明行号已越界
        if group_index in self._collapsed:
            return None
        file_index = offset - 1
        if file_index >= len(group.members):
            return None
        member = group.members[file_index]
        return RowSpec(
            kind=ROW_FILE,
            group_index=group_index,
            file_index=file_index,
            size=member.size,
            label=member.name,
            sublabel=member.parent,
            path=member.path,
        )

    def group_rows(self) -> list[RowSpec]:
        """只返回组行（供导出/批量操作使用）。"""
        rows = []
        for pos, idx in enumerate(self._view):
            group = self.groups[idx]
            rows.append(
                RowSpec(
                    kind=ROW_GROUP,
                    group_index=idx,
                    size=group.size,
                    label=f"{group.count} 个相同文件",
                    sublabel=format_bytes(group.size),
                    path=group.members[0].path if group.members else "",
                )
            )
        return rows


# ---------------------------------------------------------------------------
# 删除计划
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DeletePlan:
    """一份**可审阅**的删除清单。构造它不碰文件系统。"""

    rule: KeepRule
    items: list[tuple[str, str, FileEntry]] = field(default_factory=list)
    # (digest, keeper_path, 待删文件)
    group_count: int = 0

    @property
    def targets(self) -> list[str]:
        return [entry.path for _d, _k, entry in self.items]

    @property
    def file_count(self) -> int:
        return len(self.items)

    @property
    def reclaimable(self) -> int:
        return sum(entry.size for _d, _k, entry in self.items)

    def summary(self) -> str:
        return (
            f"将把 {self.file_count:,} 个重复副本移入回收站，"
            f"涉及 {self.group_count:,} 个重复组，预计释放 "
            f"{format_bytes(self.reclaimable)}。"
        )

    def preview_lines(self, limit: int = 200) -> list[str]:
        """人类可读的预览清单。"""
        lines: list[str] = []
        for _digest, keeper, entry in self.items[:limit]:
            lines.append(f"删除  {format_bytes(entry.size):>10}  {entry.path}")
            lines.append(f"保留  {'':>10}  {keeper}")
        if self.file_count > limit:
            lines.append(f"... 另有 {self.file_count - limit:,} 项未显示")
        return lines

    def unique_targets(self) -> list[str]:
        return list(dict.fromkeys(self.targets))


def build_plan(
    index: ResultIndex, rule: Optional[KeepRule] = None
) -> DeletePlan:
    """按保留规则，从**已勾选**的组生成删除计划。"""
    rule = rule or index.result.config.keep_rule
    plan = DeletePlan(rule=rule)
    for idx in index.view:
        if not index.is_checked(f"g:{idx}"):
            continue
        group = index.groups[idx]
        if group.count < 2:
            continue
        keeper, to_delete = group.split_by_keeper(rule)
        if not to_delete:
            continue
        plan.group_count += 1
        for member in to_delete:
            plan.items.append((group.digest.hex()[:12], keeper.path, member))
    return plan


def audit_plan(plan: DeletePlan, index: ResultIndex) -> list[str]:
    """**删除前的静态审计**：返回可疑项的文字说明。

    这是"预览确认"里最有价值的一段——把用户最容易事后后悔的情况
    在动手之前就指出来。
    """
    warnings: list[str] = []
    rule = plan.rule
    all_paths: set[str] = set()
    duplicated_targets: set[str] = set()

    for _digest, keeper, entry in plan.items:
        if entry.path in all_paths:
            duplicated_targets.add(entry.path)
        all_paths.add(entry.path)
        # keeper 绝不能同时出现在待删列表里
        if entry.path.lower() == keeper.lower():
            warnings.append(f"内部错误：保留项与待删项相同 -> {entry.path}")

    if duplicated_targets:
        warnings.append(
            f"清单中有 {len(duplicated_targets)} 条重复路径，将被去重后再执行"
        )

    # 保留"最旧"意味着倾向于保留备份文件、删掉新下载的原件
    if rule is KeepRule.OLDEST:
        warnings.append("保留策略为「最旧」：会优先保留旧备份、删除较新的副本")

    # 保留"最新"常见于自动更新/同步产生的副本，可能删掉手工命名的原版
    if rule is KeepRule.NEWEST:
        warnings.append(
            "保留策略为「最新」：同步/更新工具产生的副本通常最新，"
            "请确认要删的正是它们"
        )

    # 跨盘删除会占用目标卷回收站
    drives: dict[str, int] = {}
    for _d, _k, entry in plan.items:
        drive = os.path.splitdrive(entry.path)[0].upper() or "?"
        drives[drive] = drives.get(drive, 0) + 1
    if len(drives) > 1:
        detail = "、".join(f"{d}({n})" for d, n in sorted(drives.items()))
        warnings.append(f"删除会跨 {len(drives)} 个盘：{detail}（各自进入该盘回收站）")

    # 极小文件通常是配置/占位文件，误删代价高于收益
    tiny = [e for _d, _k, e in plan.items if e.size < 1024]
    if tiny:
        total = sum(e.size for e in tiny)
        warnings.append(
            f"其中 {len(tiny):,} 个文件小于 1 KB（合计 {format_bytes(total)}），"
            "通常是配置/占位文件，删掉省不了空间却可能弄坏程序"
        )

    if plan.file_count == 0:
        warnings.append("没有选中任何待删除文件")
    return warnings


def dry_run_summary(plan: DeletePlan, rule: KeepRule) -> str:
    return plan.summary() + f"  保留策略：{keep_rule_label(rule)}"


def keep_rule_label(rule: KeepRule) -> str:
    return {
        KeepRule.NEWEST: "保留最新修改的",
        KeepRule.OLDEST: "保留最旧修改的",
        KeepRule.SHORTEST_PATH: "保留路径最短的",
        KeepRule.LONGEST_PATH: "保留路径最长的",
        KeepRule.SHALLOWEST: "保留层级最浅的",
        KeepRule.DEEPEST: "保留层级最深的",
        KeepRule.FIRST_SEEN: "保留首个发现的",
    }.get(rule, rule.value)
