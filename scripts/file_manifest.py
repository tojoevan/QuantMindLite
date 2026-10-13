#!/usr/bin/env python3
"""列出目录下所有需要同步的文件的 md5 清单（相对路径 + 哈希）。

用途：deploy.sh 对比本地与远端文件差异。
之所以不用 rsync：macOS 自带 openrsync（2.6.9）不支持 IPv6 形式的 remote 目标
（`user@[::1]:/path` 会被按冒号错误切分），因此同步走 tar over ssh，比对走本脚本。

用法：python3 file_manifest.py [根目录]   # 默认当前目录
输出：每行 "<相对路径>\t<md5>"，按路径排序。
"""

import hashlib
import os
import sys

SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules"}
SKIP_NAMES = {"quantweb.db", ".DS_Store", "Thumbs.db"}


def should_skip(name: str) -> bool:
    if name in SKIP_NAMES:
        return True
    if name.startswith("._") or name.endswith(".pyc"):
        return True
    return False


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in filenames:
            if should_skip(fn):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            try:
                with open(full, "rb") as fh:
                    rows.append("%s\t%s" % (rel.replace(os.sep, "/"),
                                            hashlib.md5(fh.read()).hexdigest()))
            except OSError:
                continue
    print("\n".join(sorted(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
