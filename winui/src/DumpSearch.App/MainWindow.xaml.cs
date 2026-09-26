using System.Collections.ObjectModel;
using DumpSearch.App.Core;
using DumpSearch.App.ViewModels;
using Microsoft.UI;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace DumpSearch.App;

public sealed partial class MainWindow : Window
{
    private readonly ResultViewModel _viewModel = new();
    public ResultViewModel ViewModel => _viewModel;

    /// <summary>扫描进度的界面模型，供 XAML 的阶段步进器绑定。</summary>
    public ScanProgressModel Progress { get; } = new();

    private CancellationTokenSource? _scanCts;
    private Task<ScanResult>? _scanTask;
    private readonly DispatcherQueueTimer _uiTimer;

    private long _lastSpeedBytes;
    private long _lastSpeedStamp;
    private double _speed;
    private ScanProgress _latest = new();
    private ScanStats _lastStats = new();

    public MainWindow()
    {
        InitializeComponent();

        // Mica 必须在代码里设：本机 XamlCompiler 对 XAML 声明的 MicaBackdrop
        // 会报无详情的编译错误。
        SystemBackdrop = new MicaBackdrop();

        ExtendsContentIntoTitleBar = true;
        var hwnd = WindowNative.GetWindowHandle(this);
        var windowId = Win32Interop.GetWindowIdFromWindow(hwnd);
        var appWindow = AppWindow.GetFromWindowId(windowId);
        appWindow.Resize(new Windows.Graphics.SizeInt32(1440, 900));
        appWindow.Title = "Dump Search · 全盘重复文件查找";

        // 标题栏图标必须给**绝对路径**。SetIcon 拿到相对路径时会按当前工作
        // 目录去找——从资源管理器双击启动时 CWD 是用户目录，从快捷方式启动时
        // 又是别的路径，于是文件找不到，调用**静默失败**不报任何错。
        // （之前就是这么白写了一句，窗口图标一直是默认的。）
        var iconPath = Path.Combine(AppContext.BaseDirectory, "Assets", "DumpSearch.ico");
        if (File.Exists(iconPath)) appWindow.SetIcon(iconPath);
        else System.Diagnostics.Debug.WriteLine($"图标缺失，未设置标题栏图标: {iconPath}");

        _viewModel.Drives.Clear();
        foreach (var d in DriveEnumerator.List())
            _viewModel.Drives.Add(new DriveItem(d, d.SelectedByDefault));

        KeepRuleBox.ItemsSource = KeepRuleNames.All.Select(k => k.Label).ToList();
        KeepRuleBox.SelectedIndex = 0;
        SortBox.ItemsSource = new[] { "按可释放空间", "按单文件大小", "按副本数量",
                                      "按文件名", "按路径", "按修改时间" };
        SortBox.SelectedIndex = 0;
        UpdateKeepRuleHint();

        // 定时器只做"把最新快照刷到界面"，避免高频进度事件淹没 UI 线程
        _uiTimer = DispatcherQueue.CreateTimer();
        _uiTimer.Interval = TimeSpan.FromMilliseconds(120);
        _uiTimer.Tick += OnUiTimerTick;

        // 分隔条不做自定义光标：UIElement.Cursor 在本 SDK 既不能写在 XAML
        // （InputSystemCursor 无法从字符串游标名转换，XamlCompiler 直接失败
        // 且只报「错误: 1」），在代码里也没有可用的公开 CursorProperty。
        // 少个箭头光标不值得为此绕路。

        ShowEmptyState("选择左侧的盘符或目录，然后点「开始扫描」。");
        BuildStageStrip();
        UpdateSelection();
    }

