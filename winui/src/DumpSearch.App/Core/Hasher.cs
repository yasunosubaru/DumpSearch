using System.Buffers;
using System.Buffers.Binary;
using System.Security.Cryptography;

namespace DumpSearch.App.Core;

/// <summary>分层哈希：分段采样 → 全文 → 逐字节比对。</summary>
public static class Hasher
{
    /// <summary>
    /// 用 SHA-256 而不是 BLAKE2b：.NET 8 的 <see cref="HashAlgorithmName"/> 枚举里
    /// **没有 BLAKE2**（BLAKE2b128 / BLAKE2b256 都不存在），IncrementalHash
    /// 也不支持。而 SHA-256 在带 SHA-NI 的现代 CPU 上由硬件加速，
    /// 实测 1 MiB 块的多线程扩展与 BLAKE2 相当。
    /// 注意：与 Python 前端的 BLAKE2 摘要**不通用**，两边各自独立扫描。
    /// </summary>
    private const int QuickDigestSize = 16;   // 128 bit，够用作预筛
    private const int FullDigestSize = 32;    // 256 bit
    private static readonly double[] ProbeRatios = { 0.0, 0.5, 0.75 };

    private static FileStream OpenRobust(string path, int bufferSize)
    {
        try
        {
            return new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read,
                bufferSize, FileOptions.SequentialScan);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException
                                       or ArgumentException)
        {
            if (WinPath.Display(path).Length < WinPath.LONG_PATH_THRESHOLD) throw;
            return new FileStream(WinPath.Native(path), FileMode.Open, FileAccess.Read,
                FileShare.Read, bufferSize, FileOptions.SequentialScan);
        }
    }

    private static (long Size, long Ticks) Fingerprint(string path)
    {
        if (Win32.TryGetAttributes(path, out _, out long size, out long ticks))
            return (size, ticks);
        return (-1, -1);
    }

    /// <summary>
    /// 阶段 2：头/中/尾三段采样 + SHA-256(截断 128 位)。
    /// 读取期间文件被改写则返回 null——那种状态在磁盘上从未存在过。
    /// </summary>
    public static byte[]? QuickDigest(FileEntry entry, int chunk, int inlineMax)
    {
        var before = Fingerprint(entry.Path);
        using var h = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        using var fs = OpenRobust(entry.Path, 1);

        if (entry.Size <= inlineMax)
        {
            byte[] small = new byte[(int)Math.Max(0, Math.Min(entry.Size, int.MaxValue))];
            int got = ReadFull(fs, small, small.Length);
            if (got > 0) h.AppendData(small, 0, got);
        }
        else
        {
            // 采样三段而不是只比头部：很多文件（改过时间戳的镜像、只更新了
            // 尾部的日志/字幕）头部完全一致，只比头部会产生大量假候选。
            int window = (int)Math.Min(chunk, Math.Max(1, entry.Size / 8));
            byte[] buf = new byte[window];
            Span<byte> tag = stackalloc byte[8];
            foreach (double ratio in ProbeRatios)
            {
                long offset = Math.Min((long)(entry.Size * ratio),
                    Math.Max(0, entry.Size - window));
                BinaryPrimitives.WriteInt64LittleEndian(tag, offset);
                h.AppendData(tag);
                fs.Seek(offset, SeekOrigin.Begin);
                int n = fs.Read(buf, 0, window);
                if (n > 0) h.AppendData(buf, 0, n);
            }
        }

        if (before != Fingerprint(entry.Path)) return null;
        return h.GetHashAndReset().AsSpan(0, QuickDigestSize).ToArray();
    }

    /// <summary>阶段 3：全文 SHA-256。被改写则返回 null。</summary>
    public static byte[]? FullDigest(FileEntry entry, int chunk)
    {
        var before = Fingerprint(entry.Path);
        using var h = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        Span<byte> sizeTag = stackalloc byte[8];
        BinaryPrimitives.WriteInt64LittleEndian(sizeTag, entry.Size);
        h.AppendData(sizeTag);

        using var fs = OpenRobust(entry.Path, 1);
        byte[] buffer = ArrayPool<byte>.Shared.Rent(Math.Max(4096, chunk));
        try
        {
            int read;
            while ((read = fs.Read(buffer, 0, chunk)) > 0)
                h.AppendData(buffer, 0, read);
        }
        finally { ArrayPool<byte>.Shared.Return(buffer); }

        if (before != Fingerprint(entry.Path)) return null;
        return h.GetHashAndReset();
    }

    /// <summary>阶段 4：逐字节比对。哈希无法 100% 排除碰撞，删除前直接比字节。</summary>
    public static bool FilesIdentical(FileEntry a, FileEntry b, int chunk)
    {
        if (a.Size != b.Size) return false;
        if (a.Size == 0) return true;

        using var fa = OpenRobust(a.Path, 1);
        using var fb = OpenRobust(b.Path, 1);
        byte[] ba = ArrayPool<byte>.Shared.Rent(Math.Max(4096, chunk));
        byte[] bb = ArrayPool<byte>.Shared.Rent(Math.Max(4096, chunk));
        try
        {
            while (true)
            {
                int na = ReadFull(fa, ba, Math.Max(4096, chunk));
                int nb = ReadFull(fb, bb, Math.Max(4096, chunk));
                if (na != nb) return false;
                if (na == 0) return true;
                if (!ba.AsSpan(0, na).SequenceEqual(bb.AsSpan(0, nb))) return false;
            }
        }
        finally
        {
            ArrayPool<byte>.Shared.Return(ba);
            ArrayPool<byte>.Shared.Return(bb);
        }
    }

    private static int ReadFull(Stream s, byte[] buffer, int count)
    {
        int total = 0;
        while (total < count)
        {
            int n = s.Read(buffer, total, count - total);
            if (n <= 0) break;
            total += n;
        }
        return total;
    }
}
