using DumpSearch.App.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace DumpSearch.App;

/// <summary>
/// 清理前的预览 + 二次确认。
/// </summary>
/// <remarks>
/// 刻意让"点错"变难：列出完整清单、把风险审计结果放在最显眼的位置、
/// 必须显式勾选确认框才能点主按钮。删除走回收站，仍可还原。
/// 默认按钮落在「取消」上。
/// </remarks>
public sealed class CleanPreviewDialog : ContentDialog
{
    private readonly DeletePlan _plan;
    private readonly CheckBox _ack;

    public CleanPreviewDialog(DeletePlan plan, IReadOnlyList<string> warnings, KeepRule rule)
    {
        _plan = plan;
        Title = "清理预览与确认";
        PrimaryButtonText = "确认移入回收站";
        CloseButtonText = "取消";
        DefaultButton = ContentDialogButton.Close;   // 默认落在"取消"
        IsPrimaryButtonEnabled = false;               // 必须先勾确认框

        _ack = new CheckBox
        {
            Content = "我已核对上面的清单，确认这些副本可以移入回收站",
            MinWidth = 0,
        };
        _ack.Checked += (_, _) => IsPrimaryButtonEnabled = true;
        _ack.Unchecked += (_, _) => IsPrimaryButtonEnabled = false;

        Content = BuildContent(plan, warnings, rule);
    }

    private UIElement BuildContent(DeletePlan plan, IReadOnlyList<string> warnings,
        KeepRule rule)
    {
        var root = new StackPanel { Spacing = 12, MaxWidth = 860 };

        root.Children.Add(new TextBlock
        {
            Text = plan.Summary + $"　保留策略：{KeepRuleNames.Label(rule)}",
            FontSize = 15,
            TextWrapping = TextWrapping.Wrap,
        });

        if (warnings.Count > 0)
        {
            root.Children.Add(new Border
            {
                BorderBrush = new SolidColorBrush(Microsoft.UI.Colors.Goldenrod),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(6),
                Padding = new Thickness(12),
                Child = new TextBlock
                {
                    Text = "请注意\n" + string.Join("\n", warnings.Select(w => "• " + w)),
                    TextWrapping = TextWrapping.Wrap,
                    FontSize = 12,
                },
            });
        }

        var list = new ListView
        {
            Height = 300,
            SelectionMode = ListViewSelectionMode.None,
            BorderThickness = new Thickness(1),
            BorderBrush = new SolidColorBrush(Microsoft.UI.Colors.Gray),
            CornerRadius = new CornerRadius(6),
        };
        foreach (var line in plan.PreviewLines(500))
        {
            bool isDelete = line.StartsWith("删除", StringComparison.Ordinal);
            list.Items.Add(new TextBlock
            {
                Text = line,
                FontFamily = new FontFamily("Consolas, Cascadia Mono"),
                FontSize = 11,
                Foreground = new SolidColorBrush(isDelete
                    ? Microsoft.UI.Colors.IndianRed
                    : Microsoft.UI.Colors.MediumSeaGreen),
            });
        }
        root.Children.Add(list);

        root.Children.Add(new TextBlock
        {
            Text = "这些文件会被移入回收站（可以还原），不会立即永久删除。"
                 + "清空回收站后才会真正释放空间。",
            FontSize = 12,
            Opacity = 0.75,
            TextWrapping = TextWrapping.Wrap,
        });

        // 确认框必须真的出现在内容里，否则"必须勾选"形同虚设
        root.Children.Add(_ack);
        return root;
    }
}
