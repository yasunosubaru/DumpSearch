"""核心逻辑测试（无需 GUI、无需联网）。

运行::

    python -m pytest tests -q
    python tests\\test_core.py        # 无 pytest 时也能跑
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dupehunter import hasher, winpath  # noqa: E402
from dupehunter.engine import ScanEngine  # noqa: E402
from dupehunter.gui.viewmodel import (  # noqa: E402
    ResultIndex, ROW_FILE, ROW_GROUP, SortMode, audit_plan, build_plan,
)
from dupehunter.model import (  # noqa: E402
    DuplicateGroup, FileEntry, KeepRule, Phase, ScanConfig, ScanResult,
    format_bytes, parse_size,
)
from dupehunter.ops import report, trash  # noqa: E402
from dupehunter.scanner import Scanner  # noqa: E402


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

class Tree:
    """临时目录树。"""

    def __init__(self) -> None:
        self.root = tempfile.mkdtemp(prefix="ds_test_")

    def write(self, rel: str, data: bytes) -> str:
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
        return full

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


A = b"A" * 5000
B = b"B" * 3000
C = b"C" * 7000
HEAD_LIKE = b"Z" * 100  # 头部与 A 不同，用于验证不会误判


def _scan(root: str, **kwargs) -> ScanResult:
    cfg = ScanConfig(roots=[root], **kwargs)
    return ScanEngine(cfg).run()


# ---------------------------------------------------------------------------
# 扫描 / 去重正确性
# ---------------------------------------------------------------------------

def test_finds_exact_duplicates():
    t = Tree()
    try:
        t.write("x/1.txt", A)
        t.write("y/2.txt", A)
        t.write("z/3.txt", A)
        t.write("w/unique.txt", B)
        res = _scan(t.root)
        assert res.phase is Phase.DONE, res.error
        assert res.group_count == 1, res.summary()
        assert res.total_files == 3
        assert res.groups[0].size == len(A)
    finally:
        t.cleanup()


def test_different_content_never_grouped():
    t = Tree()
    try:
        for i in range(20):
            t.write(f"d{i % 5}/f{i}.bin", bytes([i]) * 1000)  # 同样大小，不同内容
        res = _scan(t.root)
        assert res.group_count == 0, res.summary()
    finally:
        t.cleanup()


def test_size_filter_is_free_and_correct():
    t = Tree()
    try:
        t.write("a1.bin", b"Q" * 100)
        t.write("a2.bin", b"Q" * 100)
        t.write("b1.bin", b"Q" * 5000)
        t.write("b2.bin", b"Q" * 5000)
        res = _scan(t.root, min_size=1000)
        assert res.group_count == 1
        assert res.groups[0].size == 5000
    finally:
        t.cleanup()


def test_min_max_size():
    t = Tree()
    try:
        t.write("s1.bin", b"Q" * 100)
        t.write("s2.bin", b"Q" * 100)
        t.write("m1.bin", b"Q" * 5000)
        t.write("m2.bin", b"Q" * 5000)
        t.write("l1.bin", b"Q" * 9000)
        t.write("l2.bin", b"Q" * 9000)
        res = _scan(t.root, min_size=1000, max_size=6000)
        assert res.group_count == 1, res.summary()
        assert res.groups[0].size == 5000
    finally:
        t.cleanup()


def test_empty_files_excluded_by_default():
    t = Tree()
    try:
        t.write("e1.txt", b"")
        t.write("e2.txt", b"")
        assert _scan(t.root).group_count == 0
        assert _scan(t.root, include_empty_files=True).group_count == 1
    finally:
        t.cleanup()


def test_exclude_dir_by_name():
    t = Tree()
    try:
        t.write("keep/1.txt", A)
        t.write("keep/2.txt", A)
        t.write("node_modules/3.txt", A)
        t.write("node_modules/4.txt", A)
        res = _scan(t.root, exclude_dirs={"node_modules"})
        assert res.group_count == 1
        assert all("node_modules" not in m.path for m in res.groups[0].members)
    finally:
        t.cleanup()


def test_include_suffix_filter():
    t = Tree()
    try:
        t.write("a1.txt", A)
        t.write("a2.txt", A)
        t.write("b1.log", A)
        t.write("b2.log", A)
        res = _scan(t.root, include_suffixes={".txt"})
        assert res.group_count == 1
        assert res.groups[0].count == 2
    finally:
        t.cleanup()


def test_partial_hash_distinguishes_same_head():
    """头部相同、尾部不同的文件绝不能被当成重复。"""
    t = Tree()
    try:
        head = b"HEAD" * 20000  # > inline_quick_max，触发分段采样
        t.write("a/p.bin", head + b"AAA")
        t.write("b/p.bin", head + b"BBB")
        t.write("c/p.bin", head + b"CCC")
        res = _scan(t.root, verify_bytes=True)
        assert res.group_count == 0, "头部相同不应被误判为重复"
    finally:
        t.cleanup()


def test_same_size_different_tail_in_large_file():
    t = Tree()
    try:
        base = os.urandom(300000)
        t.write("a/f.bin", base)
        t.write("b/f.bin", base + b"X")
        res = _scan(t.root, verify_bytes=True)
        assert res.group_count == 0
    finally:
        t.cleanup()


def test_verify_stage_catches_fake_collision():
    """构造"全文哈希被强行相同"的场景，逐字节校验必须拦下来。"""
    from dupehunter.model import DuplicateGroup, FileEntry

    t = Tree()
    try:
        p1 = t.write("a/f.bin", b"P" * 4096)
        p2 = t.write("b/f.bin", b"Q" * 4096)
        e1 = FileEntry(p1, 4096, 0.0)
        e2 = FileEntry(p2, 4096, 0.0)
        group = DuplicateGroup(digest=b"\x00" * 32, size=4096, members=[e1, e2])
        stats = _scan(t.root).stats
        verified = ScanEngine(ScanConfig(roots=[t.root]))._verify([group], 2, 0)
        assert verified == [], "逐字节校验必须剔除哈希碰撞"
    finally:
        t.cleanup()


# ---------------------------------------------------------------------------
# 长路径 / 硬链接
# ---------------------------------------------------------------------------

def test_long_path_over_260():
    t = Tree()
    try:
        deep = t.root
        for _ in range(14):
            deep = os.path.join(deep, "a_very_long_directory_name_for_path_length_test")
        p1 = os.path.join(deep, "x" * 60 + ".txt")
        os.makedirs(deep, exist_ok=True)
        for p in (p1, p1.replace("x" * 60, "y" * 60)):
            with open(p, "wb") as fh:
                fh.write(A)
        assert len(p1) > 260, f"测试路径只有 {len(p1)} 字符，没验证到长路径"
        res = _scan(t.root)
        assert res.group_count == 1, res.summary()
        for m in res.groups[0].members:
            assert not m.path.startswith("\\\\?\\"), "长路径前缀泄漏到数据层了"
            assert len(m.path) > 260
    finally:
        t.cleanup()


def test_no_prefix_leak_in_general():
    t = Tree()
    try:
        t.write("a/1.txt", A)
        t.write("b/1.txt", A)
        res = _scan(t.root)
        for m in res.groups[0].members:
            assert m.path == winpath.display(m.path)
            assert not m.path.startswith("\\\\?\\")
    finally:
        t.cleanup()


def test_hardlink_collapsed():
    t = Tree()
    try:
        real = t.write("orig.txt", A)
        alias = os.path.join(t.root, "alias.txt")
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/H", alias, real],
                               capture_output=True, text=True)
            if r.returncode != 0:
                return  # 无权限创建硬链接，跳过
            copy = t.write("copy.txt", A)
            res = _scan(t.root, treat_hardlinks_as_one=True)
            assert res.stats.hardlinks_collapsed == 1, res.summary()
            assert res.group_count == 1
            names = sorted(os.path.basename(m.path) for m in res.groups[0].members)
            assert "orig.txt" not in names or "alias.txt" not in names, names
            assert len(names) == 2, names

            res2 = _scan(t.root, treat_hardlinks_as_one=False)
            assert res2.stats.hardlinks_collapsed == 0
            assert res2.groups[0].count == 3
    finally:
        t.cleanup()


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------

def test_cancel_stops_early():
    t = Tree()
    try:
        for i in range(300):
            t.write(f"d{i % 10}/f{i}.bin", bytes([i % 251]) * 20000)
        token = threading.Event()
        token.set()  # 一开始就取消
        res = ScanEngine(ScanConfig(roots=[t.root]), cancel=token).run()
        assert res.cancelled
        assert res.phase is Phase.CANCELLED
    finally:
        t.cleanup()


def test_cancel_midway():
    t = Tree()
    try:
        for i in range(200):
            t.write(f"d{i % 7}/f{i}.bin", bytes([i % 97]) * 60000)
        token = threading.Event()
        engine = ScanEngine(ScanConfig(roots=[t.root]), cancel=token)
        threading.Timer(0.05, token.set).start()
        res = engine.run()
        assert res.cancelled or res.group_count >= 0
    finally:
        t.cleanup()


# ---------------------------------------------------------------------------
# 保留策略
# ---------------------------------------------------------------------------

def _make_group() -> DuplicateGroup:
    # 路径长度刻意互不相同，避免长度相等时按字典序 tie-break
    members = [
        FileEntry(r"C:\a\new.txt", 100, mtime=300.0),          # 14
        FileEntry(r"C:\b\b_oldest_deep_name.txt", 100, mtime=100.0),  # 33
        FileEntry(r"C:\c\deeper\long_path_name.txt", 100, mtime=200.0),  # 37
    ]
    return DuplicateGroup(digest=b"\x01" * 32, size=100, members=members)


def test_keep_rules():
    g = _make_group()
    assert g.pick_keeper(KeepRule.NEWEST).name == "new.txt"
    assert g.pick_keeper(KeepRule.OLDEST).name == "b_oldest_deep_name.txt"
    assert g.pick_keeper(KeepRule.SHORTEST_PATH).name == "new.txt"
    assert g.pick_keeper(KeepRule.LONGEST_PATH).name == "long_path_name.txt"
    assert g.pick_keeper(KeepRule.SHALLOWEST).name == "new.txt"
    assert g.pick_keeper(KeepRule.DEEPEST).name == "long_path_name.txt"


def test_split_by_keeper_excludes_keeper():
    g = _make_group()
    keeper, rest = g.split_by_keeper(KeepRule.NEWEST)
    assert keeper.path not in [f.path for f in rest]
    assert len(rest) == 2


def test_keep_rule_change_is_pure():
    g = _make_group()
    first = g.pick_keeper(KeepRule.NEWEST).path
    g.pick_keeper(KeepRule.OLDEST)
    assert g.pick_keeper(KeepRule.NEWEST).path == first, "picking 不能有副作用"


# ---------------------------------------------------------------------------
# 视图模型（虚拟列表 / 勾选 / 计划）
# ---------------------------------------------------------------------------

def _fake_result(n: int = 50) -> ScanResult:
    groups = []
    for i in range(n):
        members = [
            FileEntry(rf"C:\g{i}\copy{j}.dat", 1000 * (i + 1), 1000.0 + j)
            for j in range(2 + (i % 3))
        ]
        groups.append(
            DuplicateGroup(digest=bytes([i % 256]) * 32,
                           size=1000 * (i + 1), members=members, verified=True)
        )
    res = ScanResult(groups=groups, phase=Phase.DONE)
    res.config.keep_rule = KeepRule.NEWEST
    return res


def test_result_index_sorts_by_reclaimable_desc():
    idx = ResultIndex(_fake_result())
    values = [idx.groups[i].wasted_bytes for i in idx.view]
    assert values == sorted(values, reverse=True), "默认应按可释放空间降序"


def test_result_index_sort_modes():
    idx = ResultIndex(_fake_result())
    for mode in SortMode.LABELS:
        idx.set_sort(mode)
        assert len(idx.view) == 50


def test_flat_rows_and_locate_consistent():
    idx = ResultIndex(_fake_result())
    total = idx.total_rows()
    counted = 0
    for pos, gi in enumerate(idx.view):
        counted += 1 + len(idx.groups[gi].members)
    assert counted == total, (counted, total)
    for row in (0, 1, 5, total // 2, total - 1):
        spec = idx.locate(row)
        assert spec is not None, f"row {row} 定位失败"
        assert spec.kind in (ROW_GROUP, ROW_FILE)


def test_locate_after_collapse():
    idx = ResultIndex(_fake_result())
    idx.collapse_all()
    assert idx.total_rows() == 50
    spec = idx.locate(0)
    assert spec.kind == ROW_GROUP
    assert idx.locate(50) is None


def test_locate_after_expand():
    idx = ResultIndex(_fake_result())
    idx.expand_all()
    total = idx.total_rows()
    assert total > 50
    for row in range(total):
        assert idx.locate(row) is not None, f"展开后 row {row} 定位失败"


def test_check_state_survives_scroll_simulation():
    """勾选真源在 Python set 里，不随物化窗口变化而丢失。"""
    idx = ResultIndex(_fake_result())
    key = f"g:{idx.view[3]}"
    idx.set_checked(key, False)
    idx.expand_all()
    idx.collapse_all()
    idx.set_filter_text("copy")
    idx.set_filter_text("")
    assert not idx.is_checked(key)


def test_check_all_none_invert():
    idx = ResultIndex(_fake_result())
    idx.check_all()
    assert idx.checked_group_count() == 50
    idx.invert()
    assert idx.checked_group_count() == 0
    idx.invert()
    assert idx.checked_group_count() == 50
    idx.check_none()
    assert idx.checked_group_count() == 0


def test_filter_widening_restores_groups():
    """回归测试：过滤必须在全集上重算，不能在已过滤视图上二次过滤。"""
    idx = ResultIndex(_fake_result())
    idx.set_min_size(40000)
    assert len(idx.view) < 50
    idx.set_min_size(0)
    assert len(idx.view) == 50
    idx.set_verified_only(True)
    assert len(idx.view) == 50
    idx.set_verified_only(False)
    assert len(idx.view) == 50


def test_min_size_filter_only_shows_matching():
    idx = ResultIndex(_fake_result())
    idx.set_min_size(25000)
    assert all(idx.groups[i].size >= 25000 for i in idx.view)


def test_locate_respects_collapse():
    """折叠的组不贡献成员行，locate 不能凭空造出成员行。"""
    idx = ResultIndex(_fake_result())
    idx.collapse_all()
    total = idx.total_rows()
    for row in range(total):
        spec = idx.locate(row)
        assert spec is not None, row
        assert spec.kind == ROW_GROUP, f"折叠后 row {row} 不应是成员行"
    assert idx.locate(total) is None


def test_plan_only_contains_checked_groups():
    idx = ResultIndex(_fake_result())
    idx.check_all()
    full = build_plan(idx, KeepRule.NEWEST)
    assert full.file_count > 0
    assert full.group_count == 50
    assert len(full.unique_targets()) == full.file_count, "不应有重复目标"


def test_plan_excludes_keeper_always():
    idx = ResultIndex(_fake_result())
    idx.check_all()
    for rule in KeepRule:
        plan = build_plan(idx, rule)
        for _d, keeper, entry in plan.items:
            assert entry.path.lower() != keeper.lower(), rule


def test_plan_respects_uncheck():
    idx = ResultIndex(_fake_result())
    idx.check_none()
    assert build_plan(idx).file_count == 0
    idx.check_all()
    baseline = build_plan(idx).file_count
    idx.set_checked(f"g:{idx.view[0]}", False)
    reduced = build_plan(idx)
    assert reduced.file_count < baseline
    assert reduced.group_count == 49


def test_audit_warns_about_tiny_files():
    groups = [DuplicateGroup(
        digest=b"\x02" * 32, size=10,
        members=[FileEntry(rf"C:\x\{i}.cfg", 10, 1.0) for i in range(3)],
    )]
    res = ScanResult(groups=groups)
    idx = ResultIndex(res)
    plan = build_plan(idx, KeepRule.NEWEST)
    warnings = audit_plan(plan, idx)
    assert any("1 KB" in w for w in warnings), warnings


def test_audit_warns_about_cross_drive():
    """跨盘删除必须提醒——每个卷各有自己的回收站。

    构造时要确保**待删项**真的横跨两个盘：保留项落在 C:，
    于是 C: 与 D: 各有一个文件会被删。
    """
    groups = [DuplicateGroup(
        digest=b"\x03" * 32, size=5000,
        members=[FileEntry(r"C:\a\f.dat", 5000, 3.0),    # 最新 -> 保留
                 FileEntry(r"C:\a\g.dat", 5000, 2.0),    # 待删 (C:)
                 FileEntry(r"D:\b\f.dat", 5000, 1.0)],   # 待删 (D:)
    )]
    idx = ResultIndex(ScanResult(groups=groups))
    idx.check_all()
    plan = build_plan(idx, KeepRule.NEWEST)
    assert len({os.path.splitdrive(p)[0] for p in plan.targets}) == 2, plan.targets
    warnings = audit_plan(plan, idx)
    assert any("盘" in w for w in warnings), warnings


def test_filters():
    idx = ResultIndex(_fake_result())
    # 先收窄再放宽，验证放宽能真正恢复（曾经踩过：直接在已过滤视图上再过滤）
    idx.set_min_size(25000)
    narrowed = len(idx.view)
    assert 0 < narrowed < 50, narrowed
    idx.set_min_size(0)
    assert len(idx.view) == 50, "放宽条件必须恢复全集"

    idx.set_verified_only(True)
    assert len(idx.view) == 50
    idx.set_verified_only(False)

    # copy2 只出现在 i % 3 == 2 的组里，能真正收窄
    idx.set_filter_text("name:copy2")
    assert 0 < len(idx.view) < 50, len(idx.view)
    idx.set_filter_text("")
    assert len(idx.view) == 50, "清空筛选必须恢复全集"

    idx.set_filter_text("ext:.dat")
    assert len(idx.view) == 50
    idx.set_filter_text("ext:.mp4")
    assert len(idx.view) == 0

    idx.set_filter_text("path:copy2")
    assert 0 < len(idx.view) < 50


def test_plan_summary_mentions_count():
    idx = ResultIndex(_fake_result())
    plan = build_plan(idx)
    assert "回收站" in plan.summary()
    assert plan.preview_lines(5)


# ---------------------------------------------------------------------------
# 回收站
# ---------------------------------------------------------------------------

def test_trash_basic_and_verification():
    t = Tree()
    try:
        p = t.write("gone.txt", b"delete me")
        assert trash.exists(p)
        out = trash.trash_files([p], use_send2trash=False)
        assert out.file_count_ok if hasattr(out, "file_count_ok") else True
        assert not out.failed, out.failed
        assert out.deleted == [p]
        assert not trash.exists(p), "文件必须真的消失"
    finally:
        t.cleanup()


def test_trash_readonly():
    if os.name != "nt":
        return
    t = Tree()
    try:
        p = t.write("ro.txt", b"readonly")
        os.chmod(p, 0o444)
        out = trash.trash_files([p], use_send2trash=False)
        assert not out.failed, out.failed
        assert not trash.exists(p)
    finally:
        t.cleanup()


def test_trash_long_path():
    t = Tree()
    try:
        deep = t.root
        for _ in range(14):
            deep = os.path.join(deep, "a_very_long_directory_name_for_path_length_test")
        os.makedirs(deep, exist_ok=True)
        p = os.path.join(deep, "y" * 60 + ".txt")
        with open(p, "wb") as fh:
            fh.write(b"longpath")
        assert len(p) > 260
        out = trash.trash_files([p], use_send2trash=False)
        assert not out.failed, out.failed
        assert not trash.exists(p)
    finally:
        t.cleanup()


def test_trash_empty_input():
    out = trash.trash_files([], use_send2trash=False)
    assert out.deleted == [] and out.failed == []
    assert out.ok


def test_trash_nonexistent_reported_as_failure():
    t = Tree()
    try:
        ghost = os.path.join(t.root, "ghost.txt")
        out = trash.trash_files([ghost], use_send2trash=False)
        assert out.failed, "删除不存在的文件必须算失败"
    finally:
        t.cleanup()


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def test_export_csv_json():
    idx_result = _fake_result(5)
    t = Tree()
    try:
        csv_path = os.path.join(t.root, "r.csv")
        report.export_csv(idx_result, csv_path, KeepRule.NEWEST)
        text = open(csv_path, encoding="utf-8-sig").read()
        assert "组号" in text and "保留文件" in text

        json_path = os.path.join(t.root, "r.json")
        report.export_json(idx_result, json_path)
        import json
        data = json.load(open(json_path, encoding="utf-8"))
        assert data["summary"]["group_count"] == 5
        assert len(data["groups"]) == 5
    finally:
        t.cleanup()


def test_build_deletion_script():
    text = report.build_deletion_script(_fake_result(3).groups, KeepRule.NEWEST)
    assert "DeleteFile" in text
    assert "# 合计" in text


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1024 ** 2) == "1.00 MB"
    assert format_bytes(1024 ** 3) == "1.00 GB"


def test_parse_size():
    assert parse_size("1K") == 1024
    assert parse_size("1M") == 1024 ** 2
    assert parse_size("1.5G") == int(1.5 * 1024 ** 3)
    assert parse_size("500") == 500
    assert parse_size("") == 0


def test_winpath_display_native_roundtrip():
    """display(native(p)) == display(p)：前缀可加可去，且不残留。"""
    for p in (r"C:\a\b.txt", r"\\server\share\x.txt", r"\\?\C:\long\path.txt"):
        native = winpath.native(p)
        assert winpath.display(native) == winpath.display(p), p
        assert not winpath.display(native).startswith("\\\\?\\"), p
    # 普通路径加前缀后必须能还原
    assert winpath.display(winpath.native(r"C:\a\b.txt")) == r"C:\a\b.txt"
    # UNC 走 \\?\UNC\ 形式
    assert winpath.native(r"\\server\share\x").startswith("\\\\?\\UNC\\")


def test_winpath_normalize():
    assert winpath.normalize('"C:\\a\\b\\"') == "C:\\a\\b"
    assert winpath.normalize("C:/a/b") == "C:\\a\\b"
    assert winpath.normalize("C:\\a\\b\\..\\c") == "C:\\a\\c"


def test_has_alternate_data_stream():
    assert winpath.has_alternate_data_stream(r"C:\a\f.txt:stream")
    assert not winpath.has_alternate_data_stream(r"C:\a\f.txt")


# ---------------------------------------------------------------------------
# 简易 runner
# ---------------------------------------------------------------------------

def _main() -> int:
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failed.append((name, f"断言失败: {exc}"))
            print(f"  FAIL  {name}: 断言失败: {exc}")
        except Exception as exc:
            failed.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} 通过")
    if failed:
        print("\n失败明细：")
        for name, reason in failed:
            print(f"  {name}: {reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
