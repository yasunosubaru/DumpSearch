using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using DumpSearch.App.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;

namespace DumpSearch.App.ViewModels;

public enum RowKind { Group, File }

/// <summary>结果列表里的一行（扁平模型，组行 + 成员行）。</summary>
public sealed class ResultRow : INotifyPropertyChanged
{
    // 注意：这几个成员**不能**加 required。带 required 的类型无法被
    // `new ResultRow()` 实例化，而 XamlCompiler 为 x:DataType 生成类型信息时
    // 会走这条路，失败只报无信息的「错误: 1」。
    // 改用普通 init 属性 + RebuildRows() 里的对象初始化器赋值。
    public RowKind Kind { get; init; }
    public string GroupId { get; init; } = "";
    public int GroupIndex { get; init; }
    public int FileIndex { get; init; } = -1;

    public DuplicateGroup Group { get; init; } = new()
    {
        DigestHex = "", Size = 0, Members = Array.Empty<FileEntry>(),
    };
    public FileEntry? File { get; init; }

    public string SizeText => Kind == RowKind.Group
        ? Group.SizeText : (File?.SizeText ?? "");
    public string CountText => Kind == RowKind.Group ? Group.Count.ToString() : "";
    public string WastedText => Kind == RowKind.Group
        ? Group.WastedText : (IsToDelete ? (File?.SizeText ?? "") : "");

    /// <summary>绝对时间，交给 ToolTip 显示。</summary>
    public string TimeText => Kind == RowKind.File ? (File?.TimeText ?? "") : "";

    /// <summary>相对时间（"3 天前"），列表里主显这个。</summary>
    public string TimeLabel => Kind == RowKind.File ? (File?.TimeRelativeText ?? "") : "";

    /// <summary>组内最晚/最早修改时间，让"这一组的时间跨度"一眼可见。</summary>
    public string GroupTimeSpanText
    {
        get
        {
            if (Kind != RowKind.Group || Group.Count == 0) return "";
            var newest = Group.Members.Max(m => m.LastWriteTicks);
            var oldest = Group.Members.Min(m => m.LastWriteTicks);
            // 按**显示精度**（分钟）判断是否同一时间，而不是比原始 FILETIME：
            // 同一秒内写完的一组文件，FILETIME 之间仍差几百个 100ns 刻度，
            // 直接比会输出"今天 17:14 → 今天 17:14"这种毫无信息量的东西。
            var newestText = WinPath.FormatRelativeTimestamp(newest);
            var oldestText = WinPath.FormatRelativeTimestamp(oldest);
            if (newestText == oldestText) return $"全部同为 {newestText}";
            return $"{oldestText} → {newestText}";
        }
    }

    /// <summary>
    /// 展示用的路径。整批结果往往共享一个很长的前缀（例如都在某个临时目录下），
    /// 直接显示绝对路径会把列宽全耗在重复的前缀上，真正有区别的尾部反而被截掉。
    /// 所以这里剥掉所有结果共有的目录前缀，只留有区分度的部分；
    /// 完整绝对路径放在 <see cref="FullPathText"/>（界面上作为 ToolTip）。
    /// </summary>
    public string PathText => StripSharedPrefix(Kind == RowKind.File
        ? (File?.Directory ?? "")
        : (Group.Members.FirstOrDefault()?.Directory ?? ""));

    public string FullPathText => Kind == RowKind.File
        ? (File?.Path ?? "")
        : (Group.Members.FirstOrDefault()?.Path ?? "");

