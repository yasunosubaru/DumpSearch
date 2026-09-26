# Dump Search · WinUI 3 前端

原生 C# / WinUI 3 界面，与仓库根目录的 Python/Tkinter 前端**并存**。
两者共用同一套算法设计与安全契约（分层过滤 → 硬链接折叠 → 逐字节校验 →
删除计划 → 审计），但代码各写一份，因为 UI 框架完全不同。

```
winui/
├─ nuget.config                 NuGet 镜像源（本机 nuget.org 不可达）
├─ src/DumpSearch.App/          唯一的应用项目
│  ├─ App.xaml(.cs)             入口 + 命令行分流
│  ├─ MainWindow.xaml(.cs)      主界面
│  ├─ SelfTest.cs               引擎自检（不开窗口）
│  ├─ app.manifest              DPI / UTF-8 / longPathAware
│  ├─ Assets/DumpSearch.ico     应用图标（由 tools/make_icon.py 生成）
│  ├─ Core/                     与前端无关的引擎（可独立测试）
│  │  ├─ Win32.cs               GetFileAttributesExW / 硬链接 file id
│  │  ├─ WinPath.cs             长路径前后缀、尺寸/时间格式化
│  │  ├─ Models.cs              ScanConfig / FileEntry / DuplicateGroup
│  │  ├─ Hasher.cs              采样 + 完整 SHA-256 + 读取后复检
│  │  ├─ Scanner.cs             目录遍历、过滤、统计
│  │  ├─ ScanEngine.cs          四阶段流水线
│  │  ├─ DeletePlanner.cs       保留策略 + 计划 + 审计
│  │  └─ RecycleBin.cs          回收站删除（支持 dry-run）
│  ├─ ViewModels/
│  │  ├─ ResultViewModel.cs     结果列表（过滤/排序/勾选/展平）
│  │  └─ ScanProgressModel.cs   扫描进度（阶段步进器 + 加权总进度）
│  └─ Styles/Tokens.xaml        设计令牌（间距/圆角/画刷/样式）
└─ tools/                       构建、验证与资源生成脚本
```

## 启动

exe 在 `src\DumpSearch.App\bin\...` 这样的构建产物深处，**不用去找**——
项目根目录有两个入口：

| 入口 | 说明 |
| --- | --- |
| `Dump Search (WinUI 3).lnk` | 根目录，带应用图标，双击即开 |
| `启动 WinUI 3 界面.bat` | 同样的启动逻辑，脚本版 |

快捷方式**指向 .bat 而不是 exe**：exe 路径依赖构建配置（Debug/Release、TFM），
改一次配置快捷方式就指向空气了。启动器每次现找，找不到才构建。
重新生成：`python winui\tools\make_shortcut.py`（加 `--desktop` 可在桌面也放一份）。

## 命令行

```powershell
cd src\DumpSearch.App

# 引擎自检：不开窗口，62 项断言，全程不删除任何文件
.\bin\Release\net8.0-windows10.0.19041.0\DumpSearch.exe --selftest <目录>

# 启动后自动扫描某目录（走的是和点「开始扫描」完全相同的代码路径）
.\bin\Release\net8.0-windows10.0.19041.0\DumpSearch.exe --autoscan <目录>

# 只把进度面板填成"扫描中"的样子并常驻，用于验证进度界面
.\bin\Release\net8.0-windows10.0.19041.0\DumpSearch.exe --demo-progress
```

`--selftest` 退出码：0 = 全过，1 = 有失败项，2 = 目录不存在。

## 重新生成图标

```powershell
python tools\make_icon.py
```

输出 `Assets/DumpSearch.ico`（16/24/32/48/64/128/256 七档）+ 同尺寸 PNG + `tools/icon_preview.png`。
改设计只需改脚本里的坐标常量再跑一次，不需要开任何画图工具。

图标通过 csproj 的 `<ApplicationIcon>` 嵌进 exe 本身，所以资源管理器和任务栏里
都是它；窗口标题栏另由 `AppWindow.SetIcon` 设置。


## 验证

