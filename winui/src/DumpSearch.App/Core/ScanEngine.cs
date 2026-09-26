using System.Collections.Concurrent;
using System.Diagnostics;

namespace DumpSearch.App.Core;

/// <summary>流水线编排：遍历 → 按大小分组 → 快速哈希 → 全文哈希 → 逐字节校验。</summary>
public sealed class ScanEngine
{
    private readonly ScanConfig _config;
    private readonly CancellationTokenSource _cts;
    private readonly Action<ScanProgress>? _onProgress;
    private readonly ScanStats _stats = new();

    // 单槽进度槽：生产者永不阻塞（Queue 会被高速生产打爆并把工作线程堵死）
    private readonly object _progressLock = new();
    private long _lastEmitTicks;
    private readonly Stopwatch _clock = Stopwatch.StartNew();
    private double _speed;
    private long _lastSpeedBytes;
    private long _lastSpeedTicks;
    private long _avgSize = 1;

    public ScanEngine(ScanConfig config,
        CancellationTokenSource? cts = null,
        Action<ScanProgress>? onProgress = null)
    {
        _config = config;
        _cts = cts ?? new CancellationTokenSource();
        _onProgress = onProgress;
    }

    public ScanResult Run()
    {
        var result = new ScanResult { Config = _config, Stats = _stats };
        try { return RunCore(result); }
        catch (OperationCanceledException)
        {
            result.Phase = ScanPhase.Cancelled;
            result.Cancelled = true;
            return result;
        }
        catch (Exception ex)
        {
            result.Phase = ScanPhase.Failed;
            result.Error = $"{ex.GetType().Name}: {ex.Message}";
            Emit(new ScanProgress { Phase = ScanPhase.Failed, Detail = result.Error }, true);
            return result;
        }
    }