    private string StripSharedPrefix(string dir)
    {
        if (dir.Length == 0) return dir;
        var prefix = _owner?.SharedDirPrefix ?? "";
        if (prefix.Length > 0 && dir.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        {
            var rest = dir[prefix.Length..].TrimStart('\\', '/');
            if (rest.Length > 0) return "…\\" + rest;
        }
        return dir;
    }

    public string NameText => Kind == RowKind.Group
        ? $"{Group.Count} 个相同文件" : (File?.Name ?? "");

    public string DigestText => Group.DigestHex.Length >= 12
        ? Group.DigestHex[..12] : Group.DigestHex;

    /// <summary>
    /// 本组实际生效的保留项：优先用用户手动指定的，否则按全局规则。
    /// 所有"谁被保留"的判断都走它，界面、计划、审计保持同一口径——
    /// 分散判断迟早会出现"界面说保留 A、计划删 A"。
    /// </summary>
    public FileEntry? EffectiveKeeper
    {
        get
        {
            if (Kind != RowKind.File) return null;
            var overridePath = _owner?.GetKeeperOverride(GroupId);
            if (overridePath is not null)
            {
                var m = Group.Members.FirstOrDefault(x =>
                    string.Equals(x.Path, overridePath, StringComparison.OrdinalIgnoreCase));
                if (m is not null) return m;
            }
            return Group.PickKeeper(_owner?.KeepRule ?? KeepRule.Newest);
        }
    }

    public bool IsToDelete
    {
        get
        {
            if (Kind != RowKind.File || File is null) return false;
            var keeper = EffectiveKeeper;
            return keeper is null
                || !string.Equals(File.Path, keeper.Path, StringComparison.OrdinalIgnoreCase);
        }
    }

    /// <summary>这一行的保留项是被手动指定的（而非按规则算出来的）。</summary>
    public bool IsCustomKeeper => Kind == RowKind.File
                                  && _owner?.GetKeeperOverride(GroupId) is string p
                                  && File is not null
                                  && string.Equals(p, File.Path, StringComparison.OrdinalIgnoreCase);

    /// <summary>这一组里有手动指定的保留项。</summary>
    public bool GroupHasCustomKeeper =>
        Kind == RowKind.Group && _owner?.GetKeeperOverride(GroupId) is not null;

    public string RoleText => Kind != RowKind.File ? ""
        : IsCustomKeeper ? "保留（指定）" : IsToDelete ? "删除" : "保留";

    // ---- 供 XAML 绑定的展示属性 ----
    public bool IsGroupRow => Kind == RowKind.Group;
    public bool IsFileRow => Kind == RowKind.File;

    /// <summary>
    /// 用它而不是 <c>IsVisible</c>：本机 XamlCompiler 拒绝在 XAML 里设置
    /// <c>IsVisible</c>（连常量 <c>IsVisible="True"</c> 都编译失败，报无信息的
    /// 「错误: 1」），而 <c>Visibility</c> 一切正常。
    /// </summary>
    public Visibility GroupRowVisibility =>
        Kind == RowKind.Group ? Visibility.Visible : Visibility.Collapsed;

    public Visibility FileRoleVisibility =>
        Kind == RowKind.File ? Visibility.Visible : Visibility.Collapsed;

    /// <summary>「在 &lt;目录&gt;」那行只在成员行显示，组行不需要。</summary>
    public Visibility FileRowVisibility =>
        Kind == RowKind.File ? Visibility.Visible : Visibility.Collapsed;

    /// <summary>组内对比块：只有一个副本时没有可比对象，隐藏。</summary>
    public Visibility CompareVisibility =>
        Kind == RowKind.File && Group.Members.Count >= 2
            ? Visibility.Visible : Visibility.Collapsed;

    /// <summary>所在卷（"C:\" / "\\\\server\\share"）。跨盘副本一眼能看出来。</summary>
    public string DriveLabel => Kind == RowKind.File
        ? (File?.DriveText ?? "")
        : $"{Group.Members.Select(m => m.DriveText).Distinct().Count()} 个卷";

    /// <summary>「保留（指定）」徽标：只在手动指定时出现。</summary>
    public Visibility CustomKeeperBadgeVisibility =>
        IsCustomKeeper ? Visibility.Visible : Visibility.Collapsed;

    // ---- 悬停详情（ToolTip）----
    // 全部来自扫描时已经在手的数据，不做任何额外 IO：
    // 悬停时读磁盘会卡住 UI，而创建时间和访问时间都需要额外打开句柄，
    // 在遍历阶段为每个文件多开一次会把扫描拖慢一个数量级。
    // 这里只展示真正影响"删哪个"判断的信息。

    public string DetailPathText => FullPathText;
    public string DetailSizeText => Kind == RowKind.File
        ? $"{File?.SizeText}  ({File?.Size:N0} 字节)" : Group.SizeText;
    public string DetailTimeText => Kind == RowKind.File
        ? $"{File?.TimeRelativeText}（{File?.TimeText}）" : GroupTimeSpanText;
    public string DetailLocationText => Kind == RowKind.File
        ? $"{File?.DriveText} · {StripSharedPrefix(File?.Directory ?? "")}"
        : $"{Group.Members.Count} 个副本，分布于 " +
          $"{Group.Members.Select(m => m.DriveText).Distinct().Count()} 个位置";
    public string DetailGroupText => $"{Group.Count} 个副本 · 摘要 {DigestText}…" +
        (Group.Verified ? " · 已逐字节校验" : " · 未校验");
    public string DetailRoleText => Kind != RowKind.File
        ? "" : IsCustomKeeper ? "保留（你指定的）" : IsToDelete ? "删除" : "保留（按规则）";

    /// <summary>组内对比：把所有副本的位置和时间列出来，一眼看出该留哪个。</summary>
    public string DetailCompareText
    {
        get
        {
            if (Kind != RowKind.File || Group.Members.Count < 2) return "";
            var keeper = EffectiveKeeper;
            return string.Join("\n", Group.Members.Select(m =>
            {
                bool keep = keeper is not null
                    && string.Equals(m.Path, keeper.Path, StringComparison.OrdinalIgnoreCase);
                bool self = File is not null
                    && string.Equals(m.Path, File.Path, StringComparison.OrdinalIgnoreCase);
                return (keep ? "▶ 保留  " : "   删除  ")
                     + (self ? "◆ " : "   ")
                     + $"{m.TimeRelativeText,-12} {WinPath.FormatBytes(m.Size),10}  "
                     + StripSharedPrefix(m.Directory);
            }));
        }
    }

    private static readonly Brush Transparent = new SolidColorBrush(Microsoft.UI.Colors.Transparent);
    private static readonly Brush DangerBrush = new SolidColorBrush(Microsoft.UI.Colors.IndianRed);
    private static readonly Brush KeepBrush = new SolidColorBrush(Microsoft.UI.Colors.MediumSeaGreen);

    public Brush RoleBrush => Kind != RowKind.File ? Transparent
        : IsToDelete ? DangerBrush : KeepBrush;

    private static readonly Windows.UI.Text.FontWeight Bold = new() { Weight = 600 };
    private static readonly Windows.UI.Text.FontWeight Normal = new() { Weight = 400 };

    public Windows.UI.Text.FontWeight NameWeight =>
        Kind == RowKind.Group ? Bold : Normal;

    /// <summary>成员行缩进，视觉上体现从属关系。</summary>
    public Thickness NameMargin =>
        Kind == RowKind.Group ? new Thickness(0) : new Thickness(14, 0, 0, 0);

    private ResultViewModel? _owner;

    public void Bind(ResultViewModel owner) => _owner = owner;

    private bool? _isChecked;

    /// <summary>
    /// 必须是 <c>bool?</c> 而不是 <c>bool</c>：CheckBox.IsChecked 是
    /// 三态可空属性，x:Bind Mode=TwoWay 生成的赋值代码是
    /// <c>target.IsChecked = source.IsChecked</c>，源是非空 bool 时
    /// XamlCompiler 直接失败，而且只报无信息的「错误: 1」。
    /// </summary>
    public bool? IsChecked
    {
        get => _isChecked;
        set
        {
            if (_isChecked == value) return;
            _isChecked = value;
            if (Kind == RowKind.Group) _owner?.SetGroupChecked(GroupId, value == true);
            OnPropertyChanged();
        }
    }

    public bool IsExpanded
    {
        get => _owner?.IsGroupExpanded(GroupId) ?? false;
        set { if (_owner is not null) _owner.SetGroupExpanded(GroupId, value); }
    }

    /// <summary>
    /// 保留项相关的任何变化都要通知，因为 IsToDelete / RoleText /
    /// 各类徽标都是**计算属性**：改了字典不会自动触发 PropertyChanged，
    /// 界面会停留在上一次的结论上——这正是最容易出"界面与实际不一致"的地方。
    /// </summary>
    internal void NotifyKeeperChanged()
    {
        OnPropertyChanged(nameof(IsToDelete));
        OnPropertyChanged(nameof(IsCustomKeeper));
        OnPropertyChanged(nameof(GroupHasCustomKeeper));
        OnPropertyChanged(nameof(RoleText));
        OnPropertyChanged(nameof(CustomKeeperBadgeVisibility));
        OnPropertyChanged(nameof(EffectiveKeeper));
        OnPropertyChanged(nameof(DetailRoleText));
        OnPropertyChanged(nameof(DetailCompareText));
        OnPropertyChanged(nameof(NameWeight));
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void OnPropertyChanged([CallerMemberName] string? n = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
}

/// <summary>
/// 一个"只扫这个文件夹"的条目。
///
/// 为什么不让用户直接编辑一个多行文本框：多行 TextBox 里没法逐项勾选，
/// 想去掉一个目录只能手改文本，改错了还看不出来；而且长路径（&gt;260 字符）
/// 恰恰是最需要用扫描范围指定、又最容易手打错的路径。
/// </summary>
public sealed class ScopeFolder : INotifyPropertyChanged
{
    private bool _isSelected;
    private string _displayName;

    public ScopeFolder(string path)
    {
        Path = WinPath.Normalize(path);
        _displayName = MakeDisplayName(Path);
    }

    public string Path { get; }

    public string DisplayName
    {
        get => _displayName;
        private set => Set(ref _displayName, value);
    }

    /// <summary>是否参与本次扫描。取消勾选即"只扫别的文件夹"。</summary>
    public bool IsSelected
    {
        get => _isSelected;
        set => Set(ref _isSelected, value);
    }

    /// <summary>路径长度徽标：超长路径值得提醒一句，因为最容易在这里出错。</summary>
    public string PathLengthText => Path.Length > WinPath.LongPathUnsupported
        ? $"{Path.Length} 字符 · 超长路径"
        : $"{Path.Length} 字符";

    /// <summary>
    /// 用最后一级目录名做标题。整条路径放到副标题/悬停里。
    /// 同名目录（好几个 "backup"）靠父目录区分比靠全路径更好认。
    /// </summary>
    private static string MakeDisplayName(string path)
    {
        try
        {
            var name = System.IO.Path.GetFileName(path.TrimEnd('\\', '/'));
            if (!string.IsNullOrWhiteSpace(name)) return name;
        }
        catch { }
        return path;
    }

    /// <summary>显示用短标题：太长的末级名截断，避免撑破左侧栏。</summary>
    public string TitleText => WinPath.EllipsizeMiddle(DisplayName, 28);

    public string SubtitleText => WinPath.EllipsizeMiddle(Path, 40);

    public event PropertyChangedEventHandler? PropertyChanged;

    private void Set<T>(ref T field, T value, [CallerMemberName] string? n = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
    }
}

public sealed class DriveItem : INotifyPropertyChanged
{
    public DriveEntry Entry { get; }
    private bool _isSelected;

    public DriveItem(DriveEntry entry, bool selected)
    {
        Entry = entry;
        _isSelected = selected;
    }

    public string Root => Entry.Root;
    public string Label => Entry.Label;
    public string Title => Entry.DisplayName;

    /// <summary>
    /// 副标题只说盘的性质，不重复剩余空间——那已经在右侧单独一列显示了，
    /// 两处都写会让同一行出现两遍同样的数字。
    /// </summary>
    public string Subtitle => Entry.TypeName;
    public string FreeText => "剩余 " + WinPath.FormatBytes(Entry.FreeBytes);
    public bool IsNetwork => Entry.IsNetwork;

    public bool IsSelected
    {
        get => _isSelected;
        set { if (_isSelected != value) { _isSelected = value; Changed(); } }
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed([CallerMemberName] string? n = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
}

/// <summary>主视图模型：扫描状态 + 结果集 + 删除计划。纯逻辑，可无 UI 测试。</summary>
public sealed class ResultViewModel : INotifyPropertyChanged
{
    private readonly List<DuplicateGroup> _allGroups = new();
    private readonly HashSet<string> _checked = new(StringComparer.Ordinal);
    private readonly HashSet<string> _collapsed = new(StringComparer.Ordinal);

    /// <summary>
    /// 用户手动指定的保留项：组摘要 -> 文件路径。
    /// 覆盖全局保留规则，键值都按不区分大小写处理（Windows 上路径大小写可变）。
    /// </summary>
    private readonly Dictionary<string, string> _keepers = new(StringComparer.OrdinalIgnoreCase);

    private List<DuplicateGroup> _sorted = new();
    private ScanResult? _result;
    private KeepRule _keepRule = KeepRule.Newest;

    public ObservableCollection<ResultRow> Rows { get; } = new();
    public ObservableCollection<DriveItem> Drives { get; } = new();

    /// <summary>用户通过"选择文件夹"加入的扫描范围。</summary>
    public ObservableCollection<ScopeFolder> Folders { get; } = new();

    /// <summary>本次实际参与扫描的根路径集合（盘符 + 已勾选文件夹）。</summary>
    public IReadOnlyList<string> SelectedRoots
    {
        get
        {
            var roots = new List<string>();
            foreach (var d in Drives)
                if (d.IsSelected) roots.Add(d.Root);
            foreach (var f in Folders)
                if (f.IsSelected) roots.Add(f.Path);
            return roots.Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        }
    }

    /// <summary>
    /// 加入一个扫描文件夹。返回 false 表示路径非法或已经加过。
    /// 已存在时**不重复添加**而是把它勾上——重复扫描同一目录只会浪费时间。
    /// </summary>
    public bool AddFolder(string path)
    {
        string normalized;
        try { normalized = WinPath.Normalize(path); }
        catch (ArgumentException) { return false; }
        if (normalized.Length == 0) return false;

        var existing = Folders.FirstOrDefault(f =>
            string.Equals(f.Path, normalized, StringComparison.OrdinalIgnoreCase));
        if (existing is not null)
        {
            existing.IsSelected = true;
            return false;
        }
        Folders.Add(new ScopeFolder(normalized) { IsSelected = true });
        return true;
    }

    public void RemoveFolder(ScopeFolder folder) => Folders.Remove(folder);

    public void ClearFolders() => Folders.Clear();

    /// <summary>手动指定过保留项的组数。</summary>
    public int CustomKeeperGroupCount => _keepers.Count;

    /// <summary>取本组手动指定的保留项路径，没有则返回 null。</summary>
    public string? GetKeeperOverride(string groupId) =>
        _keepers.TryGetValue(groupId, out var p) ? p : null;

    /// <summary>
    /// 指定某组保留哪一个文件。path 传 null 表示取消指定、回到全局规则。
    /// 返回 false 表示路径不属于该组——界面不会这么调，但计划构建时会再校验一次。
    /// </summary>
    public bool SetKeeperOverride(string groupId, string? path)
    {
        var group = _allGroups.FirstOrDefault(g =>
            string.Equals(g.DigestHex, groupId, StringComparison.Ordinal));
        if (path is not null && group is not null)
        {
            bool member = group.Members.Any(m =>
                string.Equals(m.Path, path, StringComparison.OrdinalIgnoreCase));
            if (!member) return false;
        }

        bool changed;
        if (string.IsNullOrWhiteSpace(path)) changed = _keepers.Remove(groupId);
        else changed = !_keepers.TryGetValue(groupId, out var old)
                      || !string.Equals(old, path, StringComparison.OrdinalIgnoreCase);
        if (!changed) return true;

        if (string.IsNullOrWhiteSpace(path)) _keepers.Remove(groupId);
        else _keepers[groupId] = path;

        // 关键：IsToDelete / RoleText 都是计算属性，必须显式通知
        foreach (var row in Rows)
            if (string.Equals(row.GroupId, groupId, StringComparison.Ordinal))
                row.NotifyKeeperChanged();

        Changed(nameof(CustomKeeperGroupCount));
        Changed(nameof(SelectionText));
        return true;
    }

    public void ClearAllKeeperOverrides()
    {
        if (_keepers.Count == 0) return;
        _keepers.Clear();
        foreach (var row in Rows) row.NotifyKeeperChanged();
        Changed(nameof(CustomKeeperGroupCount));
        Changed(nameof(SelectionText));
    }

    public string FilterText { get; set; } = "";
    public long MinGroupSize { get; set; }
    public bool VerifiedOnly { get; set; }
    public string SortMode { get; private set; } = "reclaimable";
    public bool SortDescending { get; private set; } = true;

    public KeepRule KeepRule
    {
        get => _keepRule;
        set
        {
            if (_keepRule == value) return;
            _keepRule = value;
            // 不要写 _result?.Config.KeepRule = value：
            // 那是 C# 14 的"null 条件赋值"，语言版本低于 14 时直接编译失败，
            // 而且失败发生在 XamlCompiler 内部，只报无信息的「错误: 1」。
            if (_result is not null) _result.Config.KeepRule = value;
            RefreshAll();
            Changed(nameof(KeepRule));
        }
    }

    public ScanResult? Result => _result;

    public void Load(ScanResult result)
    {
        _result = result;
        _allGroups.Clear();
        _allGroups.AddRange(result.Groups);
        _checked.Clear();
        foreach (var g in _allGroups) _checked.Add(g.DigestHex);
        _collapsed.Clear();
        // 新一批结果和上一批没有对应关系，旧的"保留哪个"必须清掉，
        // 否则会残留指向已不存在的路径
        _keepers.Clear();
        SharedDirPrefix = ComputeSharedDirPrefix(result.Groups);
        Changed(nameof(SharedDirPrefix));
        Changed(nameof(CustomKeeperGroupCount));
        Refilter();
    }

    /// <summary>
    /// 所有结果目录的最长公共前缀（不含尾部反斜杠），用于精简路径列的显示。
    /// 必须**逐段**求公共前缀，不能拿第一个目录当基准去比其余的：
    /// 兄弟目录（…\docs\a 与 …\data）之间没有共同的第一段，
    /// 那样算出来永远是空串，路径列就退化回全路径。
    /// </summary>
    public string SharedDirPrefix { get; private set; } = "";

    private static string ComputeSharedDirPrefix(IReadOnlyList<DuplicateGroup> groups)
    {
        // 逐段比较：common 始终是「到目前为止所有目录都共有」的那几段
        string[]? common = null;
        foreach (var g in groups)
        {
            foreach (var m in g.Members)
            {
                var parts = m.Directory.Split(
                    new[] { '\\', '/' }, StringSplitOptions.RemoveEmptyEntries);
                if (common is null) { common = parts; continue; }

                int n = Math.Min(common.Length, parts.Length);
                int i = 0;
                while (i < n
                       && string.Equals(common[i], parts[i],
                                        StringComparison.OrdinalIgnoreCase))
                    i++;
                if (i != common.Length) Array.Resize(ref common, i);
                if (common.Length == 0) return "";   // 已经没有公共段了
            }
        }

        // 整批都在同一个目录里 → 剥掉前缀就什么都不剩，白搭
        if (common is null || common.Length == 0) return "";
        return WinPath.EnsureTrailingSep(string.Join('\\', common));
    }

    public void Clear()
    {
        _result = null;
        _allGroups.Clear();
        _checked.Clear();
        _collapsed.Clear();
        _keepers.Clear();
        SharedDirPrefix = "";
        Rows.Clear();
        Changed(nameof(SharedDirPrefix));
        Changed(nameof(CustomKeeperGroupCount));
        Changed(nameof(SummaryText));
        Changed(nameof(SelectionText));
    }

    public bool IsGroupExpanded(string id) => !_collapsed.Contains(id);
    public void SetGroupExpanded(string id, bool expanded)
    {
        bool changed = expanded ? _collapsed.Remove(id) : _collapsed.Add(id);
        if (changed) RebuildRows();
    }

    public bool IsGroupChecked(string id) => _checked.Contains(id);
    public void SetGroupChecked(string id, bool value)
    {
        bool changed = value ? _checked.Add(id) : _checked.Remove(id);
        if (changed) Changed(nameof(SelectionText));
    }

    public void CheckAll() { foreach (var g in VisibleGroups) _checked.Add(g.DigestHex); RefreshChecks(); }
    public void CheckNone() { _checked.Clear(); RefreshChecks(); }
    public void Invert()
    {
        foreach (var g in VisibleGroups)
        {
            if (!_checked.Remove(g.DigestHex)) _checked.Add(g.DigestHex);
        }
        RefreshChecks();
    }
    public void ExpandAll() { _collapsed.Clear(); RebuildRows(); }
    public void CollapseAll() { foreach (var g in VisibleGroups) _collapsed.Add(g.DigestHex); RebuildRows(); }

    public void SetFilter(string text) { FilterText = text ?? ""; Refilter(); }
    public void SetMinGroupSize(long size) { MinGroupSize = size; Refilter(); }
    public void SetVerifiedOnly(bool value) { VerifiedOnly = value; Refilter(); }

    public void SetSort(string mode, bool? descending = null)
    {
        SortMode = mode;
        if (descending is { } d) SortDescending = d;
        else if (mode == "reclaimable") SortDescending = true;
        Refilter();
    }

    public void ToggleSortDirection() { SortDescending = !SortDescending; Refilter(); }

    private IEnumerable<DuplicateGroup> VisibleGroups => _sorted;

    public int TotalGroupCount => _allGroups.Count;
    public int VisibleGroupCount => _sorted.Count;

    /// <summary>
    /// 重新计算"排序 + 过滤"后的可见集合。
    /// 始终从全集 _allGroups 重算：在已过滤结果上二次过滤的话，
    /// 放宽条件（清空筛选、把最小体积调小）就再也恢复不了被滤掉的组。
    /// </summary>
    private void Refilter()
    {
        string needle = FilterText.Trim();
        IEnumerable<DuplicateGroup> q = _allGroups;

        if (MinGroupSize > 0) q = q.Where(g => g.Size >= MinGroupSize);
        if (VerifiedOnly) q = q.Where(g => g.Verified);

        if (needle.Length > 0)
        {
            bool byExt = needle.StartsWith("ext:", StringComparison.OrdinalIgnoreCase);
            bool byName = needle.StartsWith("name:", StringComparison.OrdinalIgnoreCase);
            int prefix = byExt ? 4 : byName ? 5 : 0;
            string key = needle[prefix..].Trim().ToLowerInvariant();
            if (byExt && !key.StartsWith('.')) key = "." + key;

            q = byExt
                ? q.Where(g => g.Members.Any(m => m.Extension == key))
                : byName
                    ? q.Where(g => g.Members.Any(m =>
                        m.Name.Contains(key, StringComparison.OrdinalIgnoreCase)))
                    : q.Where(g => g.Members.Any(m =>
                        m.Path.Contains(key, StringComparison.OrdinalIgnoreCase)));
        }

        _sorted = ApplySort(q);
        RebuildRows();
    }

    private List<DuplicateGroup> ApplySort(IEnumerable<DuplicateGroup> source)
    {
        IEnumerable<DuplicateGroup> ordered = SortMode switch
        {
            "size" => Order(source, g => g.Size),
            "count" => Order(source, g => g.Count),
            "name" => Order(source,
                g => g.Members.FirstOrDefault()?.Name ?? string.Empty,
                StringComparer.OrdinalIgnoreCase),
            "path" => Order(source,
                g => g.Members.FirstOrDefault()?.Path ?? string.Empty,
                StringComparer.OrdinalIgnoreCase),
            "mtime" => Order(source,
                g => g.Members.Count == 0 ? 0L : g.Members.Max(m => m.LastWriteTicks)),
            _ => Order(source, g => g.WastedBytes),
        };
        return ordered.ToList();
    }

    private static IEnumerable<DuplicateGroup> Order<TKey>(
        IEnumerable<DuplicateGroup> src, Func<DuplicateGroup, TKey> key,
        IComparer<TKey>? comparer = null) =>
        src.OrderBy(key, comparer);

    private void RebuildRows()
    {
        Rows.Clear();
        int gi = 0;
        foreach (var group in _sorted)
        {
            var groupRow = new ResultRow
            {
                Kind = RowKind.Group,
                GroupId = group.DigestHex,
                GroupIndex = gi,
                Group = group,
            };
            groupRow.Bind(this);
            groupRow.IsChecked = _checked.Contains(group.DigestHex);
            Rows.Add(groupRow);

            if (!_collapsed.Contains(group.DigestHex))
            {
                for (int fi = 0; fi < group.Members.Count; fi++)
                {
                    var fileRow = new ResultRow
                    {
                        Kind = RowKind.File,
                        GroupId = group.DigestHex,
                        GroupIndex = gi,
                        FileIndex = fi,
                        Group = group,
                        File = group.Members[fi],
                    };
                    fileRow.Bind(this);
                    Rows.Add(fileRow);
                }
            }
            gi++;
        }
        Changed(nameof(VisibleGroupCount));
        Changed(nameof(TotalGroupCount));
        Changed(nameof(SelectionText));
        Changed(nameof(SummaryText));
    }

    private void RefreshAll()
    {
        foreach (var row in Rows) row.IsChecked = _checked.Contains(row.GroupId);
        Changed(nameof(SelectionText));
    }

    private void RefreshChecks()
    {
        foreach (var row in Rows)
            if (row.Kind == RowKind.Group)
                row.IsChecked = _checked.Contains(row.GroupId);
        Changed(nameof(SelectionText));
    }

    public DeletePlan BuildPlan() =>
        DeletePlanner.Build(_sorted, _checked, _keepRule, _keepers);

    public string SummaryText => _result?.Summary ?? "尚未扫描";

    public string SelectionText
    {
        get
        {
            var groups = _sorted.Count(g => _checked.Contains(g.DigestHex));
            int files = _sorted.Where(g => _checked.Contains(g.DigestHex)).Sum(g => g.Count);
            long reclaim = _sorted.Where(g => _checked.Contains(g.DigestHex))
                                 .Sum(g => g.WastedBytes);
            var text = $"显示 {_sorted.Count:N0}/{_allGroups.Count:N0} 组 · " +
                       $"已选 {groups:N0} 组 / {files:N0} 个文件 · " +
                       $"可释放 {WinPath.FormatBytes(reclaim)}";
            if (_keepers.Count > 0)
                text += $" · 手动指定保留 {_keepers.Count:N0} 组";
            return text;
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed([CallerMemberName] string? n = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
}
