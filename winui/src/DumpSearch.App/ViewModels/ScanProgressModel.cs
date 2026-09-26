using System.Diagnostics;
using DumpSearch.App.Core;

namespace DumpSearch.App.ViewModels;

/// <summary>流水线阶段的状态。</summary>
public enum StageState { Pending, Active, Done, Skipped }

/// <summary>一个流水线阶段在界面上的展示项。</summary>
public sealed class StageItem
{
    public required string Name { get; init; }
    public required string Hint { get; init; }
    /// <summary>该阶段在总进度里占的权重（相对值，不必归一）。</summary>
    public required double Weight { get; init; }
    /// <summary>已知的总量；<= 0 表示总量未知（只能显示计数，不能算百分比）。</summary>
    public int Total { get; set; }
    public int Done { get; set; }
    public StageState State { get; set; } = StageState.Pending;

    public double Fraction => Total <= 0
        ? (State == StageState.Done ? 1 : 0)
        : Math.Clamp((double)Done / Total, 0, 1);

    public bool HasKnownTotal => Total > 0;

    /// <summary>阶段计数文案：总量未知时只报已完成数，不编造百分比。</summary>
    public string CountText
    {
        get
        {
            if (State == StageState.Skipped) return "已跳过";
            if (State == StageState.Pending) return HasKnownTotal ? $"0 / {Total:N0}" : "";
            if (State == StageState.Done)
                return HasKnownTotal ? $"{Total:N0} / {Total:N0}" : $"{Done:N0}";
            return HasKnownTotal ? $"{Done:N0} / {Total:N0}" : $"{Done:N0}";
        }
    }

    public string PercentText => HasKnownTotal && State != StageState.Skipped
        ? $"{(int)Math.Round(Fraction * 100)}%" : "";
}

/// <summary>
/// 扫描进度的界面模型。
///
/// 为什么要单独一层：引擎只吐「当前阶段 + 计数」，而界面要回答三个问题——
/// 现在到哪了（阶段步进器）、整体完成多少（加权总进度）、扫得怎么样（实时统计）。
/// 直接把 <see cref="ScanProgress"/> 绑到界面上的问题是，遍历阶段总量根本未知，
/// 硬算百分比就是骗人。
///
/// 所以这里的做法是：
/// - 阶段步进器如实显示 5 个阶段各自的 pending/active/done；
/// - **只有总量已知的阶段才显示百分比**，总量未知就只显示计数；
/// - 「遍历」阶段用「已读字节 / 所选卷总容量」估算，这是有依据的估算
///   （GetDiskFreeSpaceEx 给的容量），并在界面上标注是估算而不是精确值。
/// </summary>
public sealed class ScanProgressModel
{
    /// <summary>
    /// 各阶段权重。遍历最重（全盘 IO），校验最轻（只读已命中的组）。
    /// 权重不需要归一，<see cref="OverallFraction"/> 会自己算。
    /// </summary>
    private static readonly (string Name, string Hint, double Weight)[] StageDefs =
    {
        ("遍历目录", "枚举文件、套用筛选", 40),
        ("折叠硬链接", "识别同一物理文件",  5),
        ("快速哈希", "按大小分组后采样比对", 25),
        ("完整哈希", "读全文算出摘要",      20),
        ("逐字节校验", "确认内容真的相同",  10),
    };

    private readonly List<StageItem> _stages = new();
    private readonly Stopwatch _clock = Stopwatch.StartNew();

    public ScanProgressModel()
    {
        foreach (var d in StageDefs)
            _stages.Add(new StageItem { Name = d.Name, Hint = d.Hint, Weight = d.Weight });
        // 界面默认不显示，等真的开始扫描再 ShowEmptyProgress
        Reset();
    }

    public IReadOnlyList<StageItem> Stages => _stages;

    public StageItem Traversal => _stages[0];
    public StageItem HardLink => _stages[1];
    public StageItem QuickHash => _stages[2];
    public StageItem FullHash => _stages[3];
    public StageItem Verify => _stages[4];

    /// <summary>本次扫描所涉卷的总容量，用来给遍历阶段做估算。</summary>
    public long VolumeTotalBytes { get; set; }

    public long BytesSeen { get; set; }
    public long FilesSeen { get; set; }
    public long DirsVisited { get; set; }
    public double SpeedBytesPerSec { get; set; }
    public long ElapsedSeconds => (long)_clock.Elapsed.TotalSeconds;

    public string CurrentStageName { get; set; } = "";
    public string CurrentStageDetail { get; set; } = "";

    public string ElapsedText
    {
        get
        {
            var t = _clock.Elapsed;
            return t.TotalHours >= 1
                ? $"{(int)t.TotalHours}:{t.Minutes:00}:{t.Seconds:00}"
                : $"{t.Minutes:00}:{t.Seconds:00}";
        }
    }

    public string SpeedText => SpeedBytesPerSec > 0
        ? WinPath.FormatBytes((long)SpeedBytesPerSec) + "/s"
        : "-";

    public string BytesSeenText => WinPath.FormatBytes(BytesSeen);
    public string FilesSeenText => FilesSeen.ToString("N0");
    public string DirsSeenText => DirsVisited.ToString("N0");