    // =================================================================
    // 生命周期
    // =================================================================
    private void OnUiTimerTick(object? sender, object e)
    {
        var p = _latest;
        long now = Environment.TickCount64;
        if (now - _lastSpeedStamp >= 400)
        {
            double rate = (p.BytesProcessed - _lastSpeedBytes)
                          / ((now - _lastSpeedStamp) / 1000.0);
            _speed = _speed == 0 ? rate : _speed * 0.4 + rate * 0.6;
            _lastSpeedBytes = p.BytesProcessed;
            _lastSpeedStamp = now;
        }

        PhaseText.Text = p.Phase switch
        {
            ScanPhase.Scanning => $"扫描中 · {p.Detail}",
            ScanPhase.Grouping => $"按大小分组 · {p.Detail}",
            ScanPhase.QuickHash => $"快速哈希 · {p.Done:N0}/{p.Total:N0}",
            ScanPhase.FullHash => $"完整哈希 · {p.Done:N0}/{p.Total:N0}",
            ScanPhase.Verify => $"逐字节校验 · {p.Done:N0}/{p.Total:N0}",
            ScanPhase.Done => "完成",
            ScanPhase.Cancelled => "已取消",
            ScanPhase.Failed => "出错",
            _ => p.Detail,
        };

        if (p.Phase is ScanPhase.QuickHash or ScanPhase.FullHash or ScanPhase.Verify)
        {
            ScanProgress.IsIndeterminate = false;
            ScanProgress.Value = p.Fraction;
            ProgressPercent.Text = $"{p.Percent}%";
        }
        else if (p.Phase == ScanPhase.Scanning)
        {
            // 遍历阶段总量未知（要先走完才知道有多少文件），所以这里仍然是
            // 不确定态。确定进度在上面的「总进度」里用容量估算给出。
            ScanProgress.IsIndeterminate = true;
            ProgressPercent.Text = "";
        }

        UpdateProgressDetail(p);

        SpeedText.Text = p.Phase is ScanPhase.Done or ScanPhase.Idle
            ? "" : $"{WinPath.FormatBytes(_speed)}/s";
    }

    /// <summary>
    /// 把进度模型推给界面。模型里的属性是纯计算值、没有变更通知，
    /// 所以统一在这里显式赋值（由 120ms 的 UI 定时器驱动，足够跟手，
    /// 又不会让高频进度事件淹没消息泵）。
    /// </summary>
    private void UpdateProgressDetail(ScanProgress p)
    {
        // 引擎把实时统计对象挂在进度快照上传过来（引用，不是拷贝）
        if (p.LiveStats is not null) _lastStats = p.LiveStats;

        Progress.Apply(p, _lastStats);
        Progress.SpeedBytesPerSec = _speed;

        ProgressStageName.Text = Progress.CurrentStageName;
        ProgressStageDetail.Text = Progress.CurrentStageDetail;
        ProgressOverall.Value = Progress.OverallFraction;
        ProgressOverallText.Text = Progress.OverallPercentText;
        ProgressNote.Text = Progress.TraversalEstimateNote;

        StatFiles.Text = Progress.FilesSeenText;
        StatBytes.Text = Progress.BytesSeenText;
        StatElapsed.Text = Progress.ElapsedText;
        StatSpeed.Text = Progress.SpeedText;
        StatDirs.Text = Progress.DirsSeenText;

        UpdateStageDots();
    }

    /// <summary>
    /// 阶段圆点上色。走 ItemsControl 的话拿不到每个 item 的控件引用，
    /// 而 WinUI 3 没有 DataTrigger/Setter 绑定可用，所以用 ContainerGenerator
    /// 取出实际容器再设背景色。5 个阶段，每次 tick 几十微秒，开销可忽略。
    /// </summary>
    /// <summary>
    /// 按模型动态搭建阶段步进器。
    ///
    /// 阶段数量来自 <see cref="ScanProgressModel"/>，界面不写死——但控件是
    /// 在代码里生成的：WinUI 3 的 ItemsControl 不公开容器生成器，拿不到每个
    /// item 的控件就没法按状态上色；阶段数固定只有 5 个，用 StackPanel + 命名
    /// 元素既简单又能在 tick 里直接改。
    /// </summary>
    private void BuildStageStrip()
    {
        StageStrip.Children.Clear();
        _stageDots.Clear();
        _stageCounts.Clear();
        _stageNames.Clear();

        var stages = Progress.Stages;
        for (int i = 0; i < stages.Count; i++)
        {
            var s = stages[i];

            var dot = new Border
            {
                Width = 10,
                Height = 10,
                CornerRadius = new CornerRadius(5),
                Background = _dotPending,
                VerticalAlignment = VerticalAlignment.Center,
            };

            var name = new TextBlock
            {
                Text = s.Name,
                FontSize = 12,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                HorizontalAlignment = HorizontalAlignment.Center,
            };

            var count = new TextBlock
            {
                Text = s.CountText,
                FontSize = 10,
                Opacity = 0.6,
                HorizontalAlignment = HorizontalAlignment.Center,
            };

            var block = new StackPanel
            {
                Spacing = 3,
                Children = { dot, name, count },
            };
            _stageDots.Add(dot);
            _stageNames.Add(name);
            _stageCounts.Add(count);

            var cell = new Grid();
            cell.Children.Add(block);
            Grid.SetColumn(cell, i * 2);
            StageStrip.Children.Add(cell);

            // 阶段之间的连接线：最后一个阶段后面不画
            if (i < stages.Count - 1)
            {
                var line = new Border
                {
                    Height = 1,
                    Width = 18,
                    Margin = new Thickness(6, 0, 6, 0),
                    VerticalAlignment = VerticalAlignment.Center,
                    Background = new SolidColorBrush(Windows.UI.Color.FromArgb(
                        0x80, 0xFF, 0xFF, 0xFF)),
                };
                Grid.SetColumn(line, i * 2 + 1);
                StageStrip.Children.Add(line);
            }
        }
    }

