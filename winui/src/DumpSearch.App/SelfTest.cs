using System.Runtime.InteropServices;
using DumpSearch.App.Core;

namespace DumpSearch.App;

/// <summary>
/// 命令行自检：不开窗口，直接跑完整流水线并断言结果。
///
/// 存在的理由：WinUI 界面的自动化点击测试成本很高，而真正需要被反复
/// 验证的是**引擎**（分层过滤 / 硬链接折叠 / 长路径 / 删除计划 / 审计）。
/// 这里把引擎的每条安全性质都变成可执行断言，全程**不做任何删除**。
///
/// 两个 WinExe 的坑（都实测踩过）：
/// 1. 默认没有控制台。必须 AttachConsole 接管父进程的控制台才有输出。
/// 2. **不要碰 Console.OutputEncoding** —— 无控制台时设置它会让 CLR
///    直接 failfast（0xC0000409），表现为进程无消息崩溃。
///    所以改用 StringBuilder 收集 + 同时写日志文件。
/// </summary>
public static class SelfTest
{
    private const int ATTACH_PARENT_PROCESS = -1;

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AttachConsole(int dwProcessId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AllocConsole();

    private static readonly System.Text.StringBuilder Log = new();
    private static int _passed;
    private static readonly List<string> Failures = new();

    private static void Emit(string line = "")
    {
        Log.AppendLine(line);
        try
        {
            Console.Out.WriteLine(line);
            // 立刻冲刷：本项目是 WinExe，一旦后面崩了，缓冲区里的内容会**全部丢失**，
            // 表现就是"跑崩了但一行输出都没有"，根本没法定位。
            Console.Out.Flush();
        }
        catch { /* 无控制台时忽略 */ }
    }

    private static void Check(bool condition, string what)
    {
        if (condition) { _passed++; Emit($"  PASS  {what}"); }
        else { Failures.Add(what); Emit($"  FAIL  {what}"); }
    }

    public static int Run(string root)
    {
        try
        {
            return RunCore(root);
        }
        catch (Exception ex)
        {
            // 自检本身是给人看的工具，崩在断言里必须说清楚是哪一条，
            // 而不是留下一个 0xC0000405 的无消息退出码让人猜。
            Emit();
            Emit("!! 自检过程中抛出未捕获异常 —— 这是自检代码本身的 bug");
            Emit($"   {ex.GetType().Name}: {ex.Message}");
            Emit(ex.StackTrace ?? "   （无堆栈）");
            Emit();
            Emit($"已完成的断言: {_passed} 项");
            return 3;
        }
    }

    private static int RunCore(string root)
    {
        if (!AttachConsole(ATTACH_PARENT_PROCESS)) AllocConsole();
        try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { /* 见类注释 */ }
        try { Console.InputEncoding = System.Text.Encoding.UTF8; } catch { }

        _passed = 0;
        Failures.Clear();
        Log.Clear();

        Emit("Dump Search 引擎自检");
        Emit($"  被测目录: {root}");
        Emit($"  时间:     {DateTime.Now:yyyy-MM-dd HH:mm:ss}");
        Emit();

        if (!Directory.Exists(root))
        {
            Emit($"目录不存在: {root}");
            return 2;
        }

        // ---- 0. 长路径可达性（单独确认，不依赖是否构成重复组）----
        Emit("[0] 长路径可达性");
        var longOnDisk = new List<string>();
        WalkForLongPaths(root, longOnDisk, 0);
        Emit($"        磁盘上 >260 字符的文件: {longOnDisk.Count} 个");
        foreach (var p in longOnDisk.Take(3))
            Emit($"          {p.Length} 字符  可打开={CanOpen(p)}");

        int readable = longOnDisk.Count(CanOpen);
        Check(longOnDisk.Count > 0, "被测目录里存在超长路径");
        Check(readable == longOnDisk.Count,
            $"超长路径全部可读（{readable}/{longOnDisk.Count}）——"
            + "读不了就等于扫不到，是硬伤");
        Emit();

        // ---- 1. 端到端扫描 ----
        var config = new ScanConfig
        {
            Roots = new List<string> { root },
            ExcludeDirNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase),
            VerifyBytes = true,
            TreatHardLinksAsOne = true,
        };
        int progressCount = 0;
        var result = new ScanEngine(config, onProgress: _ => Interlocked.Increment(ref progressCount)).Run();

        Emit("[1] 端到端扫描");
        Check(result.Phase == ScanPhase.Done, $"扫描完成（实际 {result.Phase}）");
        if (result.Error is not null) Emit($"        错误: {result.Error}");
        Check(result.Groups.Count > 0, $"发现重复组 {result.Groups.Count} 个");
        Check(result.Groups.All(g => g.Count >= 2), "每组至少 2 个成员");
        Check(result.Groups.All(g => g.Verified), "所有分组都通过逐字节校验");
        Check(result.Groups.All(g => g.Members.Select(m => m.Size).Distinct().Count() == 1),
            "同组成员大小一致");
        Check(result.Groups.All(g =>
                g.Members.Select(m => m.Path).Distinct().Count() == g.Count),
            "同组成员路径不重复");
        Check(progressCount > 0, $"进度回调有推送（{progressCount} 次）");
        long longPath = result.Groups.SelectMany(g => g.Members)
            .Count(m => WinPath.IsLongPath(m.Path));
        Emit($"        其中超长路径(>260 字符)成员 {longPath} 个");
        Check(longPath > 0, "覆盖到超长路径（>260 字符）");
        Emit();

        // ---- 2. 保留策略 ----
        Emit("[2] 保留策略（每组恰好保留 1 个）");
        foreach (var rule in Enum.GetValues<KeepRule>())
        {
            var ok = result.Groups.All(g =>
            {
                var (keeper, rest) = g.Split(rule);
                return rest.Count == g.Count - 1 && !rest.Any(m => m.Path == keeper.Path);
            });
            Check(ok, $"{KeepRuleNames.Label(rule)}：保留项不在待删列表里");
        }
        Emit();

        // ---- 3. 删除计划与审计（只算不删）----
        Emit("[3] 删除计划（只生成清单，绝不执行）");
        var all = result.Groups.Select(g => g.DigestHex).ToHashSet();
        var plan = DeletePlanner.Build(result.Groups, all, KeepRule.Newest);
        Check(plan.FileCount > 0, $"计划包含 {plan.FileCount} 个待删项");
        Check(plan.FileCount == plan.Targets.Distinct().Count(), "待删路径无重复");
        Check(plan.Reclaimable > 0, $"可释放 {plan.ReclaimableText}");
        Check(plan.Items.All(i => i.Entry.Path != i.Keeper.Path), "待删项都不等于保留项");

        var none = DeletePlanner.Build(result.Groups, new HashSet<string>(), KeepRule.Newest);
        Check(none.FileCount == 0, "未勾选任何组时计划为空");

        var warnings = DeletePlanner.Audit(plan);
        Emit($"        审计提醒 {warnings.Count} 条：");
        foreach (var w in warnings) Emit($"          - {w}");
        Check(warnings.Count > 0, "审计能给出提醒");
        Check(plan.Targets.Count == info_TargetsStillExist(plan), "待删目标当前都存在（尚未执行删除）");
        Emit();

        // ---- 4. 视图模型 ----
        Emit("[4] 结果视图：过滤 / 排序 / 勾选");
        var vm = new ViewModels.ResultViewModel();
        vm.Load(result);
        var total = vm.TotalGroupCount;
        Check(total > 0, $"载入 {total} 个组");
        vm.SetMinGroupSize(long.MaxValue / 2);
        Check(vm.VisibleGroupCount < total, "按最小体积收窄生效");
        vm.SetMinGroupSize(0);
        Check(vm.VisibleGroupCount == total, "放宽后恢复全集（不能在已过滤视图上二次过滤）");
        vm.SetFilter("name:__不存在的名字__");
        Check(vm.VisibleGroupCount == 0, "不匹配的筛选返回 0 组");
        vm.SetFilter("");
        Check(vm.VisibleGroupCount == total, "清空筛选恢复全集");
        foreach (var mode in new[] { "reclaimable", "size", "count", "name", "path", "mtime" })
        {
            vm.SetSort(mode);
            Check(vm.VisibleGroupCount == total, $"排序 {mode} 不丢组");
        }
        vm.CheckNone();
        Check(vm.BuildPlan().FileCount == 0, "全不选时计划为空");
        vm.CheckAll();
        Check(vm.BuildPlan().FileCount == plan.FileCount, "全选时计划与直接构建一致");
        vm.Invert();
        Check(vm.BuildPlan().FileCount == 0, "反选后计划为空");

        // 路径列精简：必须逐段求公共前缀。曾经的实现拿第一个目录当基准，
        // 遇到兄弟目录（…\docs\a 与 …\data）算出来是空串，路径列退化成全路径。
        var row = vm.Rows.FirstOrDefault(r => r.Kind == ViewModels.RowKind.File);
        if (row is not null)
        {
            Emit($"        公共前缀 = '{vm.SharedDirPrefix}'");
            Emit($"        显示路径 = {row.PathText}");
            Check(vm.SharedDirPrefix.Length > 0, "能算出公共目录前缀");
            Check(row.PathText.StartsWith("…\\"), "路径列剥掉了公共前缀");
            Check(row.PathText.Length < row.FullPathText.Length,
                "精简后的路径比完整路径短");
            Check(row.FullPathText.Length > 0, "完整路径仍然保留（tooltip 用）");
        }
        Emit();

        // ---- 5. 统计 ----
        Emit("[5] 扫描统计");
        var s = result.Stats;
        Emit($"        目录 {s.DirsVisited:N0} / 文件 {s.FilesSeen:N0} " +
             $"({WinPath.FormatBytes(s.BytesSeen)})");
        Emit($"        权限受限 {s.PermissionErrors:N0} / 重解析点 {s.ReparsePointsSkipped:N0} " +
             $"/ 硬链接折叠 {s.HardLinksCollapsed:N0} / 云占位符 {s.CloudPlaceholdersSkipped:N0}");
        Emit($"        枚举失败 {s.BrokenEntries:N0} / 读取期间被改写 {s.UnstableSkipped:N0}");
        Check(s.FilesSeen > 0, "统计到文件");
        Check(s.BrokenEntries == 0,
            $"枚举无失败项（当前 {s.BrokenEntries}）——有值通常意味着长路径没打开");

        // 修改时间：曾经因为 FILETIME 换算多乘了 100 而显示成 7274 年，
        // 所以拿磁盘上的真实时间做交叉验证。
        var probe = result.Groups.SelectMany(g => g.Members)
            .OrderByDescending(m => m.LastWriteTicks).FirstOrDefault();
        if (probe is not null)
        {
            var shown = WinPath.FormatTimestamp(probe.LastWriteTicks);
            var real = new DateTime(
                DateTime.FromFileTime(probe.LastWriteTicks).ToLocalTime().Ticks);
            var expected = new DateTime(
                new FileInfo(probe.Path).LastWriteTime.Ticks);
            Emit($"        最新成员显示为 {shown}（磁盘 {expected:yyyy-MM-dd HH:mm}）");
            Check(Math.Abs((real - expected).TotalMinutes) < 2,
                "显示的修改时间与磁盘一致（误差 < 2 分钟）");
            Check(shown != "-" && !shown.StartsWith("727"),
                $"修改时间格式正常（{shown}）");
        }
        Emit();

        // ---- 6. 进度模型 ----
        Emit("[6] 扫描进度模型");
        var pm = new ViewModels.ScanProgressModel();
        Check(pm.Stages.Count == 5, $"定义了 5 个阶段（实际 {pm.Stages.Count}）");
        Check(Math.Abs(pm.OverallFraction) < 0.0001, "初始总进度为 0%");
        Check(pm.TraversalFraction < 0, "卷容量未知时遍历进度返回 -1 而不是假装 0");

        // 总量未知的阶段不能编造百分比——这是这套模型的核心约束
        pm.Traversal.Total = 0;
        pm.Traversal.State = ViewModels.StageState.Active;
        Check(pm.Traversal.CountText == "0", "总量未知时只报计数，不显示 '0 / 0'");
        Check(pm.Traversal.PercentText == "", "总量未知时不给百分比");

        // 已知总量的阶段要能算百分比
        pm.QuickHash.Total = 1000;
        pm.QuickHash.Done = 250;
        pm.QuickHash.State = ViewModels.StageState.Active;
        Check(pm.QuickHash.CountText == "250 / 1,000", "计数文案带千分位");
        Check(pm.QuickHash.PercentText == "25%", $"百分比计算正确（{pm.QuickHash.PercentText}）");

        // 总进度必须单调递增，且全部完成时正好 100%
        double prev = -1;
        bool monotonic = true;
        foreach (var stage in pm.Stages)
        {
            double now = pm.OverallFraction;
            if (now < prev - 1e-9) monotonic = false;
            prev = now;
            stage.State = ViewModels.StageState.Done;
            stage.Done = stage.Total;
        }
        Check(monotonic, "总进度随阶段推进单调不减");
        Check(Math.Abs(pm.OverallFraction - 1.0) < 1e-9,
            $"全部完成时总进度为 100%（实际 {pm.OverallPercentText}）");

        // 跳过的阶段不能拖住总进度
        var pm2 = new ViewModels.ScanProgressModel();
        pm2.MarkSkipped(pm2.QuickHash, "没有候选");
        Check(pm2.QuickHash.State == ViewModels.StageState.Skipped, "可标记阶段为已跳过");
        Check(pm2.QuickHash.CountText == "已跳过", "跳过阶段显示'已跳过'而不是永远 0%");

        // 实时统计必须能随进度快照流进来
        var pm3 = new ViewModels.ScanProgressModel();
        pm3.VolumeTotalBytes = 1000;
        var live = new ScanStats { FilesSeen = 42, DirsVisited = 7, BytesSeen = 250 };
        pm3.Apply(new ScanProgress
        {
            Phase = ScanPhase.Scanning, LiveStats = live,
        }, live);
        Check(pm3.FilesSeen == 42 && pm3.DirsVisited == 7, "实时统计随进度快照流入");
        Check(pm3.FilesSeenText == "42", "文件数格式化正确");
        Check(Math.Abs(pm3.TraversalFraction - 0.25) < 1e-9,
            $"遍历进度按字节/容量估算正确（{pm3.TraversalFraction:P0}）");
        Check(pm3.Traversal.State == ViewModels.StageState.Active, "遍历阶段被置为进行中");
        Check(pm3.TraversalEstimateNote.Contains("估算"), "界面会声明遍历进度是估算值");
        Emit();

        // ---- 7. 相对时间（判断"删哪个"靠的是先后关系）----
        Emit("[7] 相对时间格式化");
        var clockNow = new DateTime(2026, 9, 26, 15, 0, 0);
        // DateTime.ToFileTime() 是实例方法（0 参数）；写成 DateTime.ToFileTime(x)
        // 会被解析成 TimeZoneInfo 扩展方法，编译期就报"没有采用 1 个参数的重载"
        long FT(DateTime local) => local.ToFileTime();
        Check(WinPath.FormatRelativeTimestamp(FT(clockNow), clockNow) == "今天 15:00",
            "今天");
        Check(WinPath.FormatRelativeTimestamp(FT(clockNow.AddDays(-1)), clockNow)
            == "昨天 15:00", "昨天");
        Check(WinPath.FormatRelativeTimestamp(FT(clockNow.AddDays(-3)), clockNow)
            == "3 天前", "3 天前");
        // 跨日历日才算一天：25 小时前也应该是"昨天"，按精确时间差会得到"1 天前"
        Check(WinPath.FormatRelativeTimestamp(FT(clockNow.AddHours(-25)), clockNow)
            == "昨天 14:00", "按日历日而非精确时长计算天数差");
        Check(WinPath.FormatRelativeTimestamp(FT(clockNow.AddDays(-200)), clockNow)
            == "2026年3月10日", "超过半年显示年+月日");
        Check(WinPath.FormatRelativeTimestamp(FT(clockNow.AddDays(-800)), clockNow)
            .StartsWith("202"), "超过一年显示完整日期");
        Check(WinPath.FormatRelativeTimestamp(0) == "-", "无效时间显示为 -");
        Check(WinPath.FormatRelativeTimestamp(FT(clockNow.AddDays(5)), clockNow)
            == "2026-10-01 15:00",
            "未来时间退回绝对格式（时钟偏差/备份工具会造成）");
        Check(WinPath.EllipsizeMiddle("abcdefghij", 100) == "abcdefghij", "不超长时不省略");
        Check(WinPath.EllipsizeMiddle("abcdefghijklmnop", 10).Length == 10, "超长时按中间省略");
        Emit();

        // ---- 8. 逐组指定保留项 ----
        Emit("[8] 逐组指定保留项");
        var vm2 = new ViewModels.ResultViewModel();
        vm2.Load(result);
        Check(vm2.CustomKeeperGroupCount == 0, "初始没有手动指定的保留项");

        var g0 = result.Groups.OrderByDescending(g => g.Count).First();
        var target = g0.Members.Last();   // 故意选一个当前规则不会选中的
        var ruleKeeper = g0.PickKeeper(vm2.KeepRule);
        if (!string.Equals(target.Path, ruleKeeper.Path, StringComparison.OrdinalIgnoreCase))
        {
            vm2.SetKeeperOverride(g0.DigestHex, target.Path);
            Check(vm2.CustomKeeperGroupCount == 1, "可以手动指定保留项");
            Check(vm2.GetKeeperOverride(g0.DigestHex) == target.Path, "指定被记录");

            var rowsOfG0 = vm2.Rows.Where(r => r.GroupId == g0.DigestHex
                                               && r.Kind == ViewModels.RowKind.File).ToList();
            var targetRow = rowsOfG0.First(r =>
                string.Equals(r.File!.Path, target.Path, StringComparison.OrdinalIgnoreCase));
            var otherRow = rowsOfG0.First(r =>
                !string.Equals(r.File!.Path, target.Path, StringComparison.OrdinalIgnoreCase));
            Check(!targetRow.IsToDelete, "指定的那一行不再标记为待删");
            Check(targetRow.IsCustomKeeper, "指定的那一行显示为手动指定");
            Check(otherRow.IsToDelete, "同组其他行变成待删");
            Check(otherRow.RoleText == "删除", "其他行角色文案为删除");

            // 计划必须跟着变：指定项绝不能出现在待删清单里
            var plan2 = vm2.BuildPlan();
            Check(!plan2.Targets.Any(p =>
                    string.Equals(p, target.Path, StringComparison.OrdinalIgnoreCase)),
                "待删清单里不含手动指定的保留项");
            Check(plan2.CustomKeeperCount == 1, "计划记录了 1 组手动指定");
            var audit2 = DeletePlanner.Audit(plan2);
            Check(audit2.Any(w => w.Contains("手动指定")), "审计会提示存在手动指定");
        }
        else
        {
            Emit("        （该组规则保留项恰为末位成员，跳过覆盖生效检查）");
        }

        Check(!vm2.SetKeeperOverride(g0.DigestHex, @"X:\根本不属于这一组\zzz.txt"),
            "拒绝指定不属于该组的路径");
        int countAfterReject = vm2.CustomKeeperGroupCount;
        Check(!vm2.SetKeeperOverride(g0.DigestHex, @"X:\根本不属于这一组\zzz.txt")
              && vm2.CustomKeeperGroupCount == countAfterReject,
            "被拒绝的指定不改变状态");

        // 计划层独立再校验一次：绕过界面直接传错路径也不能出事
        var badKeepers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            [g0.DigestHex] = @"X:\不存在的路径\zzz.txt",
        };
        var safePlan = DeletePlanner.Build(result.Groups, all, KeepRule.Newest, badKeepers);
        Check(safePlan.FileCount == plan.FileCount,
            $"路径不合法时退回全局规则（{safePlan.FileCount} == {plan.FileCount}）");
        Check(safePlan.CustomKeeperCount == 0, "退回规则时不计入手动指定数");