```powershell
# XAML 静态检查（5 项，5 毫秒）
python tools\lint_xaml.py -v

# 引擎自检（116 项）
.\bin\Release\...\DumpSearch.exe --selftest <目录>

# 窗口截图（PrintWindow，不依赖前台顺序）
python tools\shot.py .\bin\Release\...\DumpSearch.exe out.png 10 --autoscan <目录>

# Python 端 6 套验证
python ..\tools\verify_all.py
```

## 扫描进度

界面上的进度不是一个进度条，而是**五级阶段步进器 + 加权总进度 + 实时统计**：

```
遍历目录 → 折叠硬链接 → 快速哈希 → 完整哈希 → 逐字节校验
  ●          ●            ◉            ○            ○
已完成      已完成       进行中       未开始       未开始
```

**核心约束：总量未知的阶段绝不编造百分比。**

- 「遍历目录」要先走完才知道有多少文件，所以 `Total` 恒为 0，界面只显示
  「已枚举 N 个文件」而**不显示** `0 / 0` 或任何百分比。
- 总进度里遍历阶段用**「已读字节 / 所选卷总容量」**参与计算（容量来自
  `GetDiskFreeSpaceExW`），这是有依据的估算，界面底部有一行小字明确标注
  「按已读字节 / 卷容量估算」。拿不到容量时 `TraversalFraction` 返回 **-1**，
  此时总进度会停在低位不动——这比编一个假百分比诚实。
- 中途无候选的阶段（`QuickCandidates == 0` 之类）标成**已跳过**，
  而不是留一个永远走不完的进度条。

实时统计（已扫描文件 / 已读数据 / 已用时间 / 速度 / 目录数）靠
`ScanProgress.LiveStats` 传递——它是引擎 `_stats` 的**引用**而不是拷贝：
进度回调是单槽覆盖写，拷贝一次就是一次分配，而且拷出来的数字比回调那一刻更旧。

---

# 三个新功能

## 1. 只扫指定文件夹

左侧「只扫指定文件夹」是可勾选的列表，不是多行文本框。

- 「浏览添加文件夹…」走系统的 `FolderPicker`
- 逐项勾选：取消勾某个 = 只扫其余文件夹
- 「移除」逐项删；重复添加同一路径不会重复，而是把它重新勾上
- 同一路径按**不区分大小写**去重

为什么不用文本框：多行 TextBox 里没法逐项勾选，想去掉一个目录只能手改文本、
改错了看不出来；而长路径恰恰是最需要用选择器指定、又最容易手打错的路径。

## 2. 逐组指定保留哪个文件

全局保留规则（最新/最旧/最短路径…）只能按**一个维度**猜，而
「这个副本我要留」是逐组的、基于对具体位置和时间的判断。

- **双击成员行** = 把这一份设为该组的保留项（最直接的入口）
- 或选中行后点「保留此项」
- 「按规则」取消本组的手动指定；「全部按规则」清除所有
- 被指定的行显示「保留此项」徽标；底部状态栏显示「手动指定保留 N 组」
- 删除计划审计会明确提示「其中 N 个重复组是你手动指定保留项的」

**安全设计**：`DeletePlanner.Build` 收到覆盖时仍会独立校验
——指定的文件必须真的在这一组里，否则**退回全局规则**而不是照做。
所以即使绕过界面直接传错路径，也不会把错的文件留在删除清单外。
自检里专门验了这一条。

所有「谁被保留」的判断都走 `ResultRow.EffectiveKeeper` 这一个入口
（界面、计划、审计同一口径）——分散判断迟早会出现「界面说保留 A、计划删 A」。

## 3. 可读排版：位置 + 时间

```
#   文件 / 所在位置            大小      份数    可释放   修改时间        处理    所在卷
    report_v2.txt          11.23 KB      2   11.23 KB  今天 17:14       保留    C:
    在 …\docs\b                                                   ◆ 今天 17:14
```

- **有表头**，列宽与数据行由 `tools\lint_xaml.py` 机械校验一致
- **文件名和所在位置合成一列**：判断该删哪个靠的是「哪个文件 + 放在哪」，
  拆到两个远隔的列里要来回对视线，不如并排放
- **时间列主显相对时间**（「今天 17:14」「3 天前」）。让人心算两个
  `yyyy-MM-dd` 的差值是没必要的负担；绝对时间在悬停详情里