    private readonly List<Border> _stageDots = new();
    private readonly List<TextBlock> _stageNames = new();
    private readonly List<TextBlock> _stageCounts = new();

    private void UpdateStageDots()
    {
        for (int i = 0; i < _stageDots.Count && i < Progress.Stages.Count; i++)
        {
            var s = Progress.Stages[i];
            _stageDots[i].Background = StageColor(s.State);
            _stageCounts[i].Text = s.CountText;
            // 当前阶段的名字提亮，已完成的压暗，视觉上一眼能看出走到哪
            _stageNames[i].Opacity = s.State switch
            {
                StageState.Active => 1.0,
                StageState.Pending => 0.5,
                _ => 0.75,
            };
            _stageNames[i].FontWeight = s.State == StageState.Active
                ? Microsoft.UI.Text.FontWeights.SemiBold
                : Microsoft.UI.Text.FontWeights.Normal;
        }
    }

    private static readonly Brush _dotPending = NewBrush(0x60, 0xFF, 0xFF, 0xFF);
    private static readonly Brush _dotDone = NewBrush(0xFF, 0x4C, 0xC2, 0x6B);
    private static readonly Brush _dotActive = NewBrush(0xFF, 0x00, 0x7A, 0xFF);

    private static SolidColorBrush NewBrush(byte a, byte r, byte g, byte b) =>
        new(Windows.UI.Color.FromArgb(a, r, g, b));

    private static Brush StageColor(StageState state) => state switch
    {
        StageState.Done => _dotDone,
        StageState.Active => _dotActive,
        _ => _dotPending,
    };

    // =================================================================
    // 配置
    // =================================================================
    private ScanConfig BuildConfig()
    {
        // 根路径 = 已勾选的盘符 + 已勾选的文件夹（去重）。
        // 不再有"附加目录"文本框：多行文本框没法逐项勾选，
        // 想去掉一个目录只能手改文本，改错了还看不出来。
        var roots = _viewModel.SelectedRoots.ToList();

        return new ScanConfig
        {
            Roots = roots,
            ExcludeDirNames = SplitSet(ExcludeDirsBox.Text, ScanConfig.DefaultExcludeDirNames),
            IncludeSuffixes = SplitSet(IncludeExtBox.Text, Array.Empty<string>()),
            ExcludeSuffixes = SplitSet(ExcludeExtBox.Text, Array.Empty<string>()),
            MinSize = Math.Max(1, WinPath.ParseSize(MinSizeBox.Text, 1)),
            MaxSize = string.IsNullOrWhiteSpace(MaxSizeBox.Text)
                ? null : WinPath.ParseSize(MaxSizeBox.Text, 0),
            IncludeEmptyFiles = EmptyCheck.IsChecked == true,
            SkipCloudPlaceholders = CloudCheck.IsChecked == true,
            VerifyBytes = VerifyCheck.IsChecked == true,
            TreatHardLinksAsOne = HardLinkCheck.IsChecked == true,
            KeepRule = (KeepRule)Math.Max(0, KeepRuleBox.SelectedIndex),
        };
    }

    private static HashSet<string> SplitSet(string? text, IEnumerable<string> fallback)
    {
        var set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if (string.IsNullOrWhiteSpace(text)) { foreach (var f in fallback) set.Add(f); return set; }
        foreach (var part in text.Split(',', StringSplitOptions.RemoveEmptyEntries))
        {
            var t = part.Trim();
            if (t.Length == 0) continue;
            set.Add(t.StartsWith('.') ? t : "." + t);
        }
        return set;
    }

    // =================================================================
    // 扫描
    // =================================================================
    private async void OnStartScan(object sender, RoutedEventArgs e)
    {
        await RunScanAsync();
    }

    /// <summary>
    /// 命令行 --autoscan 用：填好附加目录并直接开扫，界面停在可截图状态。
    /// 走的是和点「开始扫描」完全相同的代码路径，不存在只为截图存在的分支。
    /// </summary>
    public void AutoScan(string dir)
    {
        _viewModel.AddFolder(dir);
        // 附加目录是显式指定的，别再被盘符勾选干扰：全不选盘符。
        foreach (var d in _viewModel.Drives) d.IsSelected = false;
        UpdateSelection();
        _ = RunScanAsync();
    }

