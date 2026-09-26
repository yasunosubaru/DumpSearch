using System.Collections.Concurrent;
using System.Diagnostics.CodeAnalysis;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text;

namespace DumpSearch.App.Core;

/// <summary>Windows 路径工具：长路径、盘符枚举、属性判定。</summary>
public static class WinPath
{
    public const string LongPrefix = @"\\?\";
    public const string LongUncPrefix = @"\\?\UNC\";

    /// <summary>shell 回收站 API 的路径长度上限。超过它 SHFileOperationW 会永久删除。</summary>
    public const int LongPathUnsupported = 260;

    /// <summary>普通 IO 失败后，才尝试加长路径前缀的长度阈值。</summary>
    public const int LONG_PATH_THRESHOLD = 250;

    public static bool IsWindows => OperatingSystem.IsWindows();

    /// <summary>补长路径前缀。业务层应优先用常规路径，仅在单次 IO 失败后降级。</summary>
    public static string Native(string path)
    {
        if (!IsWindows) return path;
        if (path.StartsWith(LongPrefix, StringComparison.Ordinal)) return path;
        if (path.StartsWith(@"\\", StringComparison.Ordinal))
            return LongUncPrefix + path[2..].Replace('/', '\\');
        return LongPrefix + Path.GetFullPath(path).Replace('/', '\\');
    }

    /// <summary>去掉长路径前缀，得到可展示、可比较的常规路径。</summary>
    public static string Display(string path)
    {
        if (!path.StartsWith(LongPrefix, StringComparison.Ordinal)) return path;
        var rest = path[LongPrefix.Length..];
        return rest.StartsWith("UNC\\", StringComparison.Ordinal) ? @"\\" + rest[4..] : rest;
    }

    /// <summary>路径是否超出 shell 回收站 API 的安全长度。</summary>
    public static bool IsLongPath(string path) => Display(path).Length > LongPathUnsupported;

    /// <summary>
    /// 保证目录串以单个分隔符结尾。做「剥公共前缀」这类字符串拼接时必须用它，
    /// 否则前缀和剩余部分会粘在一起（…\docs\a + b）。
    /// </summary>
    public static string EnsureTrailingSep(string dir)
    {
        if (dir.Length == 0) return dir;
        char last = dir[^1];
        return last == '\\' || last == '/' ? dir : dir + '\\';
    }

    public static string Normalize(string? path)
    {
        var raw = (path ?? "").Trim().Trim('"');
        if (raw.Length == 0)
            throw new ArgumentException("路径为空", nameof(path));
        if (raw.StartsWith(LongPrefix, StringComparison.Ordinal))
        {
            raw = raw[LongPrefix.Length..];
            if (raw.StartsWith("UNC\\", StringComparison.Ordinal)) raw = @"\\" + raw[4..];
        }
        raw = Environment.ExpandEnvironmentVariables(raw);
        raw = raw.Replace('/', '\\');
        if (raw.Length > 3 && raw.EndsWith('\\')) raw = raw.TrimEnd('\\');
        return raw;
    }

    [DoesNotReturn]
    private static void Fail(string message) => throw new ArgumentException(message);

    public static string? NormalizeOrNull(string? path)
    {
        try { return Normalize(path); }
        catch (ArgumentException) { return null; }
    }

    public static string DriveOf(string path)
    {
        if (path.Length >= 2 && char.IsLetter(path[0]) && path[1] == ':')
            return path[..2];
        if (path.StartsWith(@"\\", StringComparison.Ordinal))
        {
            var parts = path.Split('\\', StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length >= 2) return $@"\\{parts[0]}\{parts[1]}";
        }
        return string.Empty;
    }

    public static bool IsUnc(string path) => path.StartsWith(@"\\", StringComparison.Ordinal);

    public static string FormatBytes(double bytes, int precision = 2)
    {
        if (double.IsNaN(bytes) || double.IsInfinity(bytes)) return "-";
        string[] units = { "B", "KB", "MB", "GB", "TB", "PB" };
        bool neg = bytes < 0;
        double v = Math.Abs(bytes);
        int i = 0;
        while (v >= 1024.0 && i < units.Length - 1) { v /= 1024.0; i++; }
        // 不用 $"{v:.{precision}f}" 这种嵌套插值：本机 csc 对它报
        // CS1056「意外的字符 '{'」，且错误位置与真实原因毫无关联。
        string number = i == 0
            ? v.ToString("0", CultureInfo.InvariantCulture)
            : v.ToString("F" + precision, CultureInfo.InvariantCulture);
        return (neg ? "-" : "") + number + " " + units[i];
    }

    /// <summary>解析 "10M" / "1.5G" / "500K" 这类体积字符串。</summary>
    public static long ParseSize(string? text, long fallback = 0)
    {
        var raw = (text ?? "").Trim().ToLowerInvariant().TrimEnd('b', 'B');
        if (raw.Length == 0) return fallback;
        long mult = raw[^1] switch
        {
            'k' => 1024L,
            'm' => 1024L * 1024,
            'g' => 1024L * 1024 * 1024,
            't' => 1024L * 1024 * 1024 * 1024,
            _ => 1L,
        };
        var num = mult == 1 ? raw : raw[..^1];
        return double.TryParse(num, System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out var d)
            ? (long)(d * mult) : fallback;
    }