    private ScanResult RunCore(ScanResult result)
    {
        var token = _cts.Token;

        // ---- 阶段 0/1：遍历 + 按大小分组 ----
        EmitNow(new ScanProgress { Phase = ScanPhase.Scanning, Detail = "开始遍历" });
        var scanner = new Scanner(_config, _stats, token);
        var groups = Scanner.GroupBySize(scanner.EnumerateFiles(_config.Roots));
        token.ThrowIfCancellationRequested();

        // 硬链接折叠：同一物理文件不是"重复副本"，删一份不省空间。
        if (_config.TreatHardLinksAsOne && groups.Count > 0)
        {
            EmitNow(new ScanProgress
            {
                Phase = ScanPhase.Grouping,
                Detail = $"检查 {groups.Count:N0} 组候选中的硬链接",
            });
            groups = Scanner.CollapseHardLinks(groups, _stats, token);
        }
        token.ThrowIfCancellationRequested();

        _stats.SizeCandidates = groups.Sum(g => (long)g.Count);
        var candidates = groups.SelectMany(g => g).ToList();
        groups.Clear();

        if (candidates.Count == 0)
        {
            result.Phase = ScanPhase.Done;
            EmitNow(new ScanProgress { Phase = ScanPhase.Done, Detail = "未发现重复文件" });
            return result;
        }

        _avgSize = Math.Max(1, _stats.BytesKept / Math.Max(1, _stats.FilesKept));

        // ---- 阶段 2：快速哈希 ----
        EmitNow(new ScanProgress
        {
            Phase = ScanPhase.QuickHash,
            Detail = $"{candidates.Count:N0} 个候选文件（大小相同）",
            Total = candidates.Count,
        });
        var quick = RunParallel(candidates, token,
            f => Hasher.QuickDigest(f, _config.QuickChunk, _config.InlineQuickMax),
            (count, size) =>
            {
                _stats.FilesHashed++;
                _stats.BytesHashed += Math.Min(size, _config.QuickChunk * 3L);
                Emit(new ScanProgress { Phase = ScanPhase.QuickHash, Done = count,
                    Total = candidates.Count });
            });
        token.ThrowIfCancellationRequested();

        // 摘要与文件按下标一一对应；null = 读取出错或读取期间被改写
        var quickGroups = GroupByDigest(candidates, quick).Values
            .Where(v => v.Count >= MinGroupSize).ToList();
        _stats.QuickCandidates = quickGroups.Sum(g => (long)g.Count);

        if (quickGroups.Count == 0)
        {
            result.Phase = ScanPhase.Done;
            EmitNow(new ScanProgress { Phase = ScanPhase.Done, Detail = "快速哈希后已无候选" });
            return result;
        }

        // ---- 阶段 3：全文哈希 ----
        var fullCandidates = quickGroups.SelectMany(g => g).ToList();
        EmitNow(new ScanProgress
        {
            Phase = ScanPhase.FullHash,
            Detail = $"{fullCandidates.Count:N0} 个文件需要读全文",
            Total = fullCandidates.Count,
        });
        var full = RunParallel(fullCandidates, token,
            f => Hasher.FullDigest(f, _config.ReadChunk),
            (count, size) =>
            {
                _stats.FilesHashed++;
                _stats.BytesHashed += size;
                Emit(new ScanProgress { Phase = ScanPhase.FullHash, Done = count,
                    Total = fullCandidates.Count });
            });
        token.ThrowIfCancellationRequested();

        var duplicateGroups = GroupByDigest(fullCandidates, full)
            .Where(kv => kv.Value.Count >= MinGroupSize)
            .Select(kv => new DuplicateGroup
            {
                DigestHex = kv.Key,
                Size = kv.Value[0].Size,
                Members = kv.Value,
            })
            .ToList();

        // ---- 阶段 4：逐字节校验 ----
        if (_config.VerifyBytes && duplicateGroups.Count > 0)
        {
            EmitNow(new ScanProgress
            {
                Phase = ScanPhase.Verify,
                Detail = $"校验 {duplicateGroups.Count:N0} 个重复组",
                Total = duplicateGroups.Count,
            });
            duplicateGroups = VerifyGroups(duplicateGroups, token);
        }

        duplicateGroups.Sort((a, b) => b.WastedBytes.CompareTo(a.WastedBytes));
        result.Groups = duplicateGroups;
        result.Phase = token.IsCancellationRequested ? ScanPhase.Cancelled : ScanPhase.Done;
        EmitNow(new ScanProgress
        {
            Phase = result.Phase, Detail = result.Summary, Done = 1, Total = 1,
        });
        return result;
    }

    /// <summary>
    /// 把摘要数组按值分组，并按下标映射回文件。
    /// 两个数组等长且同序；null 项（读取失败 / 文件被改写）直接丢弃并计数。
    /// </summary>
    private const int MinGroupSize = 2;

    private static Dictionary<string, List<FileEntry>> GroupByDigest(
        List<FileEntry> items, byte[]?[] digests)
    {
        var map = new Dictionary<string, List<FileEntry>>(StringComparer.Ordinal);
        int count = Math.Min(items.Count, digests.Length);
        for (int i = 0; i < count; i++)
        {
            var digest = digests[i];
            if (digest is null) continue;
            string hex = Convert.ToHexString(digest);
            if (!map.TryGetValue(hex, out var list))
                map[hex] = list = new List<FileEntry>();
            list.Add(items[i]);
        }
        return map;
    }

    private List<DuplicateGroup> VerifyGroups(List<DuplicateGroup> groups,
        CancellationToken token)
    {
        var verified = new List<DuplicateGroup>(groups.Count);
        int processed = 0;
        var options = new ParallelOptions
        {
            MaxDegreeOfParallelism = _config.ResolvedWorkers,
            CancellationToken = token,
        };
        try
        {
            Parallel.ForEach(groups, options, (group, _) =>
            {
                var keep = new List<FileEntry> { group.Members[0] };
                foreach (var other in group.Members.Skip(1))
                {
                    if (token.IsCancellationRequested) return;
                    try
                    {
                        if (Hasher.FilesIdentical(group.Members[0], other, _config.ReadChunk))
                            keep.Add(other);
                    }
                    catch (IOException) { }
                    catch (UnauthorizedAccessException) { }
                }
                if (keep.Count >= _config.MinFilesPerGroup())
                    verified.Add(new DuplicateGroup
                    {
                        DigestHex = group.DigestHex,
                        Size = group.Size,
                        Members = keep,
                        Verified = true,
                    });
                int done = Interlocked.Increment(ref processed);
                Emit(new ScanProgress { Phase = ScanPhase.Verify, Done = done,
                    Total = groups.Count });
            });
        }
        catch (OperationCanceledException) { }
        return verified.OrderByDescending(g => g.WastedBytes).ToList();
    }

