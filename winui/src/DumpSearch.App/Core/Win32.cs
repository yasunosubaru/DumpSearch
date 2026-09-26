using System.Runtime.InteropServices;
using System.Text;

namespace DumpSearch.App.Core;

/// <summary>Win32 文件属性位与结构。全部手工声明，避免引入额外依赖。</summary>
internal static class Win32
{
    // ---- st_file_attributes 对应的属性位 ----
    public const uint FILE_ATTRIBUTE_READONLY = 0x00000001;
    public const uint FILE_ATTRIBUTE_HIDDEN = 0x00000002;
    public const uint FILE_ATTRIBUTE_SYSTEM = 0x00000004;
    public const uint FILE_ATTRIBUTE_DIRECTORY = 0x00000010;
    public const uint FILE_ATTRIBUTE_SPARSE_FILE = 0x00000200;
    public const uint FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400;
    public const uint FILE_ATTRIBUTE_COMPRESSED = 0x00000800;
    public const uint FILE_ATTRIBUTE_OFFLINE = 0x00001000;
    public const uint FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000;
    public const uint FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000;

    // ---- 重解析点 tag ----
    public const uint IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003;  // junction
    public const uint IO_REPARSE_TAG_SYMLINK = 0xA000000C;
    public const uint IO_REPARSE_TAG_CLOUD = 0x9000001A;
    public const uint IO_REPARSE_TAG_APPEXECLINK = 0x8000001B;
    public const uint IO_REPARSE_TAG_WCI = 0x9000001A;

    /// <summary>会造成无限递归、必须跳过的重解析点 tag。</summary>
    public static bool IsRecursiveReparseTag(uint tag) =>
        tag is IO_REPARSE_TAG_MOUNT_POINT or IO_REPARSE_TAG_SYMLINK
              or IO_REPARSE_TAG_APPEXECLINK;

