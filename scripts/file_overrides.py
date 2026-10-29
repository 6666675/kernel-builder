#!/usr/bin/env python3
"""Export tracked Makefiles and validate/apply the versioned editing protocol."""
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile

from builder_lib import editable_path as relative_path, receipt, require, sha256

MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024


def source_file(root, value):
    relative = relative_path(value)
    root = Path(root).resolve()
    current = root
    for part in relative.parts:
        current = current / part
        info = current.lstat()
        require(not stat.S_ISLNK(info.st_mode), f"源码路径包含符号链接：{value}")
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_FILE, f"目标必须是有大小限制的独立普通文件：{value}")
    projects = (Path(os.environ["STATE_DIR"]) / "projects.txt").read_text().splitlines()
    owners = [p for p in projects if p == "." or value.startswith(p.rstrip("/") + "/")]
    require(owners, f"文件不在 repo 管理的项目中：{value}")
    project = max(owners, key=len)
    project_root = root if project == "." else root / project
    within = str(current.relative_to(project_root))
    subprocess.run(["git", "-C", str(project_root), "ls-files", "--error-unmatch", "--", within],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return current


def metadata():
    data = {"schema_version": 1, "request_id": os.environ["REQUEST_ID"],
            "ci_commit": os.environ["CI_COMMIT_SHA"], "manifest_url": os.environ["GKI_MANIFEST_URL"],
            "branch": os.environ["GKI_BRANCH"], "arch": os.environ["GKI_ARCH"]}
    if os.environ.get("BUILDER_REF"):
        data["builder_commit"] = os.environ["BUILDER_REF"]
    return data


def add_bytes(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(data), 0o644, 0
    archive.addfile(info, io.BytesIO(data))


def write_package(path, index, files, manifest=None):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".part")
    with tarfile.open(temporary, "w:gz") as archive:
        add_bytes(archive, "index.json", json.dumps(index, ensure_ascii=False, indent=2).encode())
        if manifest is not None:
            add_bytes(archive, "manifest.xml", manifest)
        for relative, data in files.items():
            add_bytes(archive, "files/" + relative, data)
    os.replace(temporary, path)


def read_package(path, prepared=False):
    require(Path(path).stat().st_size <= MAX_TOTAL, "归档超过 64 MiB 限制")
    contents = {}
    directories = set()
    total = 0
    with tarfile.open(path, "r:gz") as archive:
        for count, member in enumerate(archive, 1):
            require(count <= 1000, "归档成员过多")
            name = member.name.rstrip("/") if member.isdir() else member.name
            parts = PurePosixPath(name)
            require(name and not parts.is_absolute() and str(parts) == name and "\\" not in name
                    and not any(c in (".", "..", ".git", ".repo") for c in parts.parts), "归档包含非法路径")
            require(name not in contents and name not in directories, "归档成员重复")
            if member.isdir():
                directories.add(name)
                continue
            require(member.isfile() and not member.issym() and not member.islnk() and not member.sparse, "归档不允许链接、稀疏文件或特殊文件")
            limit = 1024 * 1024 if name == "index.json" else MAX_FILE
            require(0 <= member.size <= limit, "归档成员过大")
            total += member.size
            require(total <= MAX_TOTAL, "归档展开数据过大")
            stream = archive.extractfile(member)
            data = stream.read(member.size + 1)
            require(len(data) == member.size, "归档成员不完整")
            contents[name] = data
    index = json.loads(contents.get("index.json", b"{}"))
    require(isinstance(index, dict) and type(index.get("schema_version")) is int and index["schema_version"] == 1, "不支持的文件包协议版本")
    files = index.get("files")
    require(isinstance(files, list) and 0 < len(files) <= 100, "文件清单必须包含 1–100 个文件")
    expected = {"index.json"}
    allowed_dirs = {"files"}
    seen = set()
    for item in files:
        require(isinstance(item, dict), "文件清单格式错误")
        relative = relative_path(item.get("path"))
        name = str(relative)
        require(name not in seen, "目标文件重复")
        seen.add(name)
        archive_name = "files/" + name
        expected.add(archive_name)
        allowed_dirs.update(str(p) for p in PurePosixPath(archive_name).parents if str(p) != ".")
        require(archive_name in contents, f"修改包缺少文件：{name}")
        contents[archive_name].decode("utf-8")
        for key in (["base_sha256"] if prepared else ["base_sha256", "modified_sha256"]):
            require(isinstance(item.get(key), str) and re.fullmatch(r"[a-fA-F0-9]{64}", item[key]), f"缺少合法的 {key}：{name}")
        import hashlib
        digest = hashlib.sha256(contents[archive_name]).hexdigest()
        require(digest == item["base_sha256" if prepared else "modified_sha256"].lower(), f"包内文件哈希不匹配：{name}")
    if prepared:
        expected.add("manifest.xml")
    require(set(contents) == expected and directories <= allowed_dirs, "归档包含清单外的成员或缺少必要文件")
    return index, {name: contents["files/" + name] for name in seen}


def prepare():
    index = metadata()
    index["files"] = []
    files = {}
    for value in json.loads(os.environ["EDIT_FILES_JSON"]):
        path = source_file(os.environ["GKI_ROOT"], value)
        data = path.read_bytes()
        data.decode("utf-8")
        require(sum(map(len, files.values())) + len(data) <= MAX_TOTAL, "提取文件总大小超过限制")
        files[value] = data
        index["files"].append({"path": value, "base_sha256": sha256(path)})
    manifest = (Path(os.environ["STATE_DIR"]) / "manifest.xml").read_bytes()
    path = Path(os.environ["ARTIFACT_DIR"]) / f"source-{os.environ['REQUEST_ID']}.tar.gz"
    write_package(path, index, files, manifest)
    receipt("prepare", path)
    print(f"已提取 {len(files)} 个文件：{path.name}")


def overrides(apply=False):
    package = Path(os.environ["STATE_DIR"]) / "overrides.tar.gz"
    require(sha256(package) == os.environ["SOURCE_OVERRIDES_SHA256"].lower(), "修改包整体 SHA-256 不匹配")
    index, files = read_package(package)
    expected = metadata()
    for key in ("schema_version", "request_id", "ci_commit", "manifest_url", "branch", "arch"):
        require(index.get(key) == expected[key], f"修改包 {key} 与当前任务不一致")
    if "builder_commit" in index:
        require(index["builder_commit"] == expected.get("builder_commit"), "修改包对应的脚本版本已变化")
    targets = []
    for item in index["files"]:
        path = source_file(os.environ["GKI_ROOT"], item["path"])
        if not apply:
            actual = sha256(path)
            require(actual == item["base_sha256"].lower(), f"上游文件冲突：{item['path']}，预期 {item['base_sha256']}，实际 {actual}")
        targets.append((path, item))
    if apply:
        for path, item in targets:
            mode = stat.S_IMODE(path.stat().st_mode)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".gki-override-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(files[item["path"]])
            try:
                temporary.chmod(mode)
                os.replace(temporary, path)
                require(sha256(path) == item["modified_sha256"].lower(), "覆盖后的文件哈希不匹配")
            finally:
                temporary.unlink(missing_ok=True)
        print(f"已应用 {len(targets)} 个修改文件")
    else:
        print(f"已通过 {len(targets)} 个文件的上游原文校验")


if __name__ == "__main__":
    try:
        {"prepare": prepare, "check": overrides, "apply": lambda: overrides(True)}[sys.argv[1]]()
    except (ValueError, OSError, KeyError, UnicodeError, tarfile.TarError, subprocess.CalledProcessError) as error:
        sys.exit(f"文件包处理失败：{error}")
