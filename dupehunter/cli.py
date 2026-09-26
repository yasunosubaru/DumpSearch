"""命令行入口：``python -m dupehunter``。

适合无 GUI 环境、脚本化、CI 校验；也方便在 GUI 出问题时仍能完成清理。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional, Sequence

from . import winpath
from .engine import Progress, ScanEngine
from .model import (
    KeepRule,
    Phase,
    ScanConfig,
    ScanResult,
    format_bytes,
    parse_size,
)
from .ops import actions, report, trash

_KEEP_LABELS = {
    KeepRule.NEWEST: "保留最新修改的",
    KeepRule.OLDEST: "保留最旧修改的",
    KeepRule.SHORTEST_PATH: "保留路径最短的",
    KeepRule.LONGEST_PATH: "保留路径最长的",
    KeepRule.SHALLOWEST: "保留层级最浅的",
    KeepRule.DEEPEST: "保留层级最深的",
    KeepRule.FIRST_SEEN: "保留首个发现的",
}


def _force_utf8_console() -> None:
    """让中文输出在 Windows 控制台正确显示。

    Windows 控制台默认用 GBK(936) 代码页，Python 的 stdout 会跟着它，
    于是中文输出全是乱码。这里做两件事：
      1. 把 stdout/stderr 切到 UTF-8，并设 errors='replace' 保证不抛异常；
      2. 尝试把控制台代码页改成 65001(UTF-8)——cmd.exe 侧才能真正显示。
    启动脚本里也应加 ``chcp 65001``（本函数对已启动的窗口无效）。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # 只改本进程附着的控制台，不影响其它程序
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def _size_arg(text: str) -> int:
    """argparse 用的严格版大小解析：无法解析直接报错。"""
    value = parse_size(text, -1)
    if value < 0:
        raise argparse.ArgumentTypeError(
            f"无法解析大小: {text!r}（示例: 10M / 1.5G / 500K）"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dupehunter",
        description="全盘重复文件查找与安全清理（默认移入回收站）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  dupehunter.py --list-drives\n"
            "  dupehunter.py E:\\ F:\\ --min-size 100M\n"
            "  dupehunter.py C:\\ --delete --yes\n"
            "  dupehunter.py . --csv out.csv --json out.json\n"
        ),
    )
    parser.add_argument("paths", nargs="*", help="要扫描的目录或盘符")
    parser.add_argument("--list-drives", action="store_true", help="列出可用盘符后退出")

    size_group = parser.add_argument_group("筛选")
    size_group.add_argument("--min-size", type=_size_arg, default=1,
                            help="忽略小于该体积的文件，如 100K / 10M（默认 1）")
    size_group.add_argument("--max-size", type=_size_arg, default=None,
                            help="忽略大于该体积的文件")
    size_group.add_argument("--include", default="", metavar="EXT",
                            help="只查这些扩展名，逗号分隔，如 .mp4,.mkv")
    size_group.add_argument("--exclude", default="", metavar="EXT",
                            help="排除这些扩展名，逗号分隔")
    size_group.add_argument("--exclude-dir", action="append", default=[],
                            metavar="NAME", help="排除同名目录，可重复")
    size_group.add_argument("--no-default-excludes", action="store_true",
                            help="不要自动排除 Windows/Program Files 等系统目录")
    size_group.add_argument("--include-empty", action="store_true",
                            help="把 0 字节文件也算作重复文件（危险）")
    size_group.add_argument("--follow-symlinks", action="store_true",
                            help="跟随 junction/符号链接（可能造成环路）")
    size_group.add_argument("--workers", type=int, default=0, help="哈希线程数，0=自动")

    act = parser.add_argument_group("操作")
    act.add_argument("--keep", choices=[r.value for r in KeepRule],
                     default=KeepRule.NEWEST.value, help="每组保留哪一个（默认 newest）")
    act.add_argument("--delete", action="store_true", help="把重复副本移入回收站")
    act.add_argument("--hard-delete", action="store_true",
                     help="永久删除（不可撤销，需再加 --yes）")
    act.add_argument("--yes", action="store_true", help="跳过交互确认")
    act.add_argument("--dry-run", action="store_true", help="只报告，不删（默认行为）")
    act.add_argument("--csv", metavar="PATH", help="导出 CSV 报告")
    act.add_argument("--json", metavar="PATH", help="导出 JSON 报告")
    act.add_argument("--open", metavar="PATH", help="扫描后用默认程序打开该文件")
    act.add_argument("--no-verify", action="store_true",
                     help="跳过逐字节校验阶段（更快，但少一道保险）")
    act.add_argument("-q", "--quiet", action="store_true", help="不打印进度")
    return parser


def list_drives_and_exit() -> int:
    drives = winpath.list_drives()
    if not drives:
        print("未发现可用盘符")
        return 1
    print(f"{'盘符':<6}{'类型':<12}{'容量':>10}{'可用':>10}  标签")
    print("-" * 64)
    for drive in drives:
        print(
            f"{drive.root:<6}{drive.type_name:<12}"
            f"{format_bytes(drive.total_bytes):>10}{format_bytes(drive.free_bytes):>10}"
            f"  {drive.label}"
        )
    return 0


