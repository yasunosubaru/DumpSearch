"""生成 Dump Search 的应用图标（多分辨率 .ico + PNG 预览）。

用法::

    python tools\\make_icon.py

设计取向（对着 16px 思考，而不是放大好看就行）：

- **圆角方块 + 渐变底**：Fluent 的应用图标惯例。底色用蓝紫渐变，在浅色和
  深色任务栏上都能立住；纯色块在深色任务栏上会"糊进背景"。
- **两张错位叠放的文档**：一眼就是"重复文件"。只画一张纸说不清是什么，
  两张才说明白。
- **后一张半透明**：不需要额外描边就能分出前后，16px 下描边会消失。
- **前一张带文本线**：强化"文件"语义，同时填补大片空白。
- **8 倍超采样再缩小**：直接按目标尺寸画圆角会有明显锯齿。

刻意的取舍：
- 不画放大镜。16px 下放大镜的圈和柄只剩 2~3 像素，会变成脏点。
- 不画勾/叉徽章。理由同上，而且语义重复（已经有两个文档了）。
"""

import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.abspath(os.path.join(HERE, "..", "src", "DumpSearch.App", "Assets"))

SS = 8           # 超采样倍数
SIZES = [16, 24, 32, 48, 64, 128, 256]

# Fluent 风格的蓝紫渐变
C_TOP = (0x2F, 0x8C, 0xFF)
C_BOTTOM = (0x6A, 0x3C, 0xF0)
C_DEEP = (0x1B, 0x2A, 0x6B)   # 文档内的"文本线"颜色，压暗一点更好读


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def vertical_gradient(size, top, bottom):
    """竖向线性渐变。先画小图再放大，比逐像素算快得多且无色带。"""
    strip = Image.new("RGB", (1, 256))
    px = strip.load()
    for y in range(256):
        px[0, y] = lerp(top, bottom, y / 255.0)
    return strip.resize((size, size), Image.BILINEAR)


def rounded_mask(size, radius_ratio):
    r = int(round(size * radius_ratio))
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    return m


def make_base(size):
    """在 size*SS 的画布上画，返回已缩回 size 的 RGBA 图。"""
    n = size * SS

    # ---- 底：渐变圆角方块 ----
    base = vertical_gradient(n, C_TOP, C_BOTTOM).convert("RGBA")
    base.putalpha(rounded_mask(n, 0.225))

    # ---- 顶部高光：让圆角方块有一点体积感，而不是纯平色 ----
    gloss = Image.new("L", (n, n), 0)
    ImageDraw.Draw(gloss).ellipse(
        [-int(n * 0.35), -int(n * 0.62), int(n * 1.35), int(n * 0.42)], fill=90
    )
    gloss = gloss.filter(ImageFilter.GaussianBlur(n * 0.06))
    base = Image.composite(
        Image.new("RGBA", (n, n), (255, 255, 255, 255)), base, gloss.point(lambda v: v)
    )
    base.putalpha(rounded_mask(n, 0.225))

    d = ImageDraw.Draw(base)

    def rr(x0, y0, x1, y1, radius, fill):
        d.rounded_rectangle(
            [int(n * x0), int(n * y0), int(n * x1), int(n * y1)],
            radius=int(n * radius), fill=fill,
        )

    white = (255, 255, 255, 255)

    # ---- 后一张文档：错位到左上。半透明白在渐变底上会混成灰污渍，
    #      所以透明度给到 150：既能看出前后关系，又不会脏。 ----
    rr(0.235, 0.185, 0.640, 0.760, 0.055, (255, 255, 255, 150))

    # ---- 前一张文档：实心白 ----
    rr(0.375, 0.290, 0.790, 0.865, 0.055, white)

    # ---- 前一张文档上的文本线 ----
    # 16px 下线宽不足 1 像素会闪，所以最小尺寸下只画 2 条粗线
    lines = 3 if size >= 32 else 2
    top = 0.400
    step = 0.108
    for i in range(lines):
        y = top + i * step
        # 最后一条短一点，像真实的段落
        x1 = 0.700 if i < lines - 1 else 0.630
        rr(0.455, y, x1, y + (0.052 if size >= 32 else 0.062), 0.026, C_DEEP + (215,))

    return base.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    frames = [make_base(s) for s in SIZES]

    ico = os.path.join(OUT_DIR, "DumpSearch.ico")
    # .ico 需要从小到大；Pillow 会按传入尺寸写入
    frames[0].save(ico, format="ICO",
                   sizes=[(s, s) for s in SIZES], append_images=frames[1:])

    for s in SIZES:
        frames[SIZES.index(s)].save(
            os.path.join(OUT_DIR, f"DumpSearch-{s}.png"), format="PNG")

    # 预览图：各尺寸按实际大小横排；右侧再放 16px 的
    # NEAREST 放大（看真实像素，不做插值美化）和 256px 缩到 48 的效果
    # （模拟任务栏里"图标被系统缩放"的样子）。
    strip = Image.new("RGBA", (sum(SIZES) + 24 * len(SIZES) + 200, 300), (32, 32, 38, 255))
    x = 12
    for f in frames:
        strip.alpha_composite(f, (x, 20))
        x += f.width + 24
    strip.alpha_composite(frames[0].resize((128, 128), Image.NEAREST), (x + 20, 20))
    strip.alpha_composite(frames[-1].resize((64, 64), Image.LANCZOS), (x + 20, 160))
    strip.alpha_composite(frames[2].resize((64, 64), Image.NEAREST), (x + 100, 160))
    preview = os.path.join(HERE, "icon_preview.png")
    strip.convert("RGB").save(preview)

    print(f"已生成 {ico}")
    for s in SIZES:
        p = os.path.join(OUT_DIR, f"DumpSearch-{s}.png")
        print(f"  {s:>3}x{s:<3} {os.path.getsize(p):>6} B")
    print(f"预览: {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
