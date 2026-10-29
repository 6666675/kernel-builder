#!/usr/bin/env python3
"""Create an overrides package while retaining hashes from the original package."""
import argparse
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from file_overrides import MAX_FILE, MAX_TOTAL, read_package, write_package
from builder_lib import require


def make(prepared, edited, output):
    index, originals = read_package(prepared, prepared=True)
    files = {}
    changed = []
    root = Path(edited).resolve()
    for item in index["files"]:
        path = root / "files" / item["path"]
        require(root in path.resolve().parents and not path.is_symlink() and path.is_file(), "编辑目录包含越界路径或缺少原文件")
        require(path.stat().st_size <= MAX_FILE, "编辑文件超过大小限制")
        data = path.read_bytes()
        data.decode("utf-8")
        if data != originals[item["path"]]:
            files[item["path"]] = data
            require(sum(map(len, files.values())) <= MAX_TOTAL, "修改文件总大小超过限制")
            changed.append({**item, "modified_sha256": hashlib.sha256(data).hexdigest()})
    require(changed, "没有发生修改的文件，无需制作修改包")
    index["files"] = changed
    write_package(output, index, files)
    print(f"已打包 {len(changed)} 个修改文件；SHA-256 {hashlib.sha256(Path(output).read_bytes()).hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True)
    parser.add_argument("--edited", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        make(args.prepared, args.edited, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f"修改包生成失败：{error}\n")