- 天数差按**日历日**算而不是精确时长：25 小时前应该算「昨天」
- 组行显示时间跨度（`全部同为 今天 17:14` 或 `8月6日 → 今天 17:14`）。
  按**显示精度**判断是否同一时间——同一秒内写完的文件 FILETIME 仍差几百个
  100ns 刻度，直接比会输出「今天 17:14 → 今天 17:14」这种毫无信息量的东西

## 4. 悬停详情

鼠标停到任意一行，浮层给出：

- 完整绝对路径（等宽字体）
- 精确字节数 + 相对时间和绝对时间
- 位置（所在卷 + 精简后的目录）
- 所属重复组：副本数、摘要前 12 位、是否已逐字节校验
- 处理方式（保留（你指定的）/ 保留（按规则）/ 删除）
- **组内对比**——判断「删哪个」最有用的一块：

```
▶ 保留  ◆ 今天 17:14       11.23 KB  …\docs\b
   删除     今天 17:14       11.23 KB  …\docs\a
```

> **刻意不做文件内容预览。** 那需要读磁盘：悬停时读会卡住 UI，
> 而创建时间和访问时间都要为每个文件额外开一次句柄——在遍历阶段对每个文件
> 多开一次会把扫描拖慢一个数量级。所以详情只用**扫描时已经在手**的数据。
> 真要做内容预览，正确做法是扫描时按需采样存进 `FileEntry`，
> 单独开一轮工作。

> **浮层的视觉验证做不了**：它是独立弹出窗口，而桌面上总有别的置顶窗口
> （agent UI 等）占着前台，鼠标根本到不了目标行，`PrintWindow` 只能抓主窗口
> 自己的重定向表面。所以验证方式是断言**内容**（自检第 10 节，17 项），
> 而不是截图。`tools\shot_hover.py` 保留了按 PID 找浮层窗口的逻辑，
> 在没有前窗遮挡的环境里可以直接用。

---

## 新增的 XAML 静态检查

`tools\lint_xaml.py` 把「编译能过、运行时才崩」的坑挡在构建之前。
今天连撞三次同一类：XamlCompiler 一声不吭，进程启动时抛
`STATUS_STOWED_RESOURCES(0xC000027B)` 崩在 `Microsoft.UI.Xaml.dll`：

```powershell
python tools\lint_xaml.py -v
```

检查项：

1. `x:Double` 资源里不许出现 `*` 等非数字
2. `TextBlock` 不许用 `Background` / `Padding` / `CornerRadius`（WPF 遗留）
3. `ColumnDefinition.Width` 不许引用资源（`GridLength` 运行时解析不到值）
4. 结果表表头与数据行的列宽必须逐列相等
5. `MainWindow` 引用的 `StaticResource` 必须在 `Tokens.xaml` 里有定义

---

# 构建踩坑记录

这一节是这个项目最有价值的部分。**本机环境**（.NET 10 SDK / Windows App
Runtime 1.8 / Windows 11 26100）下有 8 个坑，其中 4 个会导致
「构建只报一句 `错误: 1`，不给任何行号」。

## 1. XamlCompiler 失败时不输出任何诊断信息

XAML 编译失败时，MSBuild 只给你：

```
error MSB3073: 命令 "XamlCompiler.exe" 已退出，代码为 1。
```

**没有文件名、没有行号、没有原因。** `-v n` 也救不了——只有主 csc 同时失败时
才会经 `_OnXamlPreCompileError` 露出真实错误。

定位办法（按代价从低到高）：

1. 临时在 csproj 里把 XAML 从编译里摘掉，让主 csc 直接报：
   ```xml
   <Page Remove="**\*.xaml" />
   <ApplicationDefinition Remove="**\*.xaml" />
   <Compile Remove="MainWindow.xaml.cs" />
   ```
2. 做一个干净的最小 Probe 项目，用**加法二分**：每次只往里加一个片段。
   片段必须各自良构且标签平衡，否则结论全是假的。

## 2. 绝对不能用 `IsVisible`

`IsVisible`（任何值，连常量 `IsVisible="True"` 都算）会让 XamlCompiler 失败，
且只报上面那句没头没尾的「错误: 1」。

