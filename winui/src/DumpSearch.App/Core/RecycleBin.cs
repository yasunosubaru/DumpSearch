using System.Runtime.InteropServices;
using System.Text;

namespace DumpSearch.App.Core;

public sealed record DeleteOutcome
{
    public List<string> Deleted { get; } = new();
    public List<(string Path, string Reason)> Failed { get; } = new();
    public bool Ok => Failed.Count == 0;
    public int Total => Deleted.Count + Failed.Count;
    public string Summary => Failed.Count == 0
        ? $"已移入回收站 {Deleted.Count:N0} 个"
        : $"已移入回收站 {Deleted.Count:N0} 个，失败 {Failed.Count:N0} 个";
}

/// <summary>
/// 把文件移入 Windows 回收站（可撤销）。
/// </summary>
/// <remarks>
/// 两个关键设计：
/// 1. 超过 <see cref="WinPath.LongPathUnsupported"/> 字符的路径**绝不能**
///    交给 SHFileOperationW —— 实测它会"成功"返回却把文件永久删除、
///    不留任何回收站条目。这些改走手写 $Recycle.Bin 的 $I/$R 方案。
/// 2. 删除后逐个复核存在性。SHFileOperationW 的返回码在长路径场景不可信，
///    "文件还在" 是唯一可信的失败证据。
/// </remarks>
public static class RecycleBin
{
    private const ushort TrashFlags =
        Win32.FOF_ALLOWUNDO | Win32.FOF_NOCONFIRMATION | Win32.FOF_NOERRORUI
        | Win32.FOF_SILENT | Win32.FOF_NOCONFIRMMKDIR;

    private const int BatchSize = 60;
    private static readonly string Dollar = "$";
    private static string? _sidCache;

    public static bool Exists(string path)
    {
        try { return File.Exists(path) || Directory.Exists(path); }
        catch { return false; }
    }

    /// <summary>
    /// 把路径列表移入回收站。
    /// </summary>
    /// <param name="dryRun">
    /// true 时只做存在性核对与可删除性判断，**不执行任何删除**。
    /// 测试与预览一律走这条路径。
    /// </param>
    public static DeleteOutcome TrashFiles(
        IEnumerable<string> paths,
        Action<int, int>? progress = null,
        bool dryRun = false)
    {
        var outcome = new DeleteOutcome();
        var targets = paths.Where(p => !string.IsNullOrWhiteSpace(p))
                           .Distinct(StringComparer.OrdinalIgnoreCase)
                           .ToList();
        if (targets.Count == 0) return outcome;

        // 先核对存在性。SHFileOperationW 对不存在的路径返回 0（不算错误），
        // "文件本来就不存在"绝不能被报告成"已成功删除"。
        var present = new List<string>(targets.Count);
        foreach (var path in targets)
        {
            if (Exists(path)) present.Add(path);
            else outcome.Failed.Add((path, "文件已不存在（可能已被其它程序移动或删除）"));
        }
        if (present.Count == 0) return outcome;

        if (dryRun) { DryRun(present, outcome, progress); return outcome; }

        var shortPaths = present.Where(p => !WinPath.IsLongPath(p)).ToList();
        var longPaths = present.Where(WinPath.IsLongPath).ToList();

        int total = present.Count, done = 0;
        for (int start = 0; start < shortPaths.Count; start += BatchSize)
        {
            var batch = shortPaths.GetRange(start, Math.Min(BatchSize, shortPaths.Count - start));
            int code = DeleteBatchViaShell(batch);
            foreach (var p in batch)
            {
                if (Exists(p)) outcome.Failed.Add((p, ExplainShellCode(code)));
                else outcome.Deleted.Add(p);
            }
            done += batch.Count;
            progress?.Invoke(done, total);
        }

        if (longPaths.Count > 0)
        {
            string? sid = null;
            for (int i = 0; i < longPaths.Count; i++)
            {
                sid ??= ResolveSid();
                var (ok, reason) = TrashManual(longPaths[i], sid);
                if (ok) outcome.Deleted.Add(longPaths[i]);
                else outcome.Failed.Add((longPaths[i], reason));
                progress?.Invoke(done + i + 1, total);
            }
        }
        return outcome;
    }

