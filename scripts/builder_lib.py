#!/usr/bin/env python3
"""Shared input and receipt checks; uses only the Python standard library."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit


def require(condition, message):
    if not condition:
        raise ValueError(message)


def https_url(value, label):
    parsed = urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.fragment
            and not any(c.isspace() for c in value), f"{label} 必须是无用户名、密码和片段的 HTTPS 地址")
    return value


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def editable_path(value):
    require(isinstance(value, str) and 0 < len(value) <= 512, "文件路径必须为非空字符串")
    path = PurePosixPath(value)
    require(not path.is_absolute() and str(path) == value and "\\" not in value
            and not any(ord(c) < 32 for c in value)
            and all(part not in (".", "..", ".git", ".repo") for part in path.parts), f"非法文件路径：{value}")
    name = path.name
    require(name == "Makefile" or name.startswith("Makefile.") or name.endswith(".mk"), f"仅允许 Makefile、Makefile.*、*.mk：{value}")
    return path


def validate(mode):
    for name in ("GKI_MANIFEST_URL", "GKI_BRANCH", "GKI_ARCH", "REQUEST_ID", "CI_COMMIT_SHA"):
        require(os.environ.get(name), f"缺少必填参数 {name}")
    https_url(os.environ["GKI_MANIFEST_URL"], "GKI_MANIFEST_URL")
    require(not urlsplit(os.environ["GKI_MANIFEST_URL"]).query, "GKI_MANIFEST_URL 不应包含查询参数或下载令牌")
    branch = os.environ["GKI_BRANCH"]
    require(not branch.startswith("-") and not any(c.isspace() or ord(c) < 32 for c in branch), "GKI_BRANCH 不合法")
    require(os.environ["GKI_ARCH"] in ("arm64", "x86_64"), "仅支持 arm64、x86_64")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}", os.environ["REQUEST_ID"]), "REQUEST_ID 仅允许字母、数字和连字符")
    require(re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", os.environ["CI_COMMIT_SHA"]), "需要完整的 CI 仓库提交 SHA")
    workspace = Path(os.environ["WORKSPACE"]).resolve()
    roots = []
    for name in ("GKI_ROOT", "DIST_DIR", "OUT_DIR", "STATE_DIR", "ARTIFACT_DIR"):
        path = Path(os.environ[name])
        require(path.is_absolute() and not path.is_symlink(), f"{name} 必须是非符号链接的绝对路径")
        resolved = path.resolve()
        require(resolved != workspace and workspace in resolved.parents, f"{name} 必须位于工作区内的独立子目录")
        require(all(resolved != other and resolved not in other.parents and other not in resolved.parents for other in roots), "源码、输出、状态和附件目录不能互相包含")
        roots.append(resolved)
    cache = Path(os.environ["CACHE_ROOT"])
    require(cache.is_absolute(), "缓存目录必须使用绝对路径")
    cache = cache.resolve()
    require(cache != Path("/") and all(cache != p and cache not in p.parents and p not in cache.parents for p in roots), "缓存目录必须独立于源码与输出目录")
    for name in ("GKI_SYNC_JOBS", "GKI_BUILD_JOBS"):
        require(re.fullmatch(r"[1-9][0-9]{0,3}", os.environ[name]), f"{name} 必须为正整数")
    for name in ("BOOT_IMG_URL", "CUSTOM_SCRIPT_URL", "SOURCE_OVERRIDES_URL"):
        if os.environ.get(name):
            https_url(os.environ[name], name)
    override = os.environ.get("SOURCE_OVERRIDES_URL", "")
    override_hash = os.environ.get("SOURCE_OVERRIDES_SHA256", "")
    require(bool(override) == bool(override_hash), "修改包 URL 和 SHA-256 必须同时填写")
    if override_hash:
        require(re.fullmatch(r"[a-fA-F0-9]{64}", override_hash), "修改包 SHA-256 必须为 64 位十六进制")
    extra = os.environ.get("EXTRA_BAZEL_ARGS", "")
    require("\n" not in extra and "\r" not in extra and "--" not in extra.split(), "EXTRA_BAZEL_ARGS 不能包含换行或独立的 --")
    if mode == "kernel":
        require(os.environ.get("BOOT_IMG_URL"), "正式构建需要 BOOT_IMG_URL")
    else:
        files = json.loads(os.environ.get("EDIT_FILES_JSON", "[]"))
        require(isinstance(files, list) and 0 < len(files) <= 100 and all(isinstance(x, str) for x in files), "EDIT_FILES_JSON 必须是包含 1–100 个文件路径的数组")
        require(len(set(files)) == len(files), "提取文件路径不能重复")
        for path in files:
            editable_path(path)


def receipt(kind, path):
    info = {"request_id": os.environ["REQUEST_ID"], "ci_commit": os.environ["CI_COMMIT_SHA"],
            "build_id": os.environ.get("CNB_BUILD_ID", ""), "name": path.name, "sha256": sha256(path)}
    (Path(os.environ["STATE_DIR"]) / f"{kind}-success.json").write_text(json.dumps(info), encoding="utf-8")


if __name__ == "__main__":
    import sys
    try:
        validate(sys.argv[1])
    except (ValueError, KeyError) as error:
        sys.exit(f"输入校验失败：{error}")