    /// <summary>
    /// 命令行 --demo-progress 用：不开真扫描，把进度面板填成一个可信的
    /// 「正在扫描中」状态并常驻，专门用来验证进度界面本身的视觉与布局。
    ///
    /// 存在的意义：小夹具扫完只要几十毫秒，截图根本抓不到进度面板；
    /// 而进度 UI 的问题（阶段没点亮、百分比算错、统计不刷新）恰恰只在
    /// 「面板可见」时才能看出来。
    /// </summary>
    public void DemoProgress(long volumeBytes = 512L * 1024 * 1024 * 1024)
    {
        Progress.Reset();
        Progress.VolumeTotalBytes = volumeBytes;

        // 统计要通过 ScanStats 进来，而不是直接给模型赋值：
        // UpdateProgressDetail -> Progress.Apply(p, _lastStats) 会用引擎的
        // 实时统计覆盖模型上的值（真实运行时就是这样来的）。
        // 所以这里必须喂一份真的 ScanStats，否则面板上的统计全是 0。
        _lastStats = new ScanStats
        {
            FilesSeen = 214_688,
            DirsVisited = 18_302,
            BytesSeen = 187L * 1024 * 1024 * 1024,
            BytesKept = 41L * 1024 * 1024 * 1024,
            SizeCandidates = 8_940,
        };
        _speed = 148.6 * 1024 * 1024;
        _lastSpeedBytes = _lastStats.BytesSeen;
        _lastSpeedStamp = Environment.TickCount64;

        // 前两阶段已完成、第三阶段进行中——覆盖圆点三态与百分比计算
        Progress.HardLink.State = StageState.Done;
        Progress.HardLink.Done = 1_204;
        Progress.HardLink.Total = 1_204;
        Progress.QuickHash.State = StageState.Active;
        Progress.QuickHash.Done = 3_517;
        Progress.QuickHash.Total = 8_940;
        Progress.CurrentStageName = "快速哈希";
        Progress.CurrentStageDetail = "3,517 / 8,940 个候选";

        ProgressDetailPanel.Visibility = Visibility.Visible;
        UpdateProgressDetail(new ScanProgress
        {
            Phase = ScanPhase.QuickHash,
            Done = Progress.QuickHash.Done,
            Total = Progress.QuickHash.Total,
        });

        // 让"已用时间"不至于停在 00:00
        Thread.Sleep(1200);
        UpdateProgressDetail(new ScanProgress
        {
            Phase = ScanPhase.QuickHash,
            Done = Progress.QuickHash.Done,
            Total = Progress.QuickHash.Total,
        });
    }

    private async Task RunScanAsync()
    {
        if (_scanTask is { IsCompleted: false }) return;

        ScanConfig config;
        try { config = BuildConfig(); }
        catch (Exception ex)
        {
            ShowStatus(InfoBarSeverity.Error, "配置有误", ex.Message);
            return;
        }
        if (config.Roots.Count == 0)
        {
            ShowStatus(InfoBarSeverity.Warning, "请选择扫描范围",
                "至少勾选一个盘符，或在「附加目录」里填一个路径。");
            return;
        }

        SetScanning(true);
        ShowStatus(InfoBarSeverity.Informational, "正在扫描", "首次全盘扫描可能需要几分钟。");
        _lastSpeedBytes = 0; _lastSpeedStamp = Environment.TickCount64; _speed = 0;
        _lastStats = new ScanStats();
        _latest = new ScanProgress { Phase = ScanPhase.Scanning, Detail = "准备中" };

        // 遍历阶段没有已知总量，用所涉卷的总容量做估算基准。
        Progress.Reset();
        Progress.VolumeTotalBytes = EstimateVolumeBytes(config.Roots);
        ProgressDetailPanel.Visibility = Visibility.Visible;
        UpdateProgressDetail(_latest);

        _scanCts = new CancellationTokenSource();

        // 进度通过"单槽"回传：高频回调覆盖写，不会阻塞工作线程。
        // UI 线程由 _uiTimer 定时取快照并刷新，避免高频事件淹没 UI 消息泵。
        void OnProgress(ScanProgress p) => _latest = p;

        var engine = new ScanEngine(config, _scanCts, OnProgress);

        _scanTask = Task.Run(() => engine.Run());

        var result = await _scanTask;
        _scanCts.Dispose();
        _scanCts = null;
        _uiTimer.Stop();

        // 收尾时把最终状态推一次（定时器已停，最后一帧要手动刷）
        Progress.Apply(new ScanProgress
        {
            Phase = result.Phase,
            Detail = result.Summary,
            LiveStats = result.Stats,
        }, result.Stats);
        UpdateProgressDetail(new ScanProgress
        {
            Phase = result.Phase,
            Detail = result.Summary,
            LiveStats = result.Stats,
        });
        SetScanning(false);

        if (result.Phase == ScanPhase.Failed)
        {
            ShowStatus(InfoBarSeverity.Error, "扫描出错", result.Error ?? "");
            return;
        }
        if (result.Cancelled)
        {
            ShowStatus(InfoBarSeverity.Warning, "已取消", "没有修改任何文件。");
            return;
        }

        // 中途无候选的阶段标成"已跳过"，而不是留一个永远走不完的进度条
        if (result.Stats.SizeCandidates == 0)
            Progress.MarkSkipped(Progress.Traversal, "遍历完成，未找到任何文件");
        else if (result.Stats.QuickCandidates == 0)
            Progress.MarkSkipped(Progress.QuickHash, "没有大小相同的候选文件");
        else if (result.Stats.HardLinksCollapsed == 0 && result.Stats.SizeCandidates > 0)
            Progress.MarkSkipped(Progress.HardLink, "没有硬链接需要折叠");
        UpdateProgressDetail(new ScanProgress { Phase = ScanPhase.Done, LiveStats = result.Stats });
        ProgressDetailPanel.Visibility = Visibility.Collapsed;

        _viewModel.Load(result);
        SummaryText.Text = result.Summary;
        SelectedRow = null;          // 旧结果里的行已失效，选中态要清掉
        UpdateSelection();
        UpdateEmptyStateForResult(result);

        if (result.Groups.Count == 0)
        {
            ShowStatus(InfoBarSeverity.Success, "扫描完成", "没有发现重复文件。");
        }
        else
        {
            ShowStatus(InfoBarSeverity.Success, "扫描完成",
                $"发现 {result.Groups.Count:N0} 个重复组，可释放 {result.ReclaimableText}。");
        }
    }

