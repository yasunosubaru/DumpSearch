using System.Collections.ObjectModel;

namespace DumpSearch.App.Core;

public enum ScanPhase
{
    Idle,
    Scanning,
    Grouping,
    QuickHash,
    FullHash,
    Verify,
    Done,
    Cancelled,
    Failed,
}

public enum KeepRule
{
    Newest,
    Oldest,
    ShortestPath,
    LongestPath,
    Shallowest,
    Deepest,
    First,
}

public static class KeepRuleNames
{
    public static readonly IReadOnlyList<(KeepRule Rule, string Label)> All = new[]
    {
        (KeepRule.Newest, "保留最新修改的"),
        (KeepRule.Oldest, "保留最旧修改的"),
        (KeepRule.ShortestPath, "保留路径最短的"),
        (KeepRule.LongestPath, "保留路径最长的"),
        (KeepRule.Shallowest, "保留层级最浅的"),
        (KeepRule.Deepest, "保留层级最深的"),
        (KeepRule.First, "保留首个发现的"),
    };

    public static string Label(KeepRule r) =>
        All.FirstOrDefault(x => x.Rule == r).Label ?? r.ToString();
}

/// <summary>参与比对的一个文件。</summary>
public sealed record FileEntry
{
    public required string Path { get; init; }
    public required long Size { get; init; }
    public required long LastWriteTicks { get; init; }

    public string Name => System.IO.Path.GetFileName(Path);
    public string Directory => System.IO.Path.GetDirectoryName(Path) ?? "";
    public string Extension => System.IO.Path.GetExtension(Path).ToLowerInvariant();
    public int Depth => Path.Count(c => c is '\\' or '/');

    public string SizeText => WinPath.FormatBytes(Size);

    /// <summary>绝对时间，用于需要精确值的场合（悬停详情、导出）。</summary>
    public string TimeText => WinPath.FormatTimestamp(LastWriteTicks);

    /// <summary>
    /// 相对时间（"3 天前"）。列表里主显这个：判断该删哪个靠的是先后关系，
    /// 让人心算两个 yyyy-MM-dd 的差值是最没必要的负担。
    /// </summary>
    public string TimeRelativeText => WinPath.FormatRelativeTimestamp(LastWriteTicks);

    public string DriveText => WinPath.DriveOf(Path);
    public string DepthText => $"{Depth} 层";
    public string PathLengthText => $"{Path.Length} 字符"
        + (WinPath.IsLongPath(Path) ? $"（超长路径，> {WinPath.LongPathUnsupported}）" : "");

    /// <summary>扩展名，隐藏文件（.gitignore）没有扩展名，要如实说。没有任何扩展名时显示空串而不是一个孤零零的"."。</summary>
    public string ExtensionText => Extension.Length > 1 ? Extension : "";
}

/// <summary>内容完全相同的一组文件。</summary>
public sealed class DuplicateGroup
{
    public required string DigestHex { get; init; }
    public required long Size { get; init; }
    public required IReadOnlyList<FileEntry> Members { get; init; }
    public bool Verified { get; set; }

    public int Count => Members.Count;
    public long WastedBytes => Size * (Members.Count - 1);
    public string SizeText => WinPath.FormatBytes(Size);
    public string WastedText => WinPath.FormatBytes(WastedBytes);

    public FileEntry PickKeeper(KeepRule rule)
    {
        if (Members.Count == 0) throw new InvalidOperationException("空分组");
        return rule switch
        {
            KeepRule.Newest => Members.MaxBy(m => m.LastWriteTicks)!,
            KeepRule.Oldest => Members.MinBy(m => m.LastWriteTicks)!,
            KeepRule.ShortestPath => Members.MinBy(m => (m.Path.Length, m.Path.ToLowerInvariant()))!,
            KeepRule.LongestPath => Members.MaxBy(m => (m.Path.Length, m.Path.ToLowerInvariant()))!,
            KeepRule.Shallowest => Members.MinBy(m => (m.Depth, m.Path.ToLowerInvariant()))!,
            KeepRule.Deepest => Members.MaxBy(m => (m.Depth, m.Path.ToLowerInvariant()))!,
            _ => Members[0],
        };
    }

    public (FileEntry Keeper, List<FileEntry> ToDelete) Split(KeepRule rule)
    {
        var keeper = PickKeeper(rule);
        return (keeper, Members.Where(m => !string.Equals(m.Path, keeper.Path,
            StringComparison.OrdinalIgnoreCase)).ToList());
    }
}

