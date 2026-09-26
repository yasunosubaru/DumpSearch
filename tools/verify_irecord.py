"""校验手写 $I 记录的结构是否正确（Shell 能否据此还原）。"""

import glob
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dupehunter.ops import trash  # noqa: E402

D = chr(36)


def main():
    print("SID 解析链路:")
    for name, fn in (("advapi32", trash._sid_via_advapi32),
                     ("whoami", trash._sid_via_whoami),
                     ("可写目录探测", trash._sid_by_writable_probe)):
        try:
            print(f"  {name:<14} -> {fn()}")
        except Exception as exc:
            print(f"  {name:<14} -> 异常 {exc}")
    sid = trash.resolve_sid()
    print("  最终 resolve_sid() ->", sid)
    if not sid:
        print("!! SID 解析失败")
        return 1

    # 造一个长路径文件，走 manual 方案。
    # TEMP 兜底用 %SystemRoot%\Temp：os.environ.get 的默认值里**不要**写
    # 任何带用户名的路径（%TEMP% 会展开成 C:\Users\<用户名>\AppData\...），
    # 那等于把本机用户名写进公开仓库的源码里。
    root = os.path.join(
        os.environ.get("TEMP")
        or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp"),
        "irec_verify")
    deep = root
    for _ in range(14):
        deep = os.path.join(deep, "a_very_long_directory_name_for_path_length_test")
    os.makedirs(deep, exist_ok=True)
    src = os.path.join(deep, "R" + "z" * 50 + ".dat")
    with open(src, "wb") as f:
        f.write(b"IRECORD-TEST-PAYLOAD")

    before = set(glob.glob(os.path.join(f"C:\\", D + "Recycle.Bin", sid, D + "I*")))
    ok, reason = trash.trash_manual(src, sid)
    after = set(glob.glob(os.path.join(f"C:\\", D + "Recycle.Bin", sid, D + "I*")))
    print(f"\ntrash_manual -> ok={ok} reason={reason!r}")
    if not ok:
        return 1
    print("源文件已消失:", not os.path.exists(src))

    new = sorted(after - before)
    if len(new) != 1:
        print("!! 期望新增 1 个 $I 记录，实际", len(new))
        return 1
    info = new[0]
    raw = open(info, "rb").read()

    ver, size, ft, chars = struct.unpack("<QQQI", raw[:28])
    path_bytes = raw[28:]
    recorded = path_bytes.decode("utf-16-le")

    data_file = info.replace(D + "I", D + "R", 1)
    print(f"\n$I 记录: {os.path.basename(info)}  ({len(raw)} 字节)")
    print(f"  版本 ver      = {ver}   (期望 2)")
    print(f"  大小 size     = {size}")
    print(f"  时间 filetime = {ft}")
    print(f"  字符数 chars  = {chars}")
    print(f"  路径字节数    = {len(path_bytes)}  (期望 {chars*2})")
    print(f"  记录路径      = {recorded[:60]}...{recorded[-30:]}")
    print(f"  原路径长度    = {len(src)}  一致: {recorded == src}")
    print(f"  $R 配对存在   = {os.path.exists(data_file)}")
    if os.path.exists(data_file):
        print(f"  $R 内容大小   = {os.path.getsize(data_file)}  (与 $I 一致: "
              f"{os.path.getsize(data_file) == size})")

    problems = []
    if ver != 2:
        problems.append("版本号不是 2")
    if len(path_bytes) != chars * 2:
        problems.append("路径字节数与字符数不符")
    if recorded != src:
        problems.append("路径与原路径不一致")
    if not os.path.exists(data_file):
        problems.append("缺少配对的 $R 文件")
    if len(raw) != 28 + len(path_bytes):
        problems.append("记录有多余填充字节")

    print()
    if problems:
        print("!! $I 记录存在问题:")
        for p in problems:
            print("   -", p)
        return 1
    print("$I 记录结构正确，Explorer 可据此还原")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