    /// <summary>
    /// 估算本次扫描涉及的总容量，用作遍历阶段进度的分母。
    /// 同一个卷可能被多个根路径命中（例如 C:\ 和 C:\Users），所以按卷去重，
    /// 否则分母会偏大、进度条永远走不满。
    /// </summary>
    private static long EstimateVolumeBytes(IReadOnlyList<string> roots)
    {
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long total = 0;
        foreach (var r in roots)
        {
            var vol = Path.GetPathRoot(WinPath.Display(r));
            if (string.IsNullOrEmpty(vol) || !seen.Add(vol)) continue;
            if (Win32.GetDiskFreeSpaceExW(vol, out _, out ulong cap, out _))
                total += (long)Math.Min(cap, (ulong)long.MaxValue);
        }
        return total;
    }

    private void OnCancelScan(object sender, RoutedEventArgs e)
    {
        if (_scanCts is null) return;
        _scanCts.Cancel();
        PhaseText.Text = "正在停止…（当前系统调用可能需要几秒才能返回）";
        CancelButton.IsEnabled = false;
    }

    private void SetScanning(bool busy)
    {
        StartButton.Content = busy ? "扫描中…" : "开始扫描";
        StartButton.IsEnabled = !busy;
        CancelButton.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        CancelButton.IsEnabled = busy;
        ScanProgressPanel.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        if (busy) { ScanProgress.IsIndeterminate = true; _uiTimer.Start(); }
        else { _uiTimer.Stop(); ScanProgress.IsIndeterminate = false; ScanProgress.Value = 0; }
    }

    // =================================================================
    // 结果交互
    // =================================================================
    private void OnFilterChanged(AutoSuggestBox sender, AutoSuggestBoxTextChangedEventArgs args)
    {
        if (args.Reason != AutoSuggestionBoxTextChangeReason.UserInput) return;
        _viewModel.SetFilter(sender.Text);
        UpdateSelection();
    }

    private void OnSortChanged(object sender, SelectionChangedEventArgs e)
    {
        if (SortBox.SelectedIndex < 0) return;
        string[] modes = { "reclaimable", "size", "count", "name", "path", "mtime" };
        _viewModel.SetSort(modes[SortBox.SelectedIndex]);
        UpdateSelection();
    }

    private void OnSortDirectionToggle(object sender, RoutedEventArgs e)
    {
        _viewModel.ToggleSortDirection();
        SortDirToggle.Content = _viewModel.SortDescending ? "降序" : "升序";
    }

    private void OnCheckAll(object s, RoutedEventArgs e) { _viewModel.CheckAll(); UpdateSelection(); }
    private void OnCheckNone(object s, RoutedEventArgs e) { _viewModel.CheckNone(); UpdateSelection(); }
    private void OnInvert(object s, RoutedEventArgs e) { _viewModel.Invert(); UpdateSelection(); }
    private void OnExpandAll(object s, RoutedEventArgs e) { _viewModel.ExpandAll(); }
    private void OnCollapseAll(object s, RoutedEventArgs e) { _viewModel.CollapseAll(); }

