"""复现 XamlCompiler 内部那次 csc 编译，从而拿到它吞掉的真实错误。

背景：Windows App SDK 的 XamlCompiler.exe 在编译失败时只打印
「错误: 1」，把 csc 的输出全部丢掉。而 XamlCompiler 用的是 net472 版本的
csc 宿主 + 自己那份 ReferenceAssemblies，MSBuild 主编译用不到同一份
输入组合，所以主编译通过、XamlCompiler 失败的情况完全看不出来。

做法：从 obj 里的 input.json 取出 References，自己组装一次 csc 调用。
"""

import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.path.join(HERE, "src", "DumpSearch.App")
OBJ = os.path.join(PROJ, "obj", "Release", "net8.0-windows10.0.19041.0")
INPUT = os.path.join(OBJ, "input.json")
CSC = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                   "dotnet", "sdk", "10.0.400", "Roslyn", "bincore", "csc.exe")

# XamlCompiler 用的语言版本比 csproj 里的 LangVersion 低（这是本项目的坑）
LANGVERSION = "12.0"


def main() -> int:
    if not os.path.exists(INPUT):
        print("找不到 input.json，请先跑一次 dotnet build")
        return 1
    with open(INPUT, encoding="utf-8-sig") as fh:
        data = json.load(fh)

    refs = [r["FullPath"] for r in data.get("ReferenceAssemblies") or []
            if os.path.exists(r.get("FullPath", ""))]
    print(f"引用程序集: {len(refs)} 个")

    sources = []
    for pat in ("*.cs", "Core/*.cs", "ViewModels/*.cs"):
        sources.extend(glob.glob(os.path.join(PROJ, pat)))
    generated = glob.glob(os.path.join(OBJ, "*.g.cs")) + \
                glob.glob(os.path.join(OBJ, "*.g.i.cs"))
    sources.extend(generated)
    print(f"源文件: 项目 {len(sources) - len(generated)} 个 + 生成 {len(generated)} 个")

    rsp = os.path.join(OBJ, "probe.rsp")
    with open(rsp, "w", encoding="utf-8") as fh:
        fh.write("-nologo\n-noconfig\n-utf8output\n")
        fh.write(f"-langversion:{LANGVERSION}\n")
        fh.write("-target:library\n-nowarn:1701,1702,CA1416\n")
        fh.write(f"-out:{os.path.join(OBJ, 'probe_out.dll')}\n")
        for r in refs:
            fh.write(f'-reference:"{r}"\n')
        for s in sources:
            fh.write(f'"{s}"\n')

    print(f"\n以 C# {LANGVERSION} 复现编译（模拟 XamlCompiler）…\n")
    r = subprocess.run([CSC, f"@{rsp}"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    out = (r.stdout or "") + (r.stderr or "")
    lines = [l for l in out.splitlines() if ": error " in l or ": 错误 " in l]
    if not lines:
        print("没有编译错误。")
        print(out[:2000])
        return 0
    seen = set()
    for l in lines:
        key = l.split(": error ")[-1][:120]
        if key in seen:
            continue
        seen.add(key)
        print("  " + l)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