        vm2.ClearAllKeeperOverrides();
        Check(vm2.CustomKeeperGroupCount == 0, "可以一次性清除全部手动指定");
        var rowsAfter = vm2.Rows.Where(r => r.GroupId == g0.DigestHex
                                            && r.Kind == ViewModels.RowKind.File).ToList();
        Check(rowsAfter.All(r => r.IsToDelete
                                  == !string.Equals(r.File!.Path, ruleKeeper.Path,
                                      StringComparison.OrdinalIgnoreCase)),
            "清除后所有行回到按规则判定");

        // 换一批结果时旧的指定必须失效（否则会指向已不存在的路径）
        vm2.SetKeeperOverride(g0.DigestHex, target.Path);
        vm2.Load(result);
        Check(vm2.CustomKeeperGroupCount == 0, "载入新结果时清空旧的保留项指定");
        Emit();

        // ---- 9. 扫描范围（只扫指定文件夹）----
        Emit("[9] 扫描范围");
        var vm3 = new ViewModels.ResultViewModel();
        Check(vm3.Folders.Count == 0, "初始没有指定文件夹");
        Check(vm3.SelectedRoots.Count == 0, "初始没有选中任何根路径");
        Check(vm3.AddFolder("C:\\Windows\\Temp"), "可以加入文件夹");
        Check(vm3.AddFolder("D:\\Backup"), "可以加入第二个文件夹");
        Check(vm3.Folders.Count == 2, "列表里有 2 个文件夹");
        Check(!vm3.AddFolder("c:\\windows\\temp"), "同一路径不重复加入（大小写不敏感）");
        Check(vm3.Folders.Count == 2, "重复加入后数量不变");
        Check(vm3.Folders[0].IsSelected, "新加入的文件夹默认勾选");
        Check(!vm3.AddFolder("   "), "拒绝空白路径");
        Check(vm3.Folders[0].TitleText == "Temp", "标题取末级目录名");
        Check(vm3.Folders[0].PathLengthText.Contains("字符"), "显示路径长度");
        // 两个文件夹都默认勾选，所以"只扫某一个"= 全不选 + 只勾一个
        foreach (var f in vm3.Folders) f.IsSelected = false;
        Check(vm3.SelectedRoots.Count == 0,
            "全部取消勾选后不参与扫描");
        vm3.Folders[0].IsSelected = true;
        Check(vm3.SelectedRoots.Count == 1,
            "只勾一个时根路径恰好是 1 个（这就是'只扫某个文件夹'）");
        Check(vm3.SelectedRoots[0] == vm3.Folders[0].Path, "根路径就是被勾上的那个文件夹");
        vm3.Folders[1].IsSelected = true;
        Check(vm3.SelectedRoots.Count == 2, "勾上第二个后根路径变成 2 个");
        vm3.RemoveFolder(vm3.Folders[1]);
        Check(vm3.SelectedRoots.Count == 1, "移除后根路径同步减少");
        Check(vm3.Folders.Count == 1, "可以移除文件夹");
        vm3.ClearFolders();
        Check(vm3.Folders.Count == 0, "可以清空列表");
        Check(vm3.SelectedRoots.Count == 0, "清空后没有根路径");
        Emit();