    // =================================================================
    // 保留项：逐组指定
    // =================================================================
    /// <summary>单击：只选中，并把"保留此项"按钮点亮到对应的那一行。</summary>
    private void OnResultItemClick(object sender, ItemClickEventArgs e)
    {
        if (e.ClickedItem is not ResultRow row) return;
        SelectedRow = row;
        UpdateKeeperBar();
    }

    /// <summary>双击成员行：把它设为该组的保留项。这是最直接的入口。</summary>
    private void OnResultDoubleTapped(object sender, DoubleTappedRoutedEventArgs e)
    {
        if (SelectedRow is not { Kind: RowKind.File } row) return;
        SetKeeper(row, row.File!.Path);
    }

    private void OnKeepThisOne(object s, RoutedEventArgs e)
    {
        if (SelectedRow is not { Kind: RowKind.File, File: not null } row) return;
        SetKeeper(row, row.File!.Path);
    }

    private void OnResetKeeper(object s, RoutedEventArgs e)
    {
        if (SelectedRow is null) return;
        _viewModel.SetKeeperOverride(SelectedRow.GroupId, null);
        UpdateSelection();
        UpdateKeeperBar();
        ShowStatus(InfoBarSeverity.Informational, "已恢复按规则自动选择",
            KeepRuleNames.Label(_viewModel.KeepRule));
    }

    private void OnResetAllKeepers(object s, RoutedEventArgs e)
    {
        _viewModel.ClearAllKeeperOverrides();
        UpdateSelection();
        UpdateKeeperBar();
    }

    private void SetKeeper(ResultRow row, string path)
    {
        if (!_viewModel.SetKeeperOverride(row.GroupId, path))
        {
            ShowStatus(InfoBarSeverity.Error, "指定失败", "该文件不属于这一组。");
            return;
        }
        UpdateSelection();
        UpdateKeeperBar();

        var file = row.File!;
        var others = row.Group.Count - 1;
        ShowStatus(InfoBarSeverity.Success, "已设为保留项",
            $"{file.Name}（{file.TimeRelativeText}）· " +
            $"同组另外 {others} 个副本将进入待删清单");
    }

    /// <summary>刷新"保留此项"工具条的状态。没选中成员行时整条禁用。</summary>
    private void UpdateKeeperBar()
    {
        bool canSet = SelectedRow is { Kind: RowKind.File, File: not null };
        KeepThisOneButton.IsEnabled = canSet;
        ResetKeeperButton.IsEnabled = canSet;

        bool hasOverride = canSet
            && _viewModel.GetKeeperOverride(SelectedRow!.GroupId) is not null;
        KeeperInfoText.Text = !canSet
            ? "选中一个文件后可指定保留哪一份"
            : hasOverride
                ? "本组已手动指定保留项"
                : "本组按上方规则自动选择";
    }

    private ResultRow? SelectedRow { get; set; }

    private void OnKeepRuleChanged(object sender, SelectionChangedEventArgs e)
    {
        if (KeepRuleBox.SelectedIndex < 0) return;
        _viewModel.KeepRule = (KeepRule)KeepRuleBox.SelectedIndex;
        UpdateKeepRuleHint();
        UpdateSelection();
    }

    private void UpdateKeepRuleHint()
    {
        if (KeepRuleBox.SelectedIndex < 0) return;
        var rule = (KeepRule)KeepRuleBox.SelectedIndex;
        KeepRuleHint.Text = rule switch
        {
            KeepRule.Newest => "同步工具产生的副本通常最新 —— 请确认要删的正是它们。",
            KeepRule.Oldest => "会优先保留旧备份、删除较新的副本。",
            KeepRule.ShortestPath => "路径短的通常是主副本。",
            KeepRule.LongestPath => "路径长的通常是备份/归档副本。",
            KeepRule.Shallowest => "层级浅的通常是主副本。",
            KeepRule.Deepest => "层级深的通常是备份/归档副本。",
            _ => "",
        };
    }

    /// <summary>
    /// 刷新所有"随勾选/文件夹变化而变"的界面状态。
    ///
    /// 文件夹列表的显隐放在这里而不是用 x:Bind：OneWay 的 x:Bind 只在绑定
    /// 那一刻求值一次，之后改 ViewModel 不会重新读——要它跟着变就得走
    /// INotifyPropertyChanged + 转换器，而这里三个元素的显隐逻辑就这么点，
    /// 老实设属性更省事也更可靠。
    /// </summary>
    private void UpdateSelection()
    {
        SelectionText.Text = _viewModel.SelectionText;

        bool has = _viewModel.Folders.Count > 0;
        var v = has ? Visibility.Visible : Visibility.Collapsed;
        FolderListCard.Visibility = v;
        ClearFoldersButton.Visibility = v;
        FolderHintText.Visibility = v;

        UpdateKeeperBar();
    }

