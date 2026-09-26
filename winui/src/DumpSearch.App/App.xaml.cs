using Microsoft.UI.Xaml;

namespace DumpSearch.App;

public partial class App : Application
{
    private Window? _window;

    public App()
    {
        InitializeComponent();
        UnhandledException += (_, e) =>
        {
            System.Diagnostics.Debug.WriteLine($"[未处理异常] {e.Exception}");
            e.Handled = false;
        };
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        var argv = Environment.GetCommandLineArgs();

        // --selftest <目录>：不开窗口跑引擎自检，方便脚本/CI 验证。
        // 全程只读，绝不删除任何文件。
        int flag = Array.IndexOf(argv, "--selftest");
        if (flag >= 0 && flag + 1 < argv.Length)
        {
            Environment.Exit(SelfTest.Run(argv[flag + 1]));
            return;
        }

        // 窗口必须留一个强引用：没有托管引用时 WinUI 会把它回收掉，进程直接退出。
        _window = new MainWindow();
        _window.Activate();

        // --autoscan <目录>：启动后自动扫描该目录，界面停在有结果的状态。
        // 走的是和点按钮完全一样的路径，方便截图和冒烟验证。
        int auto = Array.IndexOf(argv, "--autoscan");
        if (auto >= 0 && auto + 1 < argv.Length && _window is MainWindow main)
            main.AutoScan(argv[auto + 1]);

        // --demo-progress：只把进度面板填成"扫描中"的样子并常驻，
        // 用来验证进度界面本身（小夹具扫太快，截图抓不到）。
        if (argv.Contains("--demo-progress") && _window is MainWindow demo)
            demo.DemoProgress();
    }
}
