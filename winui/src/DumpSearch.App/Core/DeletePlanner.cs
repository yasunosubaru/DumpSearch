namespace DumpSearch.App.Core;

/// <summary>一份可审阅的删除清单。构造它不碰文件系统。</summary>
public sealed class DeletePlan
{
    public KeepRule Rule { get; init; } = KeepRule.Newest;
    public List<PlanItem> Items { get; } = new();
    public int GroupCount { get; set; }

    /// <summary>其中有多少组的保留项是用户手动指定的（而非按全局规则）。</summary>
    public int CustomKeeperCount { get; set; }

    public int FileCount => Items.Count;
    public long Reclaimable => Items.Sum(i => i.Entry.Size);
    public string ReclaimableText => WinPath.FormatBytes(Reclaimable);
    public string Summary =>
        $"将把 {FileCount:N0} 个重复副本移入回收站，涉及 {GroupCount:N0} 个重复组，" +
        $"预计释放 {ReclaimableText}。";

    public IReadOnlyList<string> Targets => Items.Select(i => i.Entry.Path).ToList();

    public IEnumerable<string> PreviewLines(int limit = 200)
    {
        int i = 0;
        foreach (var item in Items)
        {
            if (i++ >= limit) break;
            yield return $"删除  {item.Entry.SizeText,10}  {item.Entry.Path}";
            yield return $"保留  {"",10}  {item.Keeper.Path}";
        }
        if (FileCount > limit)
            yield return $"... 另有 {FileCount - limit:N0} 项未显示";
    }
}

public sealed record PlanItem(string GroupDigest, FileEntry Keeper, FileEntry Entry);

public static class DeletePlanner
{
    /// <summary>按保留规则，从已勾选的组生成删除计划。</summary>
    public static DeletePlan Build(IEnumerable<DuplicateGroup> groups,
        ISet<string> checkedGroupIds, KeepRule rule) =>
        Build(groups, checkedGroupIds, rule, null);

    /// <summary>
    /// 生成删除计划，<paramref name="keepers"/> 可以逐组覆盖保留规则。
    ///
    /// 为什么要覆盖：全局规则（最新/最旧/最短路径…）只能按一个维度猜，
    /// 而"这个副本我要留"是逐组的、基于用户对具体位置和时间的判断。
    /// 键是组摘要，值是被指定保留的文件路径。
    ///
    /// 三条安全规则，任何一条不满足都**退回全局规则**而不是照做：
    /// 1. 指定的文件必须真的在这个组里（防止界面传错）；
    /// 2. 组里必须还剩至少一个待删项（否则等于什么都没做）；
    /// 3. 路径按不区分大小写比较（Windows 上同一文件大小写可变）。
    /// </summary>
    public static DeletePlan Build(IEnumerable<DuplicateGroup> groups,
        ISet<string> checkedGroupIds, KeepRule rule,
        IReadOnlyDictionary<string, string>? keepers)
    {
        var plan = new DeletePlan { Rule = rule };
        foreach (var group in groups)
        {
            if (!checkedGroupIds.Contains(group.DigestHex)) continue;
            if (group.Count < 2) continue;

            FileEntry? keeper = null;
            if (keepers is not null
                && keepers.TryGetValue(group.DigestHex, out var wanted)
                && !string.IsNullOrWhiteSpace(wanted))
            {
                keeper = group.Members.FirstOrDefault(m =>
                    string.Equals(m.Path, wanted, StringComparison.OrdinalIgnoreCase));
            }
            keeper ??= group.PickKeeper(rule);

            var toDelete = group.Members
                .Where(m => !string.Equals(m.Path, keeper.Path,
                    StringComparison.OrdinalIgnoreCase))
                .ToList();
            if (toDelete.Count == 0) continue;

            plan.GroupCount++;
            if (keepers is not null
                && keepers.TryGetValue(group.DigestHex, out var w2)
                && string.Equals(keeper.Path, w2, StringComparison.OrdinalIgnoreCase))
                plan.CustomKeeperCount++;

            foreach (var member in toDelete)
                plan.Items.Add(new PlanItem(group.DigestHex, keeper, member));
        }
        return plan;
    }

    /// <summary>
    /// 删除前静态审计：把用户最容易事后后悔的情况提前指出来。
    /// </summary>
    public static IReadOnlyList<string> Audit(DeletePlan plan)
    {
        var warnings = new List<string>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var duplicates = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        foreach (var item in plan.Items)
        {
            if (!seen.Add(item.Entry.Path)) duplicates.Add(item.Entry.Path);
            if (string.Equals(item.Entry.Path, item.Keeper.Path,
                    StringComparison.OrdinalIgnoreCase))
                warnings.Add($"内部错误：保留项与待删项相同 -> {item.Entry.Path}");
        }
        if (duplicates.Count > 0)
            warnings.Add($"清单中有 {duplicates.Count} 条重复路径，将被去重后再执行");

        if (plan.Rule == KeepRule.Oldest)
            warnings.Add("保留策略为「最旧」：会优先保留旧备份、删除较新的副本");
        if (plan.Rule == KeepRule.Newest)
            warnings.Add("保留策略为「最新」：同步/更新工具产生的副本通常最新，请确认要删的正是它们");
        if (plan.CustomKeeperCount > 0)
            warnings.Add($"其中 {plan.CustomKeeperCount} 个重复组是你**手动指定**保留项的，" +
                         "不受上方保留策略影响");

        var drives = plan.Items
            .GroupBy(i => WinPath.DriveOf(i.Entry.Path), StringComparer.OrdinalIgnoreCase)
            .ToDictionary(g => g.Key, g => g.Count(), StringComparer.OrdinalIgnoreCase);
        if (drives.Count > 1)
        {
            string detail = string.Join("、",
                drives.OrderBy(k => k.Key).Select(k => $"{k.Key}({k.Value})"));
            warnings.Add($"删除会跨 {drives.Count} 个盘：{detail}（各自进入该盘回收站）");
        }

        var tiny = plan.Items.Where(i => i.Entry.Size < 1024).ToList();
        if (tiny.Count > 0)
        {
            long total = tiny.Sum(i => i.Entry.Size);
            warnings.Add($"其中 {tiny.Count:N0} 个文件小于 1 KB（合计 {WinPath.FormatBytes(total)}），" +
                         "通常是配置/占位文件，删掉省不了空间却可能弄坏程序");
        }

        var longPaths = plan.Items.Count(i => WinPath.IsLongPath(i.Entry.Path));
        if (longPaths > 0)
            warnings.Add($"{longPaths:N0} 个文件路径超过 {WinPath.LongPathUnsupported} 字符，" +
                         "将使用手写 $Recycle.Bin 记录方式入回收站（可靠性较低，失败会自动回滚）");

        if (plan.FileCount == 0) warnings.Add("没有选中任何待删除文件");
        return warnings;
    }
}