    private void ShowEmptyState(string detail)
    {
        EmptyState.Visibility = Visibility.Visible;
        EmptyStateDetail.Text = detail;
        ResultsList.Visibility = Visibility.Collapsed;
    }

    private void UpdateEmptyStateForResult(ScanResult result)
    {
        if (result.Groups.Count == 0)
        {
            ShowEmptyState("没有发现重复文件。可以调低「文件大小下限」、"
                         + "清空「排除扩展名 / 排除目录名」，或勾选「包含 0 字节文件」。");
            ExportCsvButton.Visibility = Visibility.Collapsed;
            ExportScriptButton.Visibility = Visibility.Collapsed;
            CleanButton.Visibility = Visibility.Collapsed;
            return;
        }
        EmptyState.Visibility = Visibility.Collapsed;
        ResultsList.Visibility = Visibility.Visible;
        ExportCsvButton.Visibility = Visibility.Visible;
        ExportScriptButton.Visibility = Visibility.Visible;
        CleanButton.Visibility = Visibility.Visible;
    }

    // =================================================================
    // 左侧面板
    // =================================================================
    private void OnSelectAllDrives(object s, RoutedEventArgs e)
    {
        foreach (var d in _viewModel.Drives) d.IsSelected = true;
    }

    private void OnSelectNoDrives(object s, RoutedEventArgs e)
    {
        foreach (var d in _viewModel.Drives) d.IsSelected = false;
    }

    private async void OnBrowseDir(object sender, RoutedEventArgs e)
    {
        var picker = new FolderPicker { SuggestedStartLocation = PickerLocationId.ComputerFolder };
        picker.FileTypeFilter.Add("*");
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        var folder = await picker.PickSingleFolderAsync();
        if (folder is null) return;

        if (_viewModel.AddFolder(folder.Path))
            ShowStatus(InfoBarSeverity.Success, "已加入扫描范围", folder.Path);
        else
            ShowStatus(InfoBarSeverity.Informational, "该文件夹已在列表中",
                "已把它重新勾选。" + folder.Path);
        UpdateSelection();
    }

    private void OnRemoveFolder(object sender, RoutedEventArgs e)
    {
        // 从 DataContext 拿回这一行，不要用 Tag={x:Bind}：
        // 无路径的 x:Bind 不是合法语法，会让 XamlCompiler 失败且只报「错误: 1」。
        if (sender is FrameworkElement { DataContext: ScopeFolder folder })
        {
            _viewModel.RemoveFolder(folder);
            UpdateSelection();
        }
    }

    private void OnClearFolders(object sender, RoutedEventArgs e)
    {
        _viewModel.ClearFolders();
        UpdateSelection();
    }

    private void OnSplitterDragCompleted(object sender, DragCompletedEventArgs e)
    {
        // 本 SDK 的 DragCompletedEventArgs 只有 Handled / Cancel，没有偏移量，
        // 所以直接量分隔条当前的位置。
        try
        {
            var transform = ScopeSplitter.TransformToVisual(RootGrid);
            if (transform is null) return;
            var point = transform.TransformPoint(new Windows.Foundation.Point(0, 0));
            ScopePanel.Width = Math.Clamp(point.X, 260, 560);
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine($"调整左栏宽度失败: {ex.Message}");
        }
    }

    // =================================================================
    // 导出
    // =================================================================
    private async void OnExportCsv(object sender, RoutedEventArgs e)
    {
        if (_viewModel.Result is null) return;
        var picker = new FileSavePicker
        {
            SuggestedStartLocation = PickerLocationId.DocumentsLibrary,
            SuggestedFileName = "duplicates",
        };
        picker.FileTypeChoices.Add("CSV 文件", new[] { ".csv" });
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        var file = await picker.PickSaveFileAsync();
        if (file is null) return;

        var plan = _viewModel.BuildPlan();
        using var stream = await file.OpenStreamForWriteAsync();
        using var writer = new StreamWriter(stream);
        await writer.WriteLineAsync("组号,摘要,单文件大小,成员数,保留文件,处置,路径,目录,修改时间");
        int gi = 0;
        foreach (var group in _viewModel.Result.Groups)
        {
            gi++;
            var (keeper, toDelete) = group.Split(_viewModel.KeepRule);
            foreach (var m in toDelete)
            {
                await writer.WriteLineAsync(string.Join(",",
                    gi, group.DigestHex[..Math.Min(12, group.DigestHex.Length)],
                    m.Size, group.Count, keeper.Name, "删除",
                    Csv(m.Path), Csv(m.Directory), m.TimeText));
            }
        }
        ShowStatus(InfoBarSeverity.Success, "已导出", file.Path);
    }