**用 `Visibility` 绑定一个 `Visibility` 类型的属性。** 所以 `ResultRow` 里是：

```csharp
public Visibility GroupRowVisibility =>
    Kind == RowKind.Group ? Visibility.Visible : Visibility.Collapsed;
```

而不是 `bool IsGroupRow`。

## 3. 绝对不能在 XAML 里写 `Cursor`

同理，XAML 里的字符串游标名无法转成 `InputSystemCursor`，XamlCompiler 直接失败。

而且代码里也没有好出路：`UIElement.CursorProperty` 和
`InputSystemCursor.CreateFromName` 都不存在于本 SDK 的公开 API 上。
`FrameworkElement` 上没有公开的 `CursorProperty`，`DependencyProperty` 也没有
`PropertyType`。**结论是别做自定义光标**，少个箭头不值得绕这么大一圈。

## 4. XAML 编译通过 ≠ 运行时资源存在

`{StaticResource LinkButtonStyle}` **编译通过但运行时不存在**——WinUI 3 内置的是
`TextButtonStyle` / `SubtleButtonStyle` / `AccentButtonStyle`，
`LinkButtonStyle` 是 WinUI 2 的名字。

运行时解析不到会抛 `STATUS_STOWED_RESOURCES (0xC000027B)`，进程直接崩在
`Microsoft.UI.Xaml.dll` 里，没有任何托管异常信息：

```
故障模块: Microsoft.UI.Xaml.dll  版本 3.1.8.0
异常代码: 0xc000027b
```

这类问题**只有真跑起来才会暴露**，所以「能构建」不等于「能用」。
本项目因此在 `Tokens.xaml` 里自己定义了一份不 BasedOn 任何内置样式的
`TextLinkButtonStyle`，零外部依赖。

## 5. `required` 成员会卡住 XamlCompiler

带 `required` 的类型无法被 `new T()` 实例化，而 XamlCompiler 为 `x:DataType`
生成类型信息时正好会走这条路。`ResultRow` 只能用普通 `init` 属性 +
对象初始化器赋值。

同理 `CheckBox.IsChecked` 是**三态**，绑定的属性必须是 `bool?` 而不是 `bool`。

## 6. WinExe 里碰 `Console.OutputEncoding` 会直接 failfast

项目是 `WinExe`，默认没有控制台。此时设置 `Console.OutputEncoding` 会让 CLR
直接 `0xC0000409`（`STATUS_STACK_BUFFER_OVERRUN`），**进程无消息崩溃**。

正确做法：`AttachConsole(ATTACH_PARENT_PROCESS)` 接管父控制台，
并且**不要碰 `Console.OutputEncoding`**。

## 7. `app.manifest` 必须自己声明 `longPathAware`

这是**唯一一个会导致功能静默缺失的坑**。

CPython 的 `python.exe` 默认带 `longPathAware` 标记，所以 Python 版能扫
>260 字符的路径；**.NET 应用默认不带**。后果是 `GetFileAttributesExW`
等 Win32 API 卡在 `MAX_PATH(260)`：

- 775 字符的文件**存在于磁盘、也能用 `FileStream` 打开**
- 但扫描器枚举不到它，`broken_entries` 计数为 0（不是失败，是根本没看见）
- 结果就是「明明有超长路径重复文件，界面里一条都没有」

修法（`app.manifest`，命名空间 `http://schemas.microsoft.com/SMI/2016/WindowsSettings`）：

```xml
<longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
```

这个 bug 是靠自检的 `[0] 长路径可达性` 段抓出来的——它独立于「是否构成重复组」
去核对磁盘上的长路径既可读又能进结果。

## 8. `FILETIME` 不要手工换算

`Win32.TryGetAttributes` 曾经这样换算修改时间：

```csharp
(long)(v - 116444736000000000L) * 100   // FILETIME -> .NET ticks
```

`* 100` 把时间戳放大了 100 倍，界面上的修改时间显示成 **7274-08-06**。

FILETIME 本身就是 100 纳秒单位，任何「减纪元再乘/除」的手工换算都会把年份
放大或缩小几个数量级。正确做法是**不换算**，把原始 FILETIME 存下来，
交给格式化函数：