def _config_from_args(args: argparse.Namespace) -> ScanConfig:
    roots = [winpath.normalize(p) for p in args.paths] or [os.getcwd()]
    excludes = set(args.exclude_dir)
    if not args.no_default_excludes:
        excludes |= set(winpath.DEFAULT_EXCLUDE_DIRS)

    def split_exts(text: str) -> set[str]:
        out = set()
        for part in (text or "").split(","):
            part = part.strip().lower()
            if not part:
                continue
            out.add(part if part.startswith(".") else "." + part)
        return out

    return ScanConfig(
        roots=roots,
        exclude_dirs=excludes,
        include_suffixes=split_exts(args.include),
        exclude_suffixes=split_exts(args.exclude),
        min_size=args.min_size,
        max_size=args.max_size,
        include_empty_files=args.include_empty,
        follow_symlinks=args.follow_symlinks,
        verify_bytes=not args.no_verify,
        workers=args.workers,
        keep_rule=KeepRule(args.keep),
    )


def _printer(quiet: bool):
    last = [0.0]

    def on_progress(p: Progress) -> None:
        if quiet:
            return
        now = time.monotonic()
        if p.done != p.total and now - last[0] < 0.25:
            return
        last[0] = now
        rate = format_bytes(p.speed_bps) + "/s" if p.speed_bps else "-"
        eta = f"ETA {int(p.eta_seconds // 60)}m{int(p.eta_seconds % 60)}s" if p.eta_seconds else ""
        sys.stderr.write(
            f"\r[{p.phase.value}] {p.done:,}/{p.total:,} "
            f"{rate} {eta} {p.detail[:40]:<40}"
        )
        sys.stderr.flush()

    return on_progress


def _print_report(result: ScanResult, rule: KeepRule, verbose: bool) -> None:
    sys.stderr.write("\r" + " " * 110 + "\r")
    print(result.summary())

    if not result.groups:
        return

    print()
    print("=" * 78)
    for idx, group in enumerate(result.groups[:200], 1):
        keeper, to_delete = group.split_by_keeper(rule)
        tag = "[已校验]" if group.verified else "[未校验]"
        print(
            f"#{idx}  {format_bytes(group.size):>10} x{group.count:<3} "
            f"可释放 {format_bytes(group.wasted_bytes):>10}  {tag}  "
            f"{group.digest.hex()[:16]}"
        )
        if verbose:
            print(f"    保留: {keeper.path}")
            for member in to_delete:
                print(f"    删除: {member.path}")
    if len(result.groups) > 200:
        print(f"... 另有 {len(result.groups) - 200} 组未显示（用 -v 或导出报告查看）")


def _confirm(result: ScanResult, rule: KeepRule, count: int, space: int) -> bool:
    print()
    print("!" * 78)
    print(f"即将把 {count:,} 个重复副本移入回收站，预计释放 {format_bytes(space)}。")
    print("这些文件会进入回收站，可以恢复；但请确认你已核对过上面的清单。")
    try:
        answer = input("确认继续？输入 yes 继续，其它任意键取消: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
        return False
    return answer.lower() == "yes"


def main(argv: Optional[Sequence[str]] = None) -> int:
    _force_utf8_console()
    args = build_parser().parse_args(argv)

    if args.list_drives:
        return list_drives_and_exit()

    if not args.paths:
        print("请指定至少一个目录或盘符（--help 查看用法）", file=sys.stderr)
        return 2

    config = _config_from_args(args)
    rule = config.keep_rule

    # 先干跑一次扫描，拿到清单再决定是否删除
    result = ScanEngine(config, on_progress=_printer(args.quiet)).run()

    if result.phase is Phase.ERROR:
        print(f"扫描出错: {result.error}", file=sys.stderr)
        return 1
    if result.cancelled:
        print("扫描已取消", file=sys.stderr)
        return 130

    _print_report(result, rule, verbose=args.verbose if hasattr(args, "verbose") else True)

    if args.csv:
        report.export_csv(result, args.csv, rule)
        print(f"\nCSV 报告: {args.csv}")
    if args.json:
        report.export_json(result, args.json)
        print(f"JSON 报告: {args.json}")

    if args.open:
        actions.open_file(args.open)

    if not (args.delete or args.hard_delete):
        if not args.csv and not args.json:
            print("\n提示：加 --delete 可将重复副本移入回收站（会先要求确认）。")
        return 0

    targets: list[str] = []
    for group in result.groups:
        _keeper, to_delete = group.split_by_keeper(rule)
        targets.extend(m.path for m in to_delete)

    if not targets:
        print("没有需要删除的副本。")
        return 0

    space = result.reclaimable_bytes
    if not args.yes and not args.dry_run:
        if not _confirm(result, rule, len(targets), space):
            print("已取消，未删除任何文件。")
            return 0

    if args.dry_run:
        print(f"\n[dry-run] 本应删除 {len(targets):,} 个文件，释放 {format_bytes(space)}。")
        return 0

    print(f"\n正在移入回收站 {len(targets):,} 个文件 ...")
    outcome = trash.trash_files(
        targets,
        progress=lambda d, t: sys.stderr.write(
            f"\r  {d:,}/{t:,} ({100*d//t}%)"
        ) if t else None,
        force_hard_delete=args.hard_delete,
    )
    sys.stderr.write("\r" + " " * 40 + "\r")
    print(outcome.summary())
    if outcome.failed:
        print(f"失败 {len(outcome.failed)} 项，前 10 条：", file=sys.stderr)
        for path, reason in outcome.failed[:10]:
            print(f"  {path}\n    -> {reason}", file=sys.stderr)
    return 0 if outcome.ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