    private static string Csv(string s) => "\"" + s.Replace("\"", "\"\"") + "\"";

    private async void OnExportScript(object sender, RoutedEventArgs e)
    {
        if (_viewModel.Result is null) return;
        var picker = new FileSavePicker
        {
            SuggestedStartLocation = PickerLocationId.DocumentsLibrary,
            SuggestedFileName = "remove_duplicates",
        };
        picker.FileTypeChoices.Add("PowerShell 脚本", new[] { ".ps1" });
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        var file = await picker.PickSaveFileAsync();
        if (file is null) return;

        var lines = new List<string>
        {
            "# 由 Dump Search 生成 —— 请人工审阅后再执行",
            $"# 策略：{KeepRuleNames.Label(_viewModel.KeepRule)}",
            "# 动作：移入回收站（可撤销）",
            "",
        };
        int total = 0;
        foreach (var group in _viewModel.Result.Groups)
        {
            var (_, toDelete) = group.Split(_viewModel.KeepRule);
            if (toDelete.Count == 0) continue;
            lines.Add($"# --- {group.SizeText} x{group.Count} ---");
            foreach (var m in toDelete)
            {
                lines.Add("Add-Type -AssemblyName Microsoft.VisualBasic; "
                          + "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                          + $"'{m.Path.Replace("'", "''")}', 'OnlyErrorDialogs', 'SendToRecycleBin')");
                total++;
            }
        }
        lines.Add("");
        lines.Add($"# 合计 {total} 个文件");

        using var stream = await file.OpenStreamForWriteAsync();
        using var writer = new StreamWriter(stream);
        await writer.WriteLineAsync(string.Join("\n", lines));
        ShowStatus(InfoBarSeverity.Success, "已生成脚本", "请人工审阅后再执行。");
    }

    // =================================================================
    // 清理：预览 + 审计 + 二次确认
    // =================================================================
    private async void OnPreviewClean(object sender, RoutedEventArgs e)
    {
        var plan = _viewModel.BuildPlan();
        var warnings = DeletePlanner.Audit(plan);
        if (plan.FileCount == 0)
        {
            ShowStatus(InfoBarSeverity.Warning, "没有可清理的内容", "请先勾选至少一个重复组。");
            return;
        }

        var dialog = new CleanPreviewDialog(plan, warnings, _viewModel.KeepRule);
        var root = (Microsoft.UI.Xaml.UIElement)RootGrid;
        var result = await dialog.ShowAsync();
        if (result != ContentDialogResult.Primary) return;

        // 确认后才执行；执行前再过一次 dry-run 复核路径状态
        ProgressDeleteOverlay(true);
        var outcome = await Task.Run(() => RecycleBin.TrashFiles(
            plan.Targets, (d, t) =>
            {
                DispatcherQueue.TryEnqueue(() =>
                {
                    PhaseText.Text = $"正在移入回收站 {d:N0}/{t:N0}";
                });
            }));
        ProgressDeleteOverlay(false);

        if (outcome.Failed.Count == 0)
        {
            ShowStatus(InfoBarSeverity.Success, "清理完成",
                $"{outcome.Summary}。清空回收站后才会真正释放 {plan.ReclaimableText}。");
        }
        else
        {
            var detail = string.Join("\n",
                outcome.Failed.Take(10).Select(f => $"{f.Path}\n    {f.Reason}"));
            ShowStatus(InfoBarSeverity.Error, "部分失败",
                $"{outcome.Summary}\n{detail}");
        }
        _viewModel.Clear();
        SummaryText.Text = "文件已变动，建议重新扫描。";
        UpdateSelection();
        ShowEmptyState("清理完成。重新扫描可刷新结果。");
    }

    private void ProgressDeleteOverlay(bool busy)
    {
        StartButton.IsEnabled = !busy;
        CleanButton.IsEnabled = !busy;
        if (busy) { ScanProgressPanel.Visibility = Visibility.Visible; ScanProgress.IsIndeterminate = true; }
        else { ScanProgressPanel.Visibility = Visibility.Collapsed; }
    }

    // =================================================================
    private void ShowStatus(InfoBarSeverity severity, string title, string message)
    {
        StatusBar.Severity = severity;
        StatusBar.Title = title;
        StatusBar.Message = message;
        StatusBar.IsOpen = true;
    }
}