    /// <summary>带在途上限的并行映射，支持中途取消。</summary>
    /// <remarks>
    /// 委托签名是 <c>Func&lt;TItem, TResult?&gt;</c> 而不是 <c>Func&lt;TItem, TResult&gt;</c>：
    /// 实际工作函数（QuickDigest / FullDigest）会用 null 表示"读失败或读取期间
    /// 被改写"，而 <c>where TResult : class</c> 约束的是**非空**引用类型，
    /// 写成后者会让 byte[]? 无法作为类型实参。
    /// </remarks>
    private TResult?[] RunParallel<TItem, TResult>(
        List<TItem> items, CancellationToken token, Func<TItem, TResult?> work,
        Action<int, long> onDone) where TResult : class where TItem : notnull
    {
        var results = new TResult?[items.Count];
        if (items.Count == 0) return results;

        int workers = _config.ResolvedWorkers;
        int next = 0, completed = 0;
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(token);
        var options = new ParallelOptions
        {
            MaxDegreeOfParallelism = workers,
            CancellationToken = cts.Token,
        };

        try
        {
            // 用 Interlocked 抢任务，避免一次性提交几十万任务
            Parallel.For(0, workers, options, _ =>
            {
                while (!cts.Token.IsCancellationRequested)
                {
                    int index = Interlocked.Increment(ref next) - 1;
                    if (index >= items.Count) return;
                    try { results[index] = work(items[index]); }
                    catch (IOException) { }
                    catch (UnauthorizedAccessException) { }
                    catch (ArgumentException) { }
                    int done = Interlocked.Increment(ref completed);
                    onDone(done, GetSize(items[index]));
                }
            });
        }
        catch (OperationCanceledException) { }
        return results;
    }

    private static long GetSize<TItem>(TItem item) where TItem : notnull => item switch
    {
        FileEntry f => f.Size,
        _ => 0,
    };

    private void Emit(ScanProgress p, bool force = false)
    {
        if (_onProgress is null) return;
        long now = Stopwatch.GetTimestamp();
        if (!force && now - Interlocked.Read(ref _lastEmitTicks) < 150_000) return;
        Interlocked.Exchange(ref _lastEmitTicks, now);
        // 带上实时统计引用，界面才能在遍历阶段显示"已扫描 N 个文件 / M GB"
        try { _onProgress(p with { LiveStats = _stats }); } catch { /* UI 未就绪时忽略 */ }
    }

    private void EmitNow(ScanProgress p)
    {
        if (_onProgress is null) return;
        try
        {
            _onProgress(p with { SpeedBytesPerSecond = _speed, LiveStats = _stats });
        }
        catch { }
    }

    public void UpdateSpeed(long bytesProcessed, int done, int total)
    {
        long now = Stopwatch.GetTimestamp();
        long dt = now - _lastSpeedTicks;
        if (dt >= Stopwatch.Frequency / 4)
        {
            double rate = (bytesProcessed - _lastSpeedBytes) / (double)dt * Stopwatch.Frequency;
            _speed = _speed == 0 ? rate : _speed * 0.4 + rate * 0.6;
            _lastSpeedBytes = bytesProcessed;
            _lastSpeedTicks = now;
        }
    }
}

internal static class ScanConfigExtensions
{
    public static int MinFilesPerGroup(this ScanConfig _) => 2;
}
