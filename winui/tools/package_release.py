"""构建发布用的自包含 zip，并核对产物完整性。

为什么要自包含版：框架依赖版要求目标机器装好 .NET 8 运行时**和**
Windows App Runtime。发到 GitHub Release 上的东西应该"下载即用"，
所以发自包含版（自含 .NET 运行时）。

两个坑（都实测踩过）：

1. **``PublishSingleFile`` 对 WinUI 3 不可用**。单文件发布把资源也塞进 exe，
   但 WinUI 的 XAML 资源加载器要求 ``.pri`` 等资源是磁盘上的真实文件，
   结果启动即崩：``0xC000027B``（STATUS_STOWED_RESOURCES），
   故障模块 Microsoft.UI.Xaml.dll。必须用普通的目录式发布再打包。

2. ``<Content Include="Assets\\**" />`` **只进 build 输出、不进 publish 输出**。
   症状很隐蔽：exe 里嵌的图标还在，窗口标题栏图标没了。
   csproj 里已补 ``CopyToPublishDirectory``。

用法::

    python winui\\tools\\package_release.py                  # 构建 + 打包 + 自检
    python winui\\tools\\package_release.py --out D:\\rel    # 指定输出目录
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", "src", "DumpSearch.App"))
EXE_NAME = "DumpSearch.exe"

def run(cmd, cwd=None, timeout=1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def build_publish(out_dir: str) -> bool:
    print(f"发布到 {out_dir} …")
    r = run(["dotnet", "publish", "-c", "Release", "-r", "win-x64",
             "--self-contained", "true", "-o", out_dir, "--nologo"], cwd=APP)
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        return False
    return True


def verify(out_dir: str) -> list[str]:
    """产物检查。返回问题列表，空表示通过。"""
    problems: list[str] = []
    exe = os.path.join(out_dir, EXE_NAME)
    if not os.path.exists(exe):
        return [f"缺少 {EXE_NAME}"]

    # 图标的多尺寸 PNG 属于资源，保留；但 .ico 必须在（标题栏要用）
    ico = os.path.join(out_dir, "Assets", "DumpSearch.ico")
    if not os.path.exists(ico):
        problems.append("缺少 Assets/DumpSearch.ico（窗口标题栏会没有图标）")

    # 不该把调试符号和源码带进发布包。pdb 能让 zip 白涨好几 MB，
    # 对最终用户毫无用处（只有调试符号服务器才需要）。
    for root, _dirs, files in os.walk(out_dir):
        for f in files:
            if f.endswith((".pdb", ".cs", ".xaml")):
                rel = os.path.relpath(os.path.join(root, f), out_dir)
                os.remove(os.path.join(root, f))
                print(f"  已剔除 {rel}")

    return problems


def smoke_test(exe: str, fixture: str | None) -> bool:
    """跑一次引擎自检，证明脱离运行时也能工作。"""
    if not fixture or not os.path.isdir(fixture):
        print("  （没有夹具目录，跳过自检）")
        return True
    r = subprocess.run([exe, "--selftest", fixture],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    tail = [l for l in (r.stdout or "").splitlines() if "通过" in l or "失败" in l]
    for l in tail[-2:]:
        print(f"  {l.strip()}")
    return r.returncode == 0


def make_zip(src_dir: str, zip_path: str) -> int:
    """打包。

    两个防护：
    1. 显式排除 zip 自身（万一输出目录和源目录重叠，不至于把自己压进去）。
    2. 边压边统计体积，跑完和预期不符就直接失败——
       曾经因为把上一轮 49 GB 的坏 zip 又压进去，产出 53 GB 的文件，
       整个过程跑了半小时才被磁盘塞满打断。
    """
    src_real = os.path.realpath(src_dir)
    zip_real = os.path.realpath(zip_path)
    total_in = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for root, _dirs, files in os.walk(src_real):
            for f in files:
                full = os.path.realpath(os.path.join(root, f))
                if full == zip_real or full.endswith((".zip", ".sha256")):
                    continue
                total_in += os.path.getsize(full)
                rel = os.path.relpath(full, src_real)
                zf.write(full, rel.replace(os.sep, "/"))

    size = os.path.getsize(zip_path)
    # 合理上限：自包含 .NET + WinUI 压缩后应在 200 MB 以内
    if size > 200 * 1024 * 1024:
        raise RuntimeError(
            f"产物异常：压缩后 {size/1024/1024:.1f} MB，"
            f"远超预期（源 {total_in/1024/1024:.1f} MB）。"
            f"多半是把旧的 zip 又打进去了。")
    return size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="zip 输出目录")
    ap.add_argument("--work", default=None,
                    help="publish 中间目录（必须与 --out 分开）")
    ap.add_argument("--fixture", default=None, help="用于自检的夹具目录")
    ap.add_argument("--version", default="v1.0.0")
    args = ap.parse_args()

    # 中间目录必须和 zip 输出目录**分开**。放在一起时，上一轮遗留的
    # 49 GB 坏 zip 会在新一轮的 publish 清理之前被 walk 到并重新打包，
    # 产出 53 GB 的二次方膨胀文件——踩过一次。
    publish_root = args.work or os.path.join(tempfile.gettempdir(), "ds_pkg_work")
    out_dir = os.path.join(publish_root, "app")
    zip_dir = args.out or os.getcwd()
    os.makedirs(zip_dir, exist_ok=True)

    # 上一轮的残留先清掉，避免 zip 目录里混进旧产物
    if os.path.isdir(publish_root):
        shutil.rmtree(publish_root)
    os.makedirs(out_dir, exist_ok=True)

    if not build_publish(out_dir):
        print("发布失败")
        return 1

    print("检查产物 …")
    problems = verify(out_dir)
    if problems:
        print("产物检查未通过：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("  产物检查通过")

    print("引擎自检 …")
    if not smoke_test(os.path.join(out_dir, EXE_NAME), args.fixture):
        print("  自检未通过，拒绝打包")
        return 1

    zip_path = os.path.join(
        zip_dir, f"DumpSearch-{args.version}-win-x64-selfcontained.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    size = make_zip(out_dir, zip_path)
    print(f"已打包 {zip_path}")
    print(f"  {size/1024/1024:.1f} MB")

    # 顺便留一份 sha256，Release 说明里可以引用
    import hashlib
    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    with open(zip_path + ".sha256", "w", encoding="ascii") as f:
        f.write(f"{digest}  {os.path.basename(zip_path)}\n")
    print(f"  sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
