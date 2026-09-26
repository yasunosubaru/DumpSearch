"""重写 ScanEngine 的两段哈希流水线。

原实现把 RunParallel 的返回值（byte[] 摘要）当成 FileEntry 用，
摘要与文件之间的对应关系丢了。这里改成显式的 (FileEntry, digest) 配对，
按下标对齐，语义一眼可辨。
"""

import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "src", "DumpSearch.App", "Core", "ScanEngine.cs")

NEW_PIPELINE = '''        // ---- 阶段 2：快速哈希 ----
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
        var quickGroups = GroupByDigest(candidates, quick);
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

        var hashed = GroupByDigest(fullCandidates, full)
            .Select(g => new DuplicateGroup
            {
                DigestHex = g.Key,
                Size = g[0].Size,
                Members = g,
            })
            .ToList();
'''


def main() -> int:
    with open(PATH, encoding="utf-8-sig") as fh:
        text = fh.read()

    start = text.find("        // ---- 阶段 2：快速哈希 ----")
    end = text.find("        // ---- 阶段 4：逐字节校验 ----")
    if start < 0 or end < 0:
        print("找不到流水线区间")
        return 1
    text = text[:start] + NEW_PIPELINE + "\n" + text[end:]

    # 追加 GroupByDigest 辅助方法
    helper = '''
    /// <summary>
    /// 把摘要数组按值分组，并按下标映射回文件。
    /// 两个数组等长且同序；null 项（读取失败 / 文件被改写）直接丢弃并计数。
    /// </summary>
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

    private List<DuplicateGroup> VerifyGroups('''
    text = text.replace("\n    private List<DuplicateGroup> VerifyGroups(", helper, 1)

    with open(PATH, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(text)
    print("已重写阶段 2/3 流水线")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