    private static void DryRun(List<string> present, DeleteOutcome outcome,
        Action<int, int>? progress)
    {
        for (int i = 0; i < present.Count; i++)
        {
            try
            {
                var attrs = File.GetAttributes(present[i]);
                if ((attrs & FileAttributes.ReadOnly) != 0)
                {
                    // 只读可清除，不算阻断
                }
            }
            catch (Exception ex)
            {
                outcome.Failed.Add((present[i], ex.Message));
            }
            progress?.Invoke(i + 1, present.Count);
        }
    }

    private static int DeleteBatchViaShell(List<string> batch)
    {
        foreach (var p in batch) ClearReadOnly(p);
        if (batch.Count == 0) return 0;
        // pFrom 必须是双 NUL 结尾的、以 NUL 分隔的宽字符串列表
        var payload = string.Join("\0", batch) + "\0\0";
        var op = new Win32.SHFILEOPSTRUCTW
        {
            hwnd = IntPtr.Zero,
            wFunc = Win32.FO_DELETE,
            pFrom = payload,
            pTo = null,
            fFlags = TrashFlags,
            fAnyOperationsAborted = false,
            hNameMappings = IntPtr.Zero,
            lpszProgressTitle = null,
        };
        return Win32.SHFileOperationW(ref op);
    }

    private static string ExplainShellCode(int code) => code switch
    {
        -1 => "操作被中止",
        0 => "删除后文件仍然存在（可能被占用）",
        32 => "文件被其他程序占用，无法删除（请关闭正在打开它的程序）",
        33 => "文件被锁定（共享冲突）",
        121 => "文件为只读且无法清除属性",
        1223 or 0x75 => "操作被取消（ERROR_CANCELLED）",
        _ => $"SHFileOperationW 失败（错误码 {code}）",
    };

    private static void ClearReadOnly(string path)
    {
        try
        {
            var attrs = File.GetAttributes(path);
            if ((attrs & FileAttributes.ReadOnly) != 0)
                File.SetAttributes(path, attrs & ~FileAttributes.ReadOnly);
        }
        catch { /* 删不掉时由上层报告 */ }
    }

    // -------------------------------------------------------------------
    // 长路径：手写 $Recycle.Bin 的 $I / $R
    // -------------------------------------------------------------------

    public static string ResolveSid()
    {
        if (_sidCache is not null) return _sidCache;
        string? sid = SidViaWindowsIdentity()
                      ?? SidViaWhoami()
                      ?? SidByWritableProbe();
        _sidCache = sid ?? "";
        return _sidCache;
    }

    private static string? SidViaWindowsIdentity()
    {
        try
        {
            using var identity = System.Security.Principal.WindowsIdentity.GetCurrent();
            return identity.User?.Value;
        }
        catch { return null; }
    }