        // ---- 10. 悬停详情：数据必须齐全 ----
        // 浮层本身没法在无头环境下截图验证（它是独立弹出窗口，而桌面上总有
        // 别的置顶窗口抢前台，鼠标根本到不了目标行），所以这里验证**内容**：
        // 绑定一旦拿不到值，浮层就会是一张空卡片。
        Emit("[10] 悬停详情内容");
        var fileRows = vm.Rows.Where(r => r.Kind == ViewModels.RowKind.File).ToList();
        Check(fileRows.Count > 0, $"有 {fileRows.Count} 个成员行");
        var anyRow = fileRows[0];
        Check(anyRow.DetailPathText.Length > 0, "详情含完整路径");
        Check(anyRow.DetailPathText.Length >= anyRow.PathText.Length,
            "详情路径不短于列表里那个精简过的路径");
        Check(anyRow.DetailSizeText.Contains("字节"), $"详情含精确字节数（{anyRow.DetailSizeText}）");
        Check(anyRow.DetailTimeText.Contains("（"), "详情同时给相对时间和绝对时间");
        Check(anyRow.DetailLocationText.Length > 0, $"详情含位置（{anyRow.DetailLocationText}）");
        Check(anyRow.DetailGroupText.Contains("摘要"), "详情含所属重复组摘要");
        Check(anyRow.DetailGroupText.Contains("校验") || anyRow.DetailGroupText.Contains("未校验"),
            "详情声明是否已逐字节校验");
        Check(anyRow.DetailRoleText.Length > 0, $"详情含处理方式（{anyRow.DetailRoleText}）");

