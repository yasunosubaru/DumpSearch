"""用 ast 找未使用的导入（不依赖 pyflakes）。"""

import ast
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src, path)
    imported = {}  # name -> lineno
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                name = a.asname or a.name.split(".")[0]
                imported[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    continue
                name = a.asname or a.name
                imported[name] = node.lineno

    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            n = node
            while isinstance(n, ast.Attribute):
                n = n.value
            if isinstance(n, ast.Name):
                used.add(n.id)
    # 字符串里的类型注解也算使用
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for token in node.value.replace("[", " ").replace("]", " ").replace(
                    ",", " ").replace("|", " ").split():
                used.add(token)

    return [(name, line) for name, line in imported.items()
            if name not in used and f"# noqa" not in src.splitlines()[line - 1]]


def main():
    total = 0
    for base, _dirs, files in os.walk(ROOT):
        if "__pycache__" in base or "node_modules" in base:
            continue
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = os.path.join(base, f)
            rel = os.path.relpath(path, ROOT)
            try:
                issues = check(path)
            except SyntaxError as exc:
                print(f"{rel}: 语法错误 {exc}")
                total += 1
                continue
            for name, line in issues:
                print(f"{rel}:{line}: 未使用的导入 {name}")
                total += 1
    print(f"\n共 {total} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