```csharp
DateTime.FromFileTime(fileTimeTicks).ToLocalTime()
```

自检里加了交叉验证：拿 `FileInfo.LastWriteTime` 对照，误差必须 < 2 分钟。

## 8. `SetIcon` 用相对路径会静默失败

`AppWindow.SetIcon("Assets\\DumpSearch.ico")` 从相对路径解析，
按**当前工作目录**找文件——从资源管理器双击时 CWD 是用户目录，
从快捷方式启动时又是别的路径，于是文件找不到，调用**不报任何错**。
窗口图标一直是默认的，而这段代码"看起来"在工作。

必须给绝对路径：

```csharp
var iconPath = Path.Combine(AppContext.BaseDirectory, "Assets", "DumpSearch.ico");
if (File.Exists(iconPath)) appWindow.SetIcon(iconPath);
```

另外，**光靠 `SetIcon` 不够**——它只管窗口标题栏。
资源管理器和任务栏看的是 exe 里内嵌的图标，所以 csproj 还要有：

```xml
<ApplicationIcon>Assets\DumpSearch.ico</ApplicationIcon>
```

## 9. NuGet 源

本机 **nuget.org 不可达**。`nuget.config` 已指向华为云镜像：

```
https://repo.huaweicloud.com/repository/nuget/v3/index.json
```

## 10. TFM 必须是 `net8.0-windows10.0.19041.0`

即使装的是 .NET 10 SDK，Windows App SDK 1.8 要求 `net8.0` 的 TFM。
配合 `<WindowsAppSdkBootstrapInitialize>true</WindowsAppSdkBootstrapInitialize>`
（运行时不需要框架依赖部署）。

---

# 设计决策备忘

## 状态条独占一行，不能浮在内容之上

`InfoBar` 一开始放在 `Grid.Row="1"` 里配 `VerticalAlignment="Bottom"`，
结果盖住了左栏底部的「开始扫描」按钮和右栏的进度行——**被盖住的控件就点不到**。
现在它独占 `RowDefinition Height="Auto"`。

## 主操作固定在底部

左栏内容（7 个盘符 + 附加目录 + 4 个筛选字段 + 4 个开关 + 保留策略）比窗口高。
「开始扫描」原本在 `ScrollViewer` 里，配置一多就滚到看不见的地方。
现在 `ScrollViewer` + 底部固定操作条分占两行，配置再多主按钮也永远点得到。

## 路径列要剥掉公共前缀

整批结果往往共享一个很长的前缀（都在同一个盘 / 同一个目录下）。
直接显示绝对路径会把列宽全耗在重复的前缀上，真正有区分度的尾部反而被截掉
（第一版就是这样，十行路径长得一模一样）。

**求公共前缀必须逐段来**：不能拿第一个目录当基准去比其余的——
兄弟目录（`…\docs\a` 与 `…\data`）之间没有共同的第一段，
那样算出来永远是空串，路径列退化回全路径（这个 bug 也踩过一次）。

现在显示 `…\docs\b`，完整绝对路径放在 `ToolTipService.ToolTip`。

## 角色标记用形状而不是图标字形

`Segoe MDL2 Assets` 的私有区码位（`E3BE` / `E43C`）在 WinUI 3 的默认图标字体里
没有对应字形，`FontIcon` 会渲染成豆腐块。改用 `Ellipse` + 颜色：
绿色 = 保留，红色 = 删除，右侧本来就有文字说明。

## 显式设置 `Foreground` 可能导致文字隐形

`FieldLabelStyle` 最初给标签设了 `Foreground="{StaticResource FieldLabelBrush}"`，
结果所有表单标签**占位但完全看不见**（`FieldLabelStyle` 本身没报任何警告）。
去掉显式前景色、继承默认值后一切正常。

根因是 `SolidColorBrush` 的 `Color` 绑 `ThemeResource` 在本机解析出透明值。
**能继承就别显式设。**

## CheckBox 的 `Padding` 不能设 0

`CheckRowStyle` 里原本写了 `<Setter Property="Padding" Value="0" />`，
想着"密排不要内边距"，结果复选框方框和文字**贴死在一起**。

