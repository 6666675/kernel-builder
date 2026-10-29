import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tools")]


class BuilderFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.env = {
            "WORKSPACE": str(self.workspace), "GKI_ROOT": str(self.workspace / "gki-src"),
            "DIST_DIR": str(self.workspace / "kernel-dist"), "OUT_DIR": str(self.workspace / "kernel-out"),
            "STATE_DIR": str(self.workspace / ".gki-state"), "ARTIFACT_DIR": str(self.workspace / "artifact"),
            "GKI_CACHE_DIR": str(self.base / "cache"), "CACHE_ROOT": str(self.base / "cache"), "GKI_MANIFEST_URL": "https://example.test/manifest",
            "GKI_BRANCH": "common-android14-6.1", "GKI_ARCH": "arm64", "REQUEST_ID": "test-123",
            "CI_COMMIT_SHA": "0123456789abcdef0123456789abcdef01234567",
            "GKI_SYNC_JOBS": "1", "GKI_BUILD_JOBS": "2", "BOOT_IMG_URL": "https://example.test/boot.img",
            "EDIT_FILES_JSON": '["common/Makefile"]', "SOURCE_OVERRIDES_URL": "", "SOURCE_OVERRIDES_SHA256": "",
            "CUSTOM_SCRIPT_URL": "", "EXTRA_BAZEL_ARGS": "", "BAZEL_TARGET": "", "BOOT_PARTITION_SIZE": "",
            "CNB_BUILD_ID": "local-test", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        }
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        # Do not inherit a real CNB build context in local tests.
        os.environ.pop("CNB_BUILD_WORKSPACE", None)
        os.environ.pop("CNB_COMMIT", None)
        self.state = Path(self.env["STATE_DIR"])
        self.artifacts = Path(self.env["ARTIFACT_DIR"])
        self.source = Path(self.env["GKI_ROOT"])
        for path in (self.state, self.artifacts, self.source / "common"):
            path.mkdir(parents=True, exist_ok=True)
        self.makefile = self.source / "common/Makefile"
        self.makefile.write_bytes(b"VERSION = 6\nall:\n\techo original\r\n")
        subprocess.run(["git", "init", "--quiet", str(self.makefile.parent)], check=True)
        subprocess.run(["git", "-C", str(self.makefile.parent), "add", "Makefile"], check=True)
        (self.state / "projects.txt").write_text("common\n")
        (self.state / "manifest.xml").write_text('<manifest><project name="common"/></manifest>')

    def executable(self, path, body):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        path.chmod(0o755)
        return path

    def script(self, name, *args):
        return subprocess.run(["bash", str(ROOT / "scripts" / name), *args],
                              env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def fake_curl(self):
        binary = self.base / "bin"
        self.executable(binary / "curl", """#!/usr/bin/env python3
import os, shutil, sys
args = sys.argv[1:]
source = os.environ['TEST_ARCHIVE'] if args[-1].endswith('overrides.tar.gz') else os.environ['TEST_CUSTOM_SCRIPT']
shutil.copyfile(source, args[args.index('--output') + 1])
""")
        os.environ["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
