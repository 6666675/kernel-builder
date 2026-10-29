import gzip
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest

from helpers import BuilderFixture
from boot_image import check_architecture, header, repack

TOOLS = Path(os.environ.get("MKBOOTIMG_DIR", "/opt/mkbootimg"))


def arm_image(payload):
    data = bytearray(64)
    data[56:60] = b"ARM\x64"
    return bytes(data) + payload


@unittest.skipUnless((TOOLS / "mkbootimg.py").is_file(), "需要配套 Docker 镜像或 MKBOOTIMG_DIR 指向固定 AOSP 工具")
class BootImageTests(BuilderFixture):
    def setUp(self):
        super().setUp()
        os.environ["MKBOOTIMG_DIR"] = str(TOOLS)
        self.dist = Path(self.env["DIST_DIR"])
        self.dist.mkdir()

    def baseline(self, version, compressed=False, empty_cmdline=False, x86=False):
        if x86:
            raw = bytearray(4096)
            raw[0x202:0x206] = b"HdrS"
            struct.pack_into("<H", raw, 0x236, 1)
            old = bytes(raw)
        else:
            old = arm_image(b"original" * 100)
        kernel = self.base / "old-kernel"
        kernel.write_bytes(gzip.compress(old) if compressed else old)
        ramdisk = self.base / "ramdisk"
        ramdisk.write_bytes(b"original ramdisk contents")
        boot = self.state / "baseline-boot.img"
        command = [sys.executable, str(TOOLS / "mkbootimg.py"), "--header_version", str(version),
                   "--kernel", str(kernel), "--ramdisk", str(ramdisk), "--cmdline", "" if empty_cmdline else "console=ttyS0 test='value with space' literal=$(unchanged)",
                   "--os_version", "14.0.0", "--os_patch_level", "2024-01", "--output", str(boot)]
        if version <= 2:
            second = self.base / "second"
            second.write_bytes(b"second stage")
            command.extend(["--second", str(second)])
        if version >= 1 and version <= 2:
            recovery = self.base / "recovery"
            recovery.write_bytes(b"recovery dtbo")
            command.extend(["--recovery_dtbo", str(recovery)])
        if version == 2:
            dtb = self.base / "dtb"
            dtb.write_bytes(b"device tree")
            command.extend(["--dtb", str(dtb)])
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return boot

    def test_all_boot_headers_preserve_ramdisk_cmdline_and_extra_components(self):
        for version in range(5):
            with self.subTest(header=version):
                self.baseline(version)
                (self.dist / "Image").write_bytes(arm_image(b"new kernel" * 1800))
                repack()
                output = self.artifacts / "boot.img"
                self.assertEqual(header(output)["version"], version)
                summary = json.loads((self.artifacts / "build-metadata.json").read_text())
                self.assertTrue(summary["ramdisk_and_header_verified"])
                self.assertFalse(summary["signed"])

    def test_gzip_format_and_empty_command_line_survive(self):
        self.baseline(3, compressed=True, empty_cmdline=True)
        (self.dist / "Image").write_bytes(arm_image(b"new gzip kernel"))
        repack()
        summary = json.loads((self.artifacts / "build-metadata.json").read_text())
        self.assertEqual(summary["kernel_format"], "gzip")

    def test_old_v4_signature_and_avb_footer_are_discarded(self):
        boot = self.baseline(4)
        with open(boot, "r+b") as stream:
            stream.seek(1580)
            stream.write(struct.pack("<I", 4096))
            stream.seek(0, 2)
            stream.write(b"old-signature".ljust(4096, b"\0"))
            stream.write(b"AVBf" + bytes(60))
        (self.dist / "Image").write_bytes(arm_image(b"replacement"))
        repack()
        self.assertEqual(header(self.artifacts / "boot.img")["signature"], 0)
        summary = json.loads((self.artifacts / "build-metadata.json").read_text())
        self.assertTrue(summary["old_boot_signature_removed"])
        self.assertTrue(summary["old_avb_footer_removed"])

    def test_truncation_wrong_architecture_and_capacity_are_rejected(self):
        boot = self.baseline(3)
        (self.dist / "Image").write_bytes(arm_image(b"replacement"))
        os.environ["BOOT_PARTITION_SIZE"] = "1"
        with self.assertRaises(ValueError):
            repack()
        os.environ["BOOT_PARTITION_SIZE"] = ""
        os.environ["GKI_ARCH"] = "x86_64"
        with self.assertRaises(ValueError):
            repack()
        boot.write_bytes(boot.read_bytes()[:2000])
        with self.assertRaises(ValueError):
            header(boot)

    def test_x86_64_requires_and_repackages_64_bit_bzimage(self):
        self.baseline(3, x86=True)
        image = bytearray(8192)
        image[0x202:0x206] = b"HdrS"
        struct.pack_into("<H", image, 0x236, 1)
        candidate = self.dist / "bzImage"
        candidate.write_bytes(image)
        os.environ["GKI_ARCH"] = "x86_64"
        repack()
        struct.pack_into("<H", image, 0x236, 0)
        candidate.write_bytes(image)
        with self.assertRaises(ValueError):
            check_architecture(candidate, "bzImage", "x86_64")
