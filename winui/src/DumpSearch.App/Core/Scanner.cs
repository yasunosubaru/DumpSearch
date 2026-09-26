namespace DumpSearch.App.Core;

/// <summary>阶段 0/1：目录遍历 + 按大小分组 + 硬链接折叠。</summary>
public sealed class Scanner
{
    private readonly ScanConfig _config;
    private readonly ScanStats _stats;
    private readonly CancellationToken _cancel;
    private readonly Action<ScanStats>? _onProgress;

    private int _sinceProgress;

    public Scanner(ScanConfig config, ScanStats stats, CancellationToken cancel,
        Action<ScanStats>? onProgress = null)
    {
        _config = config;
        _stats = stats;
        _cancel = cancel;
        _onProgress = onProgress;
    }

    private bool DirExcluded(string name) => _config.ExcludeDirNames.Contains(name);

    private bool FileAccepted(string path, string name, long size)
    {
        // 0 字节必须先判：MinSize 默认 1，若先按 MinSize 过滤，
        // IncludeEmptyFiles 永远不会生效。
        if (size == 0) return _config.IncludeEmptyFiles;
        if (size < _config.MinSize) return false;
        if (_config.MaxSize is { } max && size > max) return false;

        var lowered = name.ToLowerInvariant();
        if (_config.IncludeSuffixes.Count > 0)
        {
            bool any = false;
            foreach (var s in _config.IncludeSuffixes)
                if (lowered.EndsWith(s, StringComparison.Ordinal)) { any = true; break; }
            if (!any) return false;
        }
        if (_config.ExcludeSuffixes.Count > 0)
        {
            foreach (var s in _config.ExcludeSuffixes)
                if (lowered.EndsWith(s, StringComparison.Ordinal)) return false;
        }

        // 备用数据流（file.txt:stream）不参与比对
        var baseName = name[(name.LastIndexOfAny(new[] { '\\', '/' }) + 1)..];
        if (baseName.Contains(':')) return false;
        return true;
    }

    private static bool IsCloudPlaceholder(uint attrs) =>
        (attrs & (Win32.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
                  | Win32.FILE_ATTRIBUTE_RECALL_ON_OPEN
                  | Win32.FILE_ATTRIBUTE_OFFLINE)) != 0;

    private static bool IsSparseOrCompressed(uint attrs) =>
        (attrs & (Win32.FILE_ATTRIBUTE_SPARSE_FILE
                  | Win32.FILE_ATTRIBUTE_COMPRESSED)) != 0;

