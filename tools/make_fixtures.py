"""生成一棵可控的测试目录树，用于验证扫描/哈希/去重是否正确。

用法::

    python tools/make_fixtures.py V:\\Temp\\dupetest --clean
"""

from __future__ import annotations

import argparse
import os
import random
import shutil

PAYLOADS = {
    "alpha": b"alpha payload " * 500,
    "bravo": bytes(range(256)) * 40,
    # 头部与 alpha 前 4KB 相同但整体不同 -> 用于验证"分段采样"能区分它们
    "alpha_head_only": (b"alpha payload " * 500) + b"TAIL-UNIQUE-XYZ" * 300,
    "charlie": b"",
    "delta": b"delta" * 100000,
}


def _assert_safe_to_wipe(root: str) -> None:
    """--clean 会递归删除目标目录，先做一次安全检查。

    这是把 ``rmtree`` 挂在用户输入的路径上，必须挡住明显危险的目标：
    盘符根目录、用户目录、系统目录、路径过短。
    """
    target = os.path.abspath(root)
    parts = [p for p in target.split(os.sep) if p]
    system = {"windows", "program files", "program files (x86)", "programdata",
              "users", "$recycle.bin", "system volume information"}
    problems = []
    if len(target) <= 3 or target.endswith(":\\"):
        problems.append("目标是盘符根目录")
    if len(parts) < 3:
        problems.append(f"路径层级太浅（{len(parts)} 层）")
    lowered = {p.lower() for p in parts}
    hit = lowered & system
    if hit:
        problems.append(f"路径包含系统目录: {sorted(hit)}")
    if problems:
        raise SystemExit(
            "拒绝删除该目录（--clean）：\n  - " + "\n  - ".join(problems)
            + f"\n目标: {target}"
        )


def build(root: str, clean: bool = False, seed: int = 7) -> dict:
    if clean and os.path.isdir(root):
        _assert_safe_to_wipe(root)
        shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    rng = random.Random(seed)

    layout = {
        # 完全重复的 3 份
        r"docs\a\report.txt": "alpha",
        r"docs\b\report.txt": "alpha",
        r"docs\c\report.txt": "alpha",
        # 只差尾部：应判为**不重复**
        r"docs\a\report_v2.txt": "alpha_head_only",
        r"docs\b\report_v2.txt": "alpha_head_only",
        # 二进制完全重复
        r"bin\one\blob.bin": "bravo",
        r"bin\two\blob.bin": "bravo",
        r"bin\three\copy_of_blob.bin": "bravo",
        # 大文件重复
        r"big\one\huge.dat": "delta",
        r"big\two\huge.dat": "delta",
        # 唯一文件
        r"unique\only_me.txt": "delta",
        # 空文件
        r"empty\empty1.txt": "charlie",
        r"empty\empty2.txt": "charlie",
        # 噪声
        r"noise\f1.txt": "charlie",
        r"noise\f2.txt": "charlie",
        r"noise\f3.txt": "charlie",
    }

    # 排除目录里也放重复文件，验证排除逻辑
    layout[r"node_modules\pkg\dup.txt"] = "alpha"
    layout[r"node_modules\pkg\dup2.txt"] = "alpha"

    written = 0
    for rel, key in layout.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(PAYLOADS[key])
        written += 1

    # 随机填充，制造不同大小的文件以验证"按大小分组"过滤
    fillers = 0
    for i in range(60):
        rel = os.path.join("noise", f"rand_{i:03d}.bin")
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(bytes(rng.getrandbits(8) for _ in range(rng.randint(1, 3000))))
        fillers += 1

    # 刻意制造一个超长路径（>260 字符），验证长路径支持
    deep = os.path.join(root, "deep")
    for _ in range(14):
        deep = os.path.join(deep, "a_very_long_directory_name_for_path_length_test")
    os.makedirs(deep, exist_ok=True)
    long_path = os.path.join(deep, "x" * 40 + ".txt")
    with open(long_path, "wb") as fh:
        fh.write(PAYLOADS["alpha"])
    long_len = len(long_path)

    return {
        "root": root,
        "written": written,
        "fillers": fillers,
        "long_path_len": long_len,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成重复文件测试树")
    parser.add_argument("root", help="目标目录")
    parser.add_argument("--clean", action="store_true", help="先清空目标目录")
    args = parser.parse_args()

    info = build(args.root, clean=args.clean)
    print(f"根目录 : {info['root']}")
    print(f"文件数 : {info['written']} 个受控 + {info['fillers']} 个随机")
    print(f"长路径 : {info['long_path_len']} 字符 (>260 表示长路径测试生效)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
