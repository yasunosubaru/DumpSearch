"""发布包脱敏核查：确认 zip 里没有本机路径、邮箱、用户名。

为什么单独写：源码扫描（scan_secrets.py）只覆盖 git 里的文件，
而**发布包是另一个载体** —— 构建过程可能把本机绝对路径写进
deps.json / runtimeconfig.json / 生成的 XAML 代码 / 甚至 PDB 残留。
只扫源码就宣布"已脱敏"是不够的。

另外这个脚本刻意**不用正则**：之前用正则扫 .NET 标准库 DLL 时，
`\\Users\\` 里的 `\\U` 被当成正则转义，匹配到了
`SetApartmentStateUnchecked` 这种方法名，产生一片假阳性。
这里改用字节串直接 find，零歧义。

用法::

    python tools\\scan_release_zip.py [zip路径]
"""

from __future__ import annotations

import os
import sys
import zipfile

# (标签, 字节串)。反斜杠用 bytes([92]) 拼，避免任何转义歧义。
BS = bytes([92])
NEEDLES: list[tuple[str, bytes]] = [
    ("Windows 用户名", b"SunCh"),
    ("QQ 邮箱", b"1845639127"),
    ("邮箱域名", b"qq.com"),
    ("V 盘工作区", b"V:" + BS + b"Developer"),
    ("用户目录", b"C:" + BS + b"Users" + BS),
    ("本机用户名", b"yasunosuabru"),
]

# 这些文件天然会带路径/身份信息，跳过（但会在报告里列出，说明没漏看）
EXPECTED = (".pdb",)


def scan(zip_path: str) -> int:
    if not os.path.exists(zip_path):
        print(f"找不到发布包: {zip_path}")
        return 2

    hits: list[tuple[str, str, str]] = []
    skipped: list[str] = []
    total = 0

    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name.lower().endswith(EXPECTED):
                skipped.append(name)
                continue
            total += 1
            try:
                data = z.read(name)
            except Exception:
                continue
            for label, needle in NEEDLES:
                i = data.find(needle)
                if i >= 0:
                    ctx = data[max(0, i - 60): i + 60]
                    hits.append((label, name,
                                 ctx.decode("utf-8", "replace").replace("\n", " ")))

    print(f"扫描发布包 {os.path.basename(zip_path)}")
    print(f"  检查 {total} 个条目，跳过 {len(skipped)} 个调试符号文件")

    if skipped:
        # PDB 里必然有本机路径。如果发布包里还留着 PDB，那就不是"跳过"的问题了。
        print(f"  [注意] 包内存在调试符号: {', '.join(skipped)}")
        print("         PDB 会嵌入完整本机编译路径，发布包不该带它。"
              "package_release.py 会在打包前剔除 .pdb，"
              "若这里出现说明打包流程被绕过了。")

    if hits:
        print()
        print(f"发现 {len(hits)} 处需要确认：")
        for label, name, ctx in hits:
            print(f"  [{label}] {name}")
            print(f"      …{ctx}…")
        return 1

    print("  发布包干净：未发现本机路径、邮箱或用户名")
    return 0


def main() -> int:
    default = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"),
                           "ds_rel",
                           "DumpSearch-v1.0.0-win-x64-selfcontained.zip")
    path = sys.argv[1] if len(sys.argv) > 1 else default
    return scan(path)


if __name__ == "__main__":
    sys.exit(main())