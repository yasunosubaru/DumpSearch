"""扫描结果导出：CSV（Excel 友好）与 JSON（可再分析）。

CSV 带 UTF-8 BOM，否则 Excel 打开中文列名会乱码。
"""

from __future__ import annotations

import csv
import json
import time
from typing import Optional, Sequence

from ..model import DuplicateGroup, KeepRule, ScanResult, format_bytes


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def export_csv(
    result: ScanResult,
    path: str,
    keep_rule: Optional[KeepRule] = None,
    only_reclaimable: bool = True,
) -> str:
    """导出重复组。

    :param only_reclaimable: 只列出"会被删除的那些副本"（即每组除保留项外）
    """
    rule = keep_rule or result.config.keep_rule
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "组号",
                "摘要",
                "单文件大小(字节)",
                "单文件大小",
                "成员数",
                "保留文件",
                "保留原因",
                "路径",
                "所在目录",
                "修改时间",
                "是否待删除",
            ]
        )
        for idx, group in enumerate(result.groups, 1):
            keeper, to_delete = group.split_by_keeper(rule)
            for member in group.members:
                if only_reclaimable and member.path == keeper.path:
                    continue
                writer.writerow(
                    [
                        idx,
                        group.digest.hex(),
                        member.size,
                        format_bytes(member.size),
                        group.count,
                        keeper.name,
                        _rule_label(rule),
                        member.path,
                        member.parent,
                        _iso(member.mtime),
                        "是" if member.path != keeper.path else "否",
                    ]
                )
    return path


def export_json(result: ScanResult, path: str) -> str:
    payload = {
        "generated_at": _iso(time.time()),
        "phase": result.phase.value,
        "config": {
            "roots": result.config.roots,
            "exclude_dirs": sorted(result.config.exclude_dirs),
            "min_size": result.config.min_size,
            "max_size": result.config.max_size,
            "include_suffixes": sorted(result.config.include_suffixes),
            "exclude_suffixes": sorted(result.config.exclude_suffixes),
            "keep_rule": result.config.keep_rule.value,
        },
        "summary": {
            "group_count": result.group_count,
            "total_files": result.total_files,
            "reclaimable_bytes": result.reclaimable_bytes,
            "reclaimable_human": format_bytes(result.reclaimable_bytes),
        },
        "groups": [
            {
                "digest": group.digest.hex(),
                "size": group.size,
                "count": group.count,
                "verified": group.verified,
                "files": [
                    {
                        "path": m.path,
                        "size": m.size,
                        "mtime": _iso(m.mtime),
                    }
                    for m in group.members
                ],
            }
            for group in result.groups
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def _rule_label(rule: KeepRule) -> str:
    return {
        KeepRule.NEWEST: "保留最新修改",
        KeepRule.OLDEST: "保留最旧修改",
        KeepRule.SHORTEST_PATH: "保留路径最短",
        KeepRule.LONGEST_PATH: "保留路径最长",
        KeepRule.SHALLOWEST: "保留层级最浅",
        KeepRule.DEEPEST: "保留层级最深",
        KeepRule.FIRST_SEEN: "保留首个发现",
    }.get(rule, rule.value)


def build_deletion_script(
    groups: Sequence[DuplicateGroup], keep_rule: KeepRule
) -> str:
    """生成一份可审阅的 PowerShell 删除脚本（不执行，只供人工确认）。"""
    lines = [
        "# 由 Dump Search 生成 —— 请人工审阅后再执行",
        "# 动作：把重复副本移入回收站（可撤销）",
        f"# 时间：{_iso(time.time())}",
        "",
    ]
    total = 0
    for idx, group in enumerate(groups, 1):
        _keeper, to_delete = group.split_by_keeper(keep_rule)
        if not to_delete:
            continue
        lines.append(f"# --- 组 {idx} | {format_bytes(group.size)} x {group.count} ---")
        for member in to_delete:
            lines.append(f"Add-Type -AssemblyName Microsoft.VisualBasic; "
                         f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                         f"'{member.path}', 'OnlyErrorDialogs', 'SendToRecycleBin')")
            total += 1
    lines.append("")
    lines.append(f"# 合计 {total} 个文件")
    return "\n".join(lines)
