"""诊断 real_blocks() 为什么返回 0 块。"""

import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "src", "DumpSearch.App")
PATH = os.path.join(APP, "MainWindow.xaml")

with open(PATH, encoding="utf-8-sig") as fh:
    text = fh.read()
lines = text.splitlines()
print("总行数:", len(lines))

candidates = [(i, l) for i, l in enumerate(lines)
              if l.lstrip().startswith('<Grid Grid.Row="1"')]
print("候选起始行:", [(i + 1, len(l) - len(l.lstrip())) for i, l in candidates])

start = None
for i, line in enumerate(lines):
    if not line.lstrip().startswith('<Grid Grid.Row="1"'):
        continue
    if len(line) - len(line.lstrip()) == 8:
        start = i
        break
print("start(0-based):", start)

if start is None:
    raise SystemExit(0)

depth = 0
end = None
# 注意：必须用前瞻 (?=[\s>/])，否则 <Grid.ColumnDefinitions> 也会被算成
# <Grid 的开标签，深度永远回不到 0，end 就一直是 None。
open_re = re.compile(r"<Grid(?=[\s>/])")
close_re = re.compile(r"</Grid>")
for i in range(start, len(lines)):
    line = lines[i]
    depth += len(open_re.findall(line)) - len(close_re.findall(line))
    if depth == 0:
        end = i
        break
print("end(0-based):", end)

body = lines[start + 1:end]
chunks, cur = [], []
for line in body:
    is_top = line.startswith("            <") and not line.strip().startswith("</")
    if is_top and cur:
        chunks.append("\n".join(cur))
        cur = [line]
    elif is_top:
        cur = [line]
    else:
        if cur:
            cur.append(line)
if cur:
    chunks.append("\n".join(cur))
blocks = [c for c in chunks if c.strip()]
print("块数:", len(blocks))
for i, b in enumerate(blocks, 1):
    head = " ".join(b.split())[:66]
    print(f"  {i}: {head}")