CheckBox 就是靠 `Padding` 隔开方框和 `Content` 的，默认值带左侧间距。
要紧凑就改 `MinHeight`，别动 `Padding`。现在是 `12,0,0,0`：
只加左侧间距，行高不变。

## 阶段步进器不要用 ItemsControl

阶段数固定 5 个，但**不要**用 `ItemsControl` + `DataTemplate` 渲染：
WinUI 3 的 `ItemsControl` 不公开容器生成器
（`ItemContainerGenerator` / `ContainerFromIndex` 是 **WPF** 的 API，本 SDK 里不存在），
拿不到每个 item 的控件就没法按状态上色，而 WinUI 3 又没有 DataTrigger。

现在的做法：XAML 里只留一个空 `Grid`（`StageStrip`），由 `BuildStageStrip()`
在代码里按模型生成带命名的控件，`UpdateStageDots()` 在 tick 里直接改颜色和文案。
既保持模型是唯一事实来源，又不依赖任何容器 API。

同理 `Microsoft.UI.Colors` **没有** `FromArgb`，要用
`new SolidColorBrush(Windows.UI.Color.FromArgb(...))`。

## Mica 需要根容器透明

`SystemBackdrop = new MicaBackdrop()` 只能在**代码**里设（XAML 声明会触发坑 1）。
根 `Grid` 的 `Background` 必须是 `Transparent`——刷不透明色会把 Mica 完全盖住，
表面由「卡片」承载。

> 注意：`PrintWindow` 抓到的是窗口自己的重定向表面，**不包含 DWM 在合成阶段
> 叠加的 backdrop 效果**。所以截图里 Mica 看起来是平的，这是抓图方式的
> 限制，不代表没生效。要看真实效果请直接运行程序。

---

# 维护这个项目要留意的几件事

## 长期维护的只有这些

```
src/DumpSearch.App/     应用本体
  Core/                 引擎（不含任何 XAML，可独立测试）
  ViewModels/           结果视图、逐组保留项、扫描进度
  Styles/Tokens.xaml    设计令牌
  SelfTest.cs           116 项引擎自检
tools/lint_xaml.py      XAML 静态检查 —— 改 XAML 后先跑这个
tools/bisect_runtime.py 定位「运行期才崩」的 XAML 片段
tools/shot.py           窗口截图
tools/shot_hover.py     悬停浮层截图
tools/make_icon.py      应用图标
tools/make_shortcut.py  带图标的快捷方式
tools/make_launcher.py  根目录启动器
tools/package_release.py 自包含发布包
```

## 排查过程留下的东西（不进版本库）

`.gitignore` 排除了以下内容，但**文件都还在本地**，按「不删任何文件」的要求保留：

| 路径 | 说明 |
| --- | --- |
| `Probe/` | 129 个文件的最小复现工程，里面是临时桩代码。定位「哪些属性不能写进 XAML」时用来做加法对照。结论已固化成 `lint_xaml.py` 的机械检查。 |
| `.bisect_stash/` | 二分脚本的快照备份。 |
| `tools/bisect_*.py`、`tools/patch_*.py`、`tools/dbg_blocks.py`、`tools/repro_xaml_csc.py`、`tools/test_*.py`、`tools/check_resources.py` | 一次性的二分脚本：循环是"加一个片段 → 构建 → 跑"，针对的是具体那次排查。 |
| `tools/add_bom.py` | 给 `.cs` / `.xaml` 补 UTF-8 BOM。本机 XamlCompiler 需要，但 `.gitattributes` 已保证文件编码，写文件时由编辑器负责即可。 |
| `dumpsearch*.png`、`shot_*.png`、`probe.png`、`icon_preview.png` | 各阶段界面截图。**截图里含 `%TEMP%` 展开后的 Windows 用户名，所以不能进公开仓库。** |
| `src/DumpSearch.App/MainWindow.xaml.probe` / `.linttest` | 定位崩溃与验证 linter 时留的副本，内容已还原。 |

需要重新二分时，入口是 `tools/bisect_runtime.py`，片段边界用
`<!-- CASE:name -->` / `<!-- /CASE:name -->` 在 `MainWindow.xaml` 里标记
（表头列宽、提示浮层、保留项工具条、文件夹列表这四处已经标好）。