    /// <summary>
    /// 把 Win32 FILETIME（自 1601-01-01 起的 100 纳秒数）格式化成可读时间。
    /// 必须走 <see cref="DateTime.FromFileTime"/>：FILETIME 已经是 100 纳秒单位，
    /// 任何"减纪元再乘/除"的手工换算都会把年份放大或缩小几个数量级。
    /// </summary>
    public static string FormatTimestamp(long fileTimeTicks)
    {
        if (fileTimeTicks <= 0) return "-";
        try
        {
            return DateTime.FromFileTime(fileTimeTicks).ToLocalTime()
                .ToString("yyyy-MM-dd HH:mm");
        }
        catch (ArgumentOutOfRangeException)
        {
            // 越界只可能来自损坏的元数据，格式化不了就如实说不知道
            return "-";
        }
    }

    /// <summary>
    /// 相对时间。判断"该删哪个"时，绝对时间戳很难比——要自己心算差几天。
    /// 「3 天前」比「2026-09-23 17:14」一眼就能看出先后。
    ///
    /// 刻意保留同年内的月/日、跨年才显示年份：多数重复副本的时间跨度在
    /// 几个月内，写全 <c>yyyy-MM-dd</c> 只会让"哪边更新"更难扫。
    /// </summary>
    public static string FormatRelativeTimestamp(long fileTimeTicks, DateTime? now = null)
    {
        if (fileTimeTicks <= 0) return "-";
        DateTime t;
        try { t = DateTime.FromFileTime(fileTimeTicks).ToLocalTime(); }
        catch (ArgumentOutOfRangeException) { return "-"; }

        var refTime = now ?? DateTime.Now;
        // 只按"日历日"算天数差：同一时刻相隔 25 小时就该算"昨天"，
        // 按精确时间差算会得到荒谬的"0 天前"。
        int days = (refTime.Date - t.Date).Days;
        var clock = t.ToString("HH:mm");

        return days switch
        {
            0 => $"今天 {clock}",
            1 => $"昨天 {clock}",
            2 => $"前天 {clock}",
            < 0 => t.ToString("yyyy-MM-dd HH:mm"),      // 未来时间（时钟偏差/备份工具）
            < 7 => $"{days} 天前",
            < 30 => t.ToString("M月d日 HH:mm"),
            < 365 => t.ToString("yyyy年M月d日"),
            _ => t.ToString("yyyy-MM-dd"),
        };
    }

    /// <summary>
    /// 把过长路径从中间省略，保留开头和结尾（结尾更有辨识度）。
    /// 结果长度**正好**是 max（不超过时原样返回）——
    /// 布局里按固定宽度排版，长度不固定会导致省略号忽左忽右。
    /// </summary>
    public static string EllipsizeMiddle(string text, int max)
    {
        if (max <= 5 || text.Length <= max) return text;
        int keep = (max - 3) / 2;
        int tail = max - 3 - keep;      // 奇偶凑整，保证 head+3+tail == max
        return text[..keep] + "..." + text[^tail..];
    }
}

/// <summary>一个可扫描的卷。</summary>
public sealed class DriveEntry
{
    public required string Root { get; init; }
    public string Label { get; init; } = "";
    public string FileSystem { get; init; } = "";
    public uint DriveType { get; init; }
    public ulong TotalBytes { get; init; }
    public ulong FreeBytes { get; init; }

    public string TypeName => Win32.DriveTypeName(DriveType);
    public bool IsNetwork => DriveType == Win32.DRIVE_REMOTE;
    public string DisplayName => string.IsNullOrWhiteSpace(Label) ? Root : $"{Root} {Label}";
    public bool SelectedByDefault => DriveType is Win32.DRIVE_FIXED or Win32.DRIVE_REMOVABLE;
}

public static class DriveEnumerator
{
    /// <summary>枚举可用盘符。</summary>
    /// <remarks>
    /// 必须用 GetDiskFreeSpaceExW 成功与否来过滤：已挂载的 VHD 字母盘、
    /// 掉线的网络盘在 GetLogicalDrives 里存在、GetDriveTypeW 也报 FIXED，
    /// 但取不到空间——不提前过滤就会在扫描时才发现整盘读不了。
    /// </remarks>
    public static IReadOnlyList<DriveEntry> List(bool includeCdrom = false,
        bool includeNetwork = true)
    {
        var result = new List<DriveEntry>();
        if (!WinPath.IsWindows) return result;

        uint mask = Win32.GetLogicalDrives();
        for (int i = 0; i < 26; i++)
        {
            if ((mask & (1u << i)) == 0) continue;
            string root = $"{(char)('A' + i)}:\\";
            uint type = Win32.GetDriveTypeW(root);
            if (type is Win32.DRIVE_NO_ROOT_DIR or Win32.DRIVE_UNKNOWN) continue;
            if (type == Win32.DRIVE_CDROM && !includeCdrom) continue;
            if (type == Win32.DRIVE_REMOTE && !includeNetwork) continue;

            if (!Win32.GetDiskFreeSpaceExW(root, out ulong free, out ulong total, out _))
                continue;   // 卷不可用（未挂载 VHD / 掉线网络盘）

            var nameBuf = new StringBuilder(261);
            var fsBuf = new StringBuilder(64);
            Win32.GetVolumeInformationW(root, nameBuf, 261, out _, out _, out _,
                fsBuf, 64);

            result.Add(new DriveEntry
            {
                Root = root,
                Label = nameBuf.ToString(),
                FileSystem = fsBuf.ToString(),
                DriveType = type,
                TotalBytes = total,
                FreeBytes = free,
            });
        }
        return result;
    }
}