public sealed class ScanStats
{
    public int DirsVisited;
    public long FilesSeen;
    public long FilesKept;
    public long BytesSeen;
    public long BytesKept;
    public long BytesHashed;
    public long FilesHashed;
    public long PermissionErrors;
    public long BrokenEntries;
    public long ReparsePointsSkipped;
    public long HardLinksCollapsed;
    public long UnstableSkipped;
    public long CloudPlaceholdersSkipped;
    public long SparseSkipped;
    public long SizeCandidates;
    public long QuickCandidates;
}

public sealed class ScanConfig
{
    public List<string> Roots { get; set; } = new();
    public HashSet<string> ExcludeDirNames { get; set; } = new(StringComparer.OrdinalIgnoreCase);
    public HashSet<string> IncludeSuffixes { get; set; } = new(StringComparer.OrdinalIgnoreCase);
    public HashSet<string> ExcludeSuffixes { get; set; } = new(StringComparer.OrdinalIgnoreCase);

    public long MinSize { get; set; } = 1;
    public long? MaxSize { get; set; }
    public bool IncludeEmptyFiles { get; set; }
    public bool FollowSymlinks { get; set; }
    public bool VerifyBytes { get; set; } = true;
    /// <summary>硬链接指向同一物理文件，删一份不省空间，默认折叠。</summary>
    public bool TreatHardLinksAsOne { get; set; } = true;
    public bool SkipCloudPlaceholders { get; set; } = true;
    public long SkipSparseOver { get; set; } = 4L * 1024 * 1024 * 1024;

    public int Workers { get; set; }
    public int QuickChunk { get; set; } = 4096;
    /// <summary>1 MiB 是 hashlib/BLAKE2 释放锁拿到多线程扩展的甜点。</summary>
    public int ReadChunk { get; set; } = 1 * 1024 * 1024;
    public int InlineQuickMax { get; set; } = 128 * 1024;
    public KeepRule KeepRule { get; set; } = KeepRule.Newest;

    public int ResolvedWorkers => Workers > 0
        ? Workers
        : Math.Clamp(Environment.ProcessorCount * 2, 4, 16);

    /// <summary>默认排除：系统目录、缓存、版本库目录。</summary>
    public static IReadOnlyList<string> DefaultExcludeDirNames { get; } = new[]
    {
        "$Recycle.Bin", "System Volume Information", "Windows",
        "Program Files", "Program Files (x86)", "ProgramData",
        "node_modules", ".git", ".svn", ".hg", ".cache", ".venv", "venv",
        "__pycache__", ".Trash", ".Trashes", "$WinREAgent", "Recovery",
        "Config.Msi", "MSOCache", "PerfLogs", "Documents and Settings",
    };
}

/// <summary>不可变进度快照。</summary>
public sealed record ScanProgress
{
    public ScanPhase Phase { get; init; } = ScanPhase.Idle;
    public string Detail { get; init; } = "";
    public int Done { get; init; }
    public int Total { get; init; }
    public long BytesProcessed { get; init; }
    public double SpeedBytesPerSecond { get; init; }
    public double EtaSeconds { get; init; }

    /// <summary>
    /// 引擎的**实时**统计对象引用（不是副本）。
    /// 遍历阶段没有已知总量，界面要显示"已扫描 N 个文件 / M GB"只能靠它。
    /// 这里必须是引用而不是拷贝：进度回调是单槽覆盖写，拷贝一次就是一次分配，
    /// 而且拷贝出来的数字会比回调那一刻更旧。
    /// </summary>
    public ScanStats? LiveStats { get; init; }

    public double Fraction => Total <= 0 ? 0 : Math.Clamp((double)Done / Total, 0, 1);
    public int Percent => (int)(Fraction * 100);
}

public sealed class ScanResult
{
    public List<DuplicateGroup> Groups { get; set; } = new();
    public ScanStats Stats { get; init; } = new();
    public ScanConfig Config { get; init; } = new();
    // 这三项在流水线运行过程中会被改写，不能是 init-only
    public ScanPhase Phase { get; set; } = ScanPhase.Done;
    public string? Error { get; set; }
    public bool Cancelled { get; set; }

    public int TotalFiles => Groups.Sum(g => g.Count);
    public long ReclaimableBytes => Groups.Sum(g => g.WastedBytes);
    public string ReclaimableText => WinPath.FormatBytes(ReclaimableBytes);

    public string Summary =>
        $"重复组 {Groups.Count:N0} 个 / 涉及文件 {TotalFiles:N0} 个 / " +
        $"可释放 {ReclaimableText}；扫描 {Stats.FilesSeen:N0} 个文件" +
        $"（{WinPath.FormatBytes(Stats.BytesSeen)}），" +
        $"跳过权限受限 {Stats.PermissionErrors:N0}，" +
        $"跳过重解析点 {Stats.ReparsePointsSkipped:N0}";
}