        // 组内对比：判断"删哪个"最有用的一块
        var multi = fileRows.FirstOrDefault(r => r.Group.Count >= 2);
        if (multi is not null)
        {
            Emit("        组内对比:");
            foreach (var line in multi.DetailCompareText.Split('\n'))
                Emit($"          {line}");
            Check(multi.DetailCompareText.Split('\n').Length == multi.Group.Count,
                "对比块列出了组内全部副本");
            Check(multi.DetailCompareText.Contains("▶ 保留"),
                "对比块标出哪一份被保留");
            Check(multi.DetailCompareText.Contains("◆"), "对比块标出当前悬停的那一行");
            Check(multi.CompareVisibility == Microsoft.UI.Xaml.Visibility.Visible,
                "多副本时对比块可见");
        }
        var single = fileRows.FirstOrDefault(r => r.Group.Count < 2);
        if (single is not null)
            Check(single.CompareVisibility == Microsoft.UI.Xaml.Visibility.Collapsed,
                "只有一个副本时对比块隐藏（没有可比对象）");

        var groupRow = vm.Rows.FirstOrDefault(r => r.Kind == ViewModels.RowKind.Group);
        if (groupRow is not null)
        {
            Check(groupRow.DetailCompareText == "", "组行不显示组内对比（那是成员行的职责）");
            Check(groupRow.GroupTimeSpanText.Length > 0,
                $"组行有时间跨度（{groupRow.GroupTimeSpanText}）");
            Check(!groupRow.GroupTimeSpanText.Contains("→ →"),
                "时间跨度不会输出无意义的重复箭头");
            Check(groupRow.DriveLabel.Contains("卷"), $"组行显示副本分布（{groupRow.DriveLabel}）");
        }
        Emit();