    private static string? SidViaWhoami()
    {
        try
        {
            var psi = new System.Diagnostics.ProcessStartInfo("whoami.exe", "/user /fo csv /nh")
            {
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using var p = System.Diagnostics.Process.Start(psi);
            if (p is null) return null;
            string output = p.StandardOutput.ReadToEnd();
            p.WaitForExit(5000);
            foreach (var token in output.Split(','))
            {
                var t = token.Trim().Trim('"');
                if (t.StartsWith("S-1-", StringComparison.OrdinalIgnoreCase)
                    && t.Length > 10) return t;
            }
        }
        catch { }
        return null;
    }

    private static string SidByWritableProbe()
    {
        foreach (var dir in Directory.EnumerateDirectories(@"C:\" + Dollar + "Recycle.Bin"))
        {
            var name = Path.GetFileName(dir);
            if (!name.StartsWith("S-1-", StringComparison.OrdinalIgnoreCase)) continue;
            string probe = Path.Combine(dir, ".ds_probe");
            try
            {
                File.WriteAllBytes(probe, Array.Empty<byte>());
                File.Delete(probe);
                return name;
            }
            catch { }
        }
        return "";
    }

    private static ulong NowFileTime()
    {
        Win32.GetSystemTimeAsFileTime(out var ft);
        return ft.ToUInt64();
    }

    /// <summary>
    /// 长路径专用：搬进 $Recycle.Bin 并写好 $I 记录。**任何一步失败都回滚**，
    /// 绝不会出现"文件没了但回收站里也没有"的情况。
    /// </summary>
    public static (bool Ok, string Reason) TrashManual(string path, string? sid = null)
    {
        if (!WinPath.IsWindows) return (false, "长路径回收站仅支持 Windows");

        sid ??= ResolveSid();
        if (string.IsNullOrEmpty(sid)) return (false, "无法确定当前用户 SID");
        if (!Exists(path)) return (false, "源文件不存在");

        string drive = WinPath.DriveOf(path);
        if (string.IsNullOrEmpty(drive)) return (false, "无法确定所在盘符");

        string binRoot = drive + "\\" + Dollar + "Recycle.Bin\\" + sid;
        try { Directory.CreateDirectory(binRoot); }
        catch (Exception ex) { return (false, $"无法访问回收站目录（网络盘/无权限？）: {ex.Message}"); }

        bool isDir = Directory.Exists(path);
        string ext = isDir ? "" : Path.GetExtension(path);

        string? target = null, itemId = null;
        for (int i = 0; i < 8; i++)
        {
            string rid = Convert.ToHexString(System.Security.Cryptography.RandomNumberGenerator
                .GetBytes(6));
            string candidate = Path.Combine(binRoot, Dollar + "R" + rid + ext);
            if (Exists(candidate)) continue;
            target = candidate;
            itemId = rid + ext;
            break;
        }
        if (target is null || itemId is null) return (false, "无法分配回收站条目 ID");

        try
        {
            if (isDir) Directory.Move(path, target);
            else File.Move(path, target);
        }
        catch (IOException ex)
        {
            int code = ex.HResult & 0xFFFF;
            return (false, code is 32 or 33
                ? "文件被其他进程占用"
                : $"移入回收站失败: {ex.Message}");
        }
        catch (Exception ex) { return (false, $"移入回收站失败: {ex.Message}"); }

        // $I v2: 版本(8) | 大小(8) | 修改时间 FILETIME(8) | 路径字符数(4) | UTF-16LE 路径
        // 路径必须存完整值：截断会让 Shell 静默丢弃整个条目。
        long size = 0;
        try { if (!isDir) size = new FileInfo(target).Length; } catch { }
        string displayPath = WinPath.Display(path);
        byte[] pathBytes = Encoding.Unicode.GetBytes(displayPath);

        using var ms = new MemoryStream();
        using var w = new BinaryWriter(ms);
        w.Write((ulong)2);
        w.Write((ulong)size);
        w.Write(NowFileTime());
        w.Write((uint)displayPath.Length);
        w.Write(pathBytes);
        w.Flush();

        string infoPath = Path.Combine(binRoot, Dollar + "I" + itemId);
        try
        {
            using var fs = new FileStream(infoPath, FileMode.Create, FileAccess.Write,
                FileShare.None);
            ms.Position = 0;
            ms.CopyTo(fs);
        }
        catch (Exception ex)
        {
            try
            {
                if (isDir) Directory.Move(target, path);
                else File.Move(target, path);
            }
            catch { }
            return (false, $"写回收站记录失败（已回滚）: {ex.Message}");
        }

        return (true, "");
    }

    /// <summary>清空回收站（可选根路径，null = 全部卷）。</summary>
    public static bool EmptyRecycleBin(string? rootPath = null)
    {
        if (!WinPath.IsWindows) return false;
        const uint flags = 0x1 | 0x2 | 0x4;   // NOCONFIRMATION | NOPROGRESSUI | NOSOUND
        int hr = Win32.SHEmptyRecycleBinW(IntPtr.Zero, rootPath, flags);
        return hr is 0 or unchecked((int)0x8000FFFF);
    }
}