    /// <summary>遍历阶段的估算进度（0..1）。容量未知时返回 -1 表示无法估算。</summary>
    public double TraversalFraction
    {
        get
        {
            if (VolumeTotalBytes <= 0) return -1;
            return Math.Clamp((double)BytesSeen / VolumeTotalBytes, 0, 1);
        }
    }

    /// <summary>
    /// 总进度 = 各阶段权重加权和。
    /// 遍历阶段总量未知，用容量估算值参与计算（拿不到容量就按"进行中"给 0，
    /// 那时总进度会停在低位不动——这比编一个假百分比诚实）。
    /// </summary>
    public double OverallFraction
    {
        get
        {
            double total = 0, acc = 0;
            foreach (var s in _stages)
            {
                total += s.Weight;
                double f = s.State switch
                {
                    StageState.Done or StageState.Skipped => 1,
                    StageState.Active => s == Traversal
                        ? Math.Max(0, TraversalFraction)
                        : s.Fraction,
                    _ => 0,
                };
                acc += s.Weight * Math.Clamp(f, 0, 1);
            }
            return total <= 0 ? 0 : Math.Clamp(acc / total, 0, 1);
        }
    }

    public string OverallPercentText =>
        $"{(int)Math.Round(OverallFraction * 100)}%";

    /// <summary>遍历阶段用的是容量估算，界面上要说明白，不能让人以为是精确值。</summary>
    public string TraversalEstimateNote => TraversalFraction >= 0
        ? "按已读字节 / 卷容量估算"
        : "按已读字节 / 卷容量估算（卷容量未知，进度仅供参考）";

    public void Reset()
    {
        _clock.Restart();
        foreach (var s in _stages)
        {
            s.State = StageState.Pending;
            s.Done = 0;
            s.Total = 0;
        }
        BytesSeen = 0;
        FilesSeen = 0;
        DirsVisited = 0;
        SpeedBytesPerSec = 0;
        CurrentStageName = "";
        CurrentStageDetail = "";
    }

    /// <summary>把引擎的一次进度快照合并进界面状态。</summary>
    public void Apply(ScanProgress p, ScanStats stats)
    {
        BytesSeen = stats.BytesSeen;
        FilesSeen = stats.FilesSeen;
        DirsVisited = stats.DirsVisited;

        switch (p.Phase)
        {
            case ScanPhase.Scanning:
                SetActive(Traversal);
                CurrentStageName = "遍历目录";
                CurrentStageDetail = $"已枚举 {stats.FilesSeen:N0} 个文件";
                // 遍历没有"总量"，Total 保持 0，界面就只显示计数不算百分比
                break;

            case ScanPhase.Grouping:
                SetActive(HardLink);
                CurrentStageName = "折叠硬链接";
                CurrentStageDetail = p.Detail;
                break;

            case ScanPhase.QuickHash:
                QuickHash.Total = p.Total;
                QuickHash.Done = p.Done;
                SetActive(QuickHash);
                CurrentStageName = "快速哈希";
                CurrentStageDetail = p.Total > 0
                    ? $"{p.Done:N0} / {p.Total:N0} 个候选"
                    : p.Detail;
                break;

            case ScanPhase.FullHash:
                FullHash.Total = p.Total;
                FullHash.Done = p.Done;
                SetActive(FullHash);
                CurrentStageName = "完整哈希";
                CurrentStageDetail = p.Total > 0
                    ? $"{p.Done:N0} / {p.Total:N0} 个文件"
                    : p.Detail;
                break;

            case ScanPhase.Verify:
                Verify.Total = p.Total;
                Verify.Done = p.Done;
                SetActive(Verify);
                CurrentStageName = "逐字节校验";
                CurrentStageDetail = p.Total > 0
                    ? $"{p.Done:N0} / {p.Total:N0} 个重复组"
                    : p.Detail;
                break;

            case ScanPhase.Done:
            case ScanPhase.Cancelled:
                foreach (var s in _stages)
                    if (s.State == StageState.Active || s.State == StageState.Pending)
                        s.State = StageState.Done;
                CurrentStageName = p.Phase == ScanPhase.Done ? "完成" : "已取消";
                CurrentStageDetail = p.Detail;
                break;

            case ScanPhase.Failed:
                CurrentStageName = "出错";
                CurrentStageDetail = p.Detail;
                break;
        }
    }

    /// <summary>标记某阶段之前的阶段都已完成，之后的都还没开始。</summary>
    private void SetActive(StageItem current)
    {
        foreach (var s in _stages)
        {
            if (ReferenceEquals(s, current))
            {
                if (s.State == StageState.Pending) s.State = StageState.Active;
                continue;
            }
            if (s.State == StageState.Active) s.State = StageState.Done;
        }
        // 当前阶段之前的全部置为完成
        bool seen = false;
        foreach (var s in _stages)
        {
            if (ReferenceEquals(s, current)) { seen = true; continue; }
            if (!seen && s.State == StageState.Pending) s.State = StageState.Done;
        }
    }

    /// <summary>把「本阶段已无候选，跳过」标出来，而不是显示成一个走不完的进度条。</summary>
    public void MarkSkipped(StageItem stage, string reason)
    {
        if (stage.State is StageState.Done or StageState.Skipped) return;
        stage.State = StageState.Skipped;
        stage.Done = 0;
        stage.Total = 0;
        CurrentStageDetail = reason;
    }
}
