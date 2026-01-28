import os

OUTPUT_FILE = "all_code.txt"
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# 简单忽略这些目录
IGNORE_DIRS = {".git", ".venv", "venv", "__pycache__", ".cursor"}

# 认为是“代码文件”的后缀（可按需增减）
CODE_EXTS = {".py", ".sql", ".js", ".ts", ".tsx", ".json", ".yml", ".yaml"}

with open(os.path.join(PROJECT_ROOT, OUTPUT_FILE), "w", encoding="utf-8") as out:
    for root, dirs, files in os.walk(PROJECT_ROOT):
        # 过滤掉不想遍历的目录
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for name in files:
            _, ext = os.path.splitext(name)
            if ext not in CODE_EXTS:
                continue

            path = os.path.join(root, name)
            rel_path = os.path.relpath(path, PROJECT_ROOT)

            out.write("\n\n" + "=" * 80 + "\n")
            out.write(f"FILE: {rel_path}\n")
            out.write("=" * 80 + "\n\n")

            try:
                with open(path, "r", encoding="utf-8") as f:
                    out.write(f.read())
            except Exception as e:
                out.write(f"<<无法读取文件 {rel_path}: {e}>>\n")