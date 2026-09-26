"""把 ResultViewModel 里那套绕的排序/过滤逻辑换成单一 Refilter。

原实现有 Resort / Refilter / ResortPreservingOrder 三个方法互相调用，
既难维护又有一处真实的 bug：_sorted 是 List 却直接赋 LINQ 的
IOrderedEnumerable（编译不过），而且 Refilter 里那句
`byName ? needle[5..] : byPath ? needle[5..] : needle` 里 byPath 分支
重复取下标，可读性极差。
"""

import io
import os
import sys

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "src", "DumpSearch.App", "ViewModels", "ResultViewModel.cs")

NEW = '''    /// <summary>
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
            string key = needle[(byExt || byName) ? (byExt ? 4 : 5) : 0..]
                .Trim().ToLowerInvariant();
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
'''


def main() -> int:
    with open(PATH, encoding="utf-8-sig") as fh:
        text = fh.read()

    start = text.find("    private void Resort()")
    end_marker = "    private void RebuildRows()"
    end = text.find(end_marker)
    if start < 0 or end < 0 or end < start:
        print("找不到待替换区间")
        return 1

    # Resort() 之前的 SetSort/Load 里对 Resort() 的调用要改成 Refilter()
    text = text[:start] + NEW + "\n" + text[end:]
    text = text.replace("        Resort();\n", "        Refilter();\n")

    with open(PATH, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(text)
    print("已替换排序/过滤实现")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