        Emit(new string('-', 60));
        Emit(result.Summary);
        Emit();
        Emit($"通过 {_passed} 项，失败 {Failures.Count} 项");
        foreach (var f in Failures) Emit($"  FAILED: {f}");
        Emit("本次自检未删除任何文件。");
        return Failures.Count == 0 ? 0 : 1;
    }

    private static int info_TargetsStillExist(DeletePlan plan)
    {
        int n = 0;
        foreach (var p in plan.Targets)
        {
            try { if (File.Exists(p) || Directory.Exists(p)) n++; } catch { }
        }
        return n;
    }

    private static void WalkForLongPaths(string dir, List<string> found, int depth)
    {
        if (depth > 24 || found.Count > 40) return;
        IEnumerable<string> entries;
        try { entries = Directory.EnumerateFileSystemEntries(dir).ToArray(); }
        catch { return; }
        foreach (var e in entries)
        {
            bool isDir;
            try { isDir = Directory.Exists(e); } catch { continue; }
            if (isDir)
            {
                WalkForLongPaths(e, found, depth + 1);
            }
            else if (e.Length > WinPath.LongPathUnsupported)
            {
                found.Add(e);
            }
        }
    }

    private static bool CanOpen(string path)
    {
        try
        {
            using var fs = new FileStream(path, FileMode.Open, FileAccess.Read,
                                          FileShare.ReadWrite, 1,
                                          FileOptions.SequentialScan);
            return fs.Length >= 0;
        }
        catch
        {
            return false;
        }
    }
}