    /// <summary>遍历所有根目录，产出通过预筛的文件。</summary>
    public IEnumerable<FileEntry> EnumerateFiles(IEnumerable<string> roots)
    {
        foreach (var root in roots)
        {
            if (_cancel.IsCancellationRequested) yield break;
            string? displayRoot = WinPath.NormalizeOrNull(root);
            if (displayRoot is null) continue;

            var stack = new Stack<string>();
            stack.Push(displayRoot);

            while (stack.Count > 0)
            {
                if (_cancel.IsCancellationRequested) yield break;
                string dir = stack.Pop();

                IEnumerable<string> children;
                try
                {
                    children = Directory.EnumerateFileSystemEntries(dir);
                    // 立刻物化，避免在枚举过程中把异常抛到 yield 之后
                    children = children.ToArray();
                }
                catch (UnauthorizedAccessException) { _stats.PermissionErrors++; continue; }
                catch (DirectoryNotFoundException) { _stats.BrokenEntries++; continue; }
                catch (IOException) { _stats.PermissionErrors++; continue; }

                foreach (var child in children)
                {
                    if (_cancel.IsCancellationRequested) yield break;

                    if (!Win32.TryGetAttributes(child, out uint attrs, out long size,
                            out long ticks))
                    {
                        _stats.BrokenEntries++;
                        continue;
                    }

                    bool isDir = (attrs & Win32.FILE_ATTRIBUTE_DIRECTORY) != 0;
                    bool isReparse = (attrs & Win32.FILE_ATTRIBUTE_REPARSE_POINT) != 0;

                    if (isReparse && !_config.FollowSymlinks)
                    {
                        // 关键：不能靠 DirectoryInfo.LinkTarget 判 junction，
                        // 必须看 FILE_ATTRIBUTE_REPARSE_POINT，否则会跟着
                        // junction 一路递归下去。
                        _stats.ReparsePointsSkipped++;
                        continue;
                    }

                    if (isDir)
                    {
                        string name = Path.GetFileName(child);
                        if (string.IsNullOrEmpty(name) || DirExcluded(name)) continue;
                        stack.Push(child);
                        _stats.DirsVisited++;
                        continue;
                    }

                    _stats.FilesSeen++;
                    _stats.BytesSeen += size;

                    string fileName = Path.GetFileName(child);
                    if (string.IsNullOrEmpty(fileName)) continue;
                    if (!FileAccepted(child, fileName, size)) continue;

                    // 云端占位符：逻辑大小可用，但读内容会触发下载
                    if (_config.SkipCloudPlaceholders && IsCloudPlaceholder(attrs))
                    {
                        _stats.CloudPlaceholdersSkipped++;
                        continue;
                    }
                    // 超大稀疏/压缩文件：读一遍要数秒，且几乎不会有副本
                    if (_config.SkipSparseOver > 0 && size > _config.SkipSparseOver
                        && IsSparseOrCompressed(attrs))
                    {
                        _stats.SparseSkipped++;
                        continue;
                    }

                    _stats.FilesKept++;
                    _stats.BytesKept += size;
                    yield return new FileEntry
                    {
                        Path = WinPath.Display(child),
                        Size = size,
                        LastWriteTicks = ticks,
                    };
                }

                if (++_sinceProgress >= 400)
                {
                    _sinceProgress = 0;
                    _onProgress?.Invoke(_stats);
                }
            }
        }
    }

    /// <summary>
    /// 折叠指向同一物理文件的多个路径。
    /// </summary>
    /// <remarks>
    /// 必须放在"按大小分组之后"：遍历阶段用 GetFileAttributesEx 拿不到
    /// 文件 ID 与链接数（.NET 没有这个 API），只有 GetFileInformationByHandle 才行。
    /// 而对全盘几十万个文件都做一次额外 open 会很贵，只对"大小相同、
    /// 可能重复"的候选做，代价可忽略。
    /// </remarks>
    public static List<List<FileEntry>> CollapseHardLinks(
        List<List<FileEntry>> groups, ScanStats stats, CancellationToken cancel)
    {
        var output = new List<List<FileEntry>>(groups.Count);
        foreach (var group in groups)
        {
            if (cancel.IsCancellationRequested) return output;
            var seen = new HashSet<(uint Volume, ulong FileId)>();
            var kept = new List<FileEntry>(group.Count);
            foreach (var entry in group)
            {
                if (Win32.TryGetFileIdentity(entry.Path, out uint vol, out ulong id,
                        out uint links) && links > 1)
                {
                    if (!seen.Add((vol, id))) { stats.HardLinksCollapsed++; continue; }
                }
                kept.Add(entry);
            }
            if (kept.Count >= 2) output.Add(kept);
        }
        return output;
    }

    /// <summary>阶段 1：按大小分组。零成本的最大过滤器。</summary>
    public static List<List<FileEntry>> GroupBySize(
        IEnumerable<FileEntry> entries, int minGroup = 2)
    {
        var buckets = new Dictionary<long, List<FileEntry>>();
        foreach (var e in entries)
        {
            if (!buckets.TryGetValue(e.Size, out var list))
                buckets[e.Size] = list = new List<FileEntry>();
            list.Add(e);
        }
        return buckets.Values.Where(v => v.Count >= minGroup).ToList();
    }
}
