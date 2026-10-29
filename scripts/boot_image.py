#!/usr/bin/env python3
"""Replace the kernel with pinned AOSP tools and verify preserved components."""
import gzip
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

from builder_lib import receipt, require, sha256


def kernel_format(path):
    with open(path, "rb") as stream:
        data = stream.read(0x220)
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    if data[:4] == b"\x02\x21\x4c\x18":
        return "lz4-legacy"
    if data[:4] == b"\x04\x22\x4d\x18":
        return "lz4-frame"
    if data[56:60] == b"ARM\x64":
        return "raw-arm64"
    if data[0x202:0x206] == b"HdrS":
        return "bzImage"
    raise ValueError("无法识别基线或编译产物的内核格式")


def check_architecture(path, kind, arch):
    if arch == "x86_64":
        require(kind == "bzImage", "x86_64 需要 bzImage 内核")
        with open(path, "rb") as stream:
            setup = stream.read(0x238)
        require(len(setup) == 0x238 and struct.unpack_from("<H", setup, 0x236)[0] & 1, "bzImage 缺少 64 位内核标志")
        return
    if kind == "gzip":
        with gzip.open(path, "rb") as stream:
            prefix = stream.read(64)
            total = len(prefix)
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                require(total <= 512 * 1024 * 1024, "解压后的内核超过 512 MiB 限制")
    elif kind.startswith("lz4"):
        process = subprocess.Popen(["lz4", "-d", "-c", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            prefix = process.stdout.read(64)
            total = len(prefix)
            for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                total += len(chunk)
                require(total <= 512 * 1024 * 1024, "解压后的内核超过 512 MiB 限制")
            require(process.wait() == 0, "LZ4 内核解压校验失败")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
    else:
        with open(path, "rb") as stream:
            prefix = stream.read(64)
    require(prefix[56:60] == b"ARM\x64", "内核不是可识别的 arm64 Image")


def header(path):
    with open(path, "rb") as stream:
        data = stream.read(4096)
    require(len(data) >= 1660 and data[:8] == b"ANDROID!", "只接受标准 Android boot.img，不能使用 vendor_boot 或 init_boot")
    version = struct.unpack_from("<I", data, 40)[0]
    require(version <= 4, "只支持 boot header v0–v4")
    kernel_size = struct.unpack_from("<I", data, 8)[0]
    require(kernel_size > 0, "基线没有内核，不能替换 init_boot")
    if version >= 3:
        ramdisk_size = struct.unpack_from("<I", data, 12)[0]
        size = 1584 if version == 4 else 1580
        require(struct.unpack_from("<I", data, 20)[0] == size, "boot header 长度不符合版本")
        page = 4096
        signature = struct.unpack_from("<I", data, 1580)[0] if version == 4 else 0
        required_size = page + align(kernel_size, page) + align(ramdisk_size, page) + signature
    else:
        ramdisk_size = struct.unpack_from("<I", data, 16)[0]
        second_size = struct.unpack_from("<I", data, 24)[0]
        page = struct.unpack_from("<I", data, 36)[0]
        require(2048 <= page <= 65536 and page & (page - 1) == 0, "boot pagesize 不合法")
        size = (1632, 1648, 1660)[version]
        signature = 0
        required_size = page + align(kernel_size, page) + align(ramdisk_size, page) + align(second_size, page)
        if version >= 1:
            recovery_size = struct.unpack_from("<I", data, 1632)[0]
            recovery_offset = struct.unpack_from("<Q", data, 1636)[0]
            if recovery_size:
                require(recovery_offset == required_size, "不支持非标准 recovery_dtbo 布局")
                required_size += align(recovery_size, page)
        if version == 2:
            required_size += struct.unpack_from("<I", data, 1648)[0]
    require(path.stat().st_size >= required_size, "boot.img 被截断或声明的组件大小不合法")
    return {"version": version, "size": size, "signature": signature, "bytes": data[:size]}


def align(size, page):
    return (size + page - 1) // page * page


def unpack(tools, image, destination):
    header(image)
    result = subprocess.run([sys.executable, str(tools / "unpack_bootimg.py"), "--boot_img", str(image),
                             "--out", str(destination), "--format=mkbootimg", "--null"],
                            check=True, stdout=subprocess.PIPE)
    parts = result.stdout.split(b"\0")
    require(parts[-1] == b"", "AOSP 参数缺少 NUL 结尾")
    args = [part.decode("utf-8") for part in parts[:-1]]
    require(args and len(args) % 2 == 0, "AOSP unpack_bootimg 未返回有效的参数数组")
    return args


def select_kernel(dist, baseline_kernel, state, arch):
    kind = kernel_format(baseline_kernel)
    check_architecture(baseline_kernel, kind, arch)
    name = {"gzip": "Image.gz", "lz4-legacy": "Image.lz4", "lz4-frame": "Image.lz4",
            "raw-arm64": "Image", "bzImage": "bzImage"}[kind]
    candidate = dist / name
    if candidate.is_file() and kernel_format(candidate) == kind:
        check_architecture(candidate, kind, arch)
        return candidate, kind
    raw = dist / "Image"
    require(arch == "arm64" and raw.is_file() and kind in ("gzip", "lz4-legacy", "lz4-frame"), f"缺少与基线匹配的编译产物 {name}")
    check_architecture(raw, kernel_format(raw), arch)
    candidate = state / name
    with open(candidate, "wb") as stream:
        if kind == "gzip":
            with gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as compressed, open(raw, "rb") as source:
                shutil.copyfileobj(source, compressed)
        else:
            flags = ["-l"] if kind == "lz4-legacy" else []
            subprocess.run(["lz4", *flags, "-c", str(raw)], check=True, stdout=stream)
    return candidate, kind


def repack():
    tools = Path(os.environ.get("MKBOOTIMG_DIR", "/opt/mkbootimg"))
    require((tools / "mkbootimg.py").is_file() and (tools / "unpack_bootimg.py").is_file(), "缺少 AOSP mkbootimg 工具，请使用配套 Docker 镜像")
    state = Path(os.environ["STATE_DIR"])
    baseline = state / "baseline-boot.img"
    before = header(baseline)
    output = Path(os.environ["ARTIFACT_DIR"]) / "boot.img"
    partial = output.with_suffix(".img.part")
    with tempfile.TemporaryDirectory(dir=state, prefix="repack-") as work:
        work = Path(work)
        original = work / "original"
        args = unpack(tools, baseline, original)
        kernel, kind = select_kernel(Path(os.environ["DIST_DIR"]), original / "kernel", work, os.environ["GKI_ARCH"])
        require(args.count("--kernel") == 1, "基线内核参数不唯一")
        args[args.index("--kernel") + 1] = str(kernel)
        subprocess.run([sys.executable, str(tools / "mkbootimg.py"), *args, "--output", str(partial)], check=True)
        checked = work / "checked"
        unpack(tools, partial, checked)
        after = header(partial)
        require(before["version"] == after["version"] and after["signature"] == 0, "header 版本变化或保留了旧签名")
        require(sha256(checked / "kernel") == sha256(kernel), "重打包后的内核字节不匹配")
        original_parts = {p.name for p in original.iterdir()} - {"kernel", "boot_signature"}
        checked_parts = {p.name for p in checked.iterdir()} - {"kernel", "boot_signature"}
        require(original_parts == checked_parts, "重打包改变了原有镜像组件")
        for name in original_parts:
            require(sha256(original / name) == sha256(checked / name), f"原有 {name} 内容发生变化")
        old_header, new_header = bytearray(before["bytes"]), bytearray(after["bytes"])
        mutable = [(8, 12)]
        if before["version"] <= 2:
            mutable.append((576, 608))  # Legacy image SHA includes kernel contents.
            if before["version"] >= 1:
                mutable.append((1636, 1644))  # recovery_dtbo offset follows kernel pages.
        if before["version"] == 4:
            mutable.append((1580, 1584))  # Old boot signature is deliberately discarded.
        for start, end in mutable:
            old_header[start:end] = new_header[start:end] = bytes(end - start)
        require(old_header == new_header, "原有 cmdline、地址、OS 版本或其他 header 字段发生变化")
        partition = os.environ.get("BOOT_PARTITION_SIZE", "")
        if partition:
            require(partition.isdecimal() and int(partition) > 0 and partial.stat().st_size <= int(partition), "新镜像超过 BOOT_PARTITION_SIZE 或参数不合法")
        with open(baseline, "rb") as stream:
            stream.seek(-64, 2)
            had_avb = stream.read(4) == b"AVBf"
        os.replace(partial, output)
        summary = {"request_id": os.environ["REQUEST_ID"], "ci_commit": os.environ["CI_COMMIT_SHA"],
                   "builder_commit": os.environ.get("BUILDER_REF", ""), "branch": os.environ["GKI_BRANCH"],
                   "arch": os.environ["GKI_ARCH"], "header_version": before["version"], "kernel_format": kind,
                   "kernel_sha256": sha256(kernel), "boot_sha256": sha256(output), "signed": False,
                   "old_boot_signature_removed": bool(before["signature"]), "old_avb_footer_removed": had_avb,
                   "partition_size_checked": bool(partition), "ramdisk_and_header_verified": True}
        (output.parent / "build-metadata.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        receipt("kernel", output)
    print("boot.img 已重打包并通过内核、ramdisk 和 header 一致性检查")


if __name__ == "__main__":
    try:
        repack()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f"boot.img 重打包失败：{error}")