    [StructLayout(LayoutKind.Sequential)]
    public struct FILETIME
    {
        public uint dwLowDateTime;
        public uint dwHighDateTime;
        public ulong ToUInt64() => ((ulong)dwHighDateTime << 32) | dwLowDateTime;
        public static FILETIME FromUInt64(ulong v) =>
            new() { dwLowDateTime = (uint)(v & 0xFFFFFFFF), dwHighDateTime = (uint)(v >> 32) };
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct BY_HANDLE_FILE_INFORMATION
    {
        public uint dwFileAttributes;
        public FILETIME ftCreationTime;
        public FILETIME ftLastAccessTime;
        public FILETIME ftLastWriteTime;
        public uint dwVolumeSerialNumber;
        public uint nFileSizeHigh;
        public uint nFileSizeLow;
        public uint nNumberOfLinks;   // 硬链接数
        public uint nFileIndexHigh;    // 与 nFileIndexLow 合成文件 ID
        public uint nFileIndexLow;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct SHFILEOPSTRUCTW
    {
        public IntPtr hwnd;
        public uint wFunc;
        public string? pFrom;
        public string? pTo;
        public ushort fFlags;
        [MarshalAs(UnmanagedType.Bool)] public bool fAnyOperationsAborted;
        public IntPtr hNameMappings;
        public string? lpszProgressTitle;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true,
        EntryPoint = "GetFileInformationByHandle")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetFileInformationByHandle(
        IntPtr hFile, out BY_HANDLE_FILE_INFORMATION lpFileInformation);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetFileAttributesExW(
        string lpFileName, int iFileAttributeLevel, out WIN32_FILE_ATTRIBUTE_DATA data);

    [StructLayout(LayoutKind.Sequential)]
    public struct WIN32_FILE_ATTRIBUTE_DATA
    {
        public uint dwFileAttributes;
        public FILETIME ftCreationTime;
        public FILETIME ftLastAccessTime;
        public FILETIME ftLastWriteTime;
        public uint nFileSizeHigh;
        public uint nFileSizeLow;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetDiskFreeSpaceExW(
        string lpDirectoryName,
        out ulong lpFreeBytesAvailableToCaller,
        out ulong lpTotalNumberOfBytes,
        out ulong lpTotalNumberOfFreeBytes);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint GetLogicalDrives();

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern uint GetDriveTypeW(string lpRootPathName);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetVolumeInformationW(
        string lpRootPathName,
        StringBuilder lpVolumeNameBuffer, uint nVolumeNameSize,
        out uint lpVolumeSerialNumber,
        out uint lpMaximumComponentLength,
        out uint lpFileSystemFlags,
        StringBuilder lpFileSystemNameBuffer, uint nFileSystemNameSize);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr GetSystemTimeAsFileTime(out FILETIME lpSystemTimeAsFileTime);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool MoveFileExW(string lpExistingFileName, string lpNewFileName, uint dwFlags);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern int SHFileOperationW(ref SHFILEOPSTRUCTW lpFileOp);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern int SHEmptyRecycleBinW(IntPtr hwnd, string? pszRootPath, uint dwFlags);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern int SHQueryRecycleBinW(string? pszRootPath, ref SHQUERYRBINFO pmqbInfo);

    [StructLayout(LayoutKind.Sequential)]
    public struct SHQUERYRBINFO
    {
        public uint cbSize;
        public ulong i64Size;
        public ulong i64NumItems;
    }

    // ---- SHFileOperation 常量 ----
    public const uint FO_DELETE = 0x0003;
    public const ushort FOF_SILENT = 0x0004;
    public const ushort FOF_NOCONFIRMATION = 0x0010;   // 必开，否则阻塞 15 秒且不删
    public const ushort FOF_ALLOWUNDO = 0x0040;       // 进回收站
    public const ushort FOF_NOCONFIRMMKDIR = 0x0200;
    public const ushort FOF_NOERRORUI = 0x0400;

    public const uint MOVEFILE_REPLACE_EXISTING = 0x1;
    public const uint MOVEFILE_COPY_ALLOWED = 0x2;
    public const uint MOVEFILE_DELAY_UNTIL_REBOOT = 0x4;

    // ---- 卷类型 ----
    public const uint DRIVE_UNKNOWN = 0;
    public const uint DRIVE_NO_ROOT_DIR = 1;
    public const uint DRIVE_REMOVABLE = 2;
    public const uint DRIVE_FIXED = 3;
    public const uint DRIVE_REMOTE = 4;
    public const uint DRIVE_CDROM = 5;
    public const uint DRIVE_RAMDISK = 6;

    public static string DriveTypeName(uint t) => t switch
    {
        DRIVE_REMOVABLE => "可移动盘",
        DRIVE_FIXED => "本地磁盘",
        DRIVE_REMOTE => "网络盘",
        DRIVE_CDROM => "光驱",
        DRIVE_RAMDISK => "内存盘",
        _ => "未知",
    };

    /// <summary>取文件的真实文件 ID 与硬链接数。</summary>
    /// <remarks>
    /// .NET 的 FileSystemInfo 拿不到链接数与文件 ID（等价于 Python 侧
    /// os.DirEntry.stat() 的 st_nlink/st_ino 恒为 0 的问题），必须走
    /// GetFileInformationByHandle。
    /// </remarks>
    public static bool TryGetFileIdentity(
        string path, out uint volumeSerial, out ulong fileId, out uint linkCount)
    {
        volumeSerial = 0; fileId = 0; linkCount = 0;
        IntPtr h = CreateFile(path, 0 /* FILE_READ_ATTRIBUTES */,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, IntPtr.Zero,
            OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, IntPtr.Zero);
        if (h == INVALID_HANDLE_VALUE) return false;
        try
        {
            if (!GetFileInformationByHandle(h, out var info)) return false;
            volumeSerial = info.dwVolumeSerialNumber;
            fileId = ((ulong)info.nFileIndexHigh << 32) | info.nFileIndexLow;
            linkCount = info.nNumberOfLinks;
            return true;
        }
        finally { CloseHandle(h); }
    }

    private const int OPEN_EXISTING = 3;
    private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
    private static readonly IntPtr INVALID_HANDLE_VALUE = new(-1);

    // CreateFile 的共享模式。自己定义常量而不是用 System.IO.FileShare 枚举：
    // P/Invoke 声明的是 uint，枚举传过去会报 CS1503。
    public const uint FILE_SHARE_READ = 0x00000001;
    public const uint FILE_SHARE_WRITE = 0x00000002;
    public const uint FILE_SHARE_DELETE = 0x00000004;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFile(
        string lpFileName, uint dwDesiredAccess, uint dwShareMode,
        IntPtr lpSecurityAttributes, uint dwCreationDisposition,
        uint dwFlagsAndAttributes, IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr hObject);

    /// <summary>取文件的属性位与逻辑大小（单次系统调用，不需 FileInfo 构造）。</summary>
    /// <param name="lastWriteTicks">
    /// 原始 FILETIME（自 1601-01-01 起的 100 纳秒数）。**故意不做单位换算**——
    /// 之前在这里减掉 Unix 纪元后"再乘 100"，把时间戳放大了 100 倍，
    /// 界面上 modification time 会显示成 7274 年这种明显错误的年份。
    /// 换算交给 <see cref="WinPath.FormatTimestamp"/> 用 DateTime.FromFileTime
    /// 做，那条路是 FILETIME 的唯一正确入口。
    /// </param>
    public static bool TryGetAttributes(string path, out uint attributes, out long size,
        out long lastWriteTicks)
    {
        attributes = 0; size = 0; lastWriteTicks = 0;
        if (!GetFileAttributesExW(path, 0, out var d)) return false;
        attributes = d.dwFileAttributes;
        size = ((long)d.nFileSizeHigh << 32) | d.nFileSizeLow;
        lastWriteTicks = unchecked((long)d.ftLastWriteTime.ToUInt64());
        return true;
    }
}
