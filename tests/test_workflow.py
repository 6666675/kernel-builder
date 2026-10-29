import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import textwrap
from unittest.mock import patch

from helpers import BuilderFixture, ROOT
from builder_lib import validate
from file_overrides import add_bytes, overrides, prepare, read_package, relative_path, source_file, write_package
from make_overrides import make
from render_cnb import render


class WorkflowTests(BuilderFixture):
    def fake_repo(self):
        shutil.rmtree(self.source)
        self.source.mkdir()
        binary = self.base / "bin"
        self.executable(binary / "flock", "#!/usr/bin/env bash\nexit 0\n")
        self.executable(binary / "sleep", "#!/usr/bin/env bash\nexit 0\n")
        self.executable(binary / "repo", """#!/usr/bin/env python3
import json, os, pathlib, sys
state = pathlib.Path(os.environ['STATE_DIR'])
with open(state / 'repo-calls.jsonl', 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'sync':
    counter = state / 'sync-count'
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    if count < 3 or os.environ.get('TEST_SYNC_ALWAYS_FAIL'):
        sys.exit(7)
elif sys.argv[1] == 'manifest':
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('<manifest/>')
elif sys.argv[1] == 'list':
    print('common')
""")
        os.environ["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]

    def modification(self):
        prepare()
        prepared = self.artifacts / "source-test-123.tar.gz"
        edited = self.base / "edited"
        (edited / "files/common").mkdir(parents=True)
        changed = b"VERSION = 6\nall:\n\techo edited\r\n"
        (edited / "files/common/Makefile").write_bytes(changed)
        target = self.base / "overrides.tar.gz"
        make(prepared, edited, target)
        os.environ["SOURCE_OVERRIDES_URL"] = "https://example.test/overrides.tar.gz"
        os.environ["SOURCE_OVERRIDES_SHA256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        (self.state / "overrides.tar.gz").write_bytes(target.read_bytes())
        return target, changed

    def test_input_validation_fails_before_any_network_or_compilation(self):
        cases = [("GKI_MANIFEST_URL", ""), ("GKI_ARCH", "mips"), ("REQUEST_ID", "../bad"),
                 ("GKI_MANIFEST_URL", "https://example.test/manifest?token=secret"),
                 ("BOOT_IMG_URL", "http://example.test/boot.img"), ("SOURCE_OVERRIDES_URL", "https://example.test/a"),
                 ("EXTRA_BAZEL_ARGS", "-- --bad"), ("GKI_ROOT", str(self.workspace))]
        for name, value in cases:
            with self.subTest(name=name), patch.dict(os.environ, {name: value}):
                result = self.script("entrypoint.sh", "validate-kernel")
                self.assertNotEqual(result.returncode, 0, result.stdout)
        validate("prepare")
        validate("kernel")

    def test_export_edit_check_script_then_apply_preserves_bytes_and_mode(self):
        original = self.makefile.read_bytes()
        self.makefile.chmod(0o640)
        _, edited = self.modification()
        self.assertEqual(self.makefile.read_bytes(), original)
        overrides()
        self.makefile.write_bytes(b"changes from custom script\n")
        overrides(True)
        self.assertEqual(self.makefile.read_bytes(), edited)
        self.assertEqual(self.makefile.stat().st_mode & 0o777, 0o640)

    def test_upstream_conflict_prevents_custom_script_and_compiler(self):
        package, _ = self.modification()
        self.makefile.write_bytes(b"upstream changed\n")
        custom = self.base / "custom.sh"
        custom.write_text('touch "$STATE_DIR/custom-ran"\n')
        os.environ.update(TEST_ARCHIVE=str(package), TEST_CUSTOM_SCRIPT=str(custom), CUSTOM_SCRIPT_URL="https://example.test/custom.sh")
        self.fake_curl()
        result = self.script("build_gki.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("上游文件冲突", result.stdout)
        self.assertFalse((self.state / "custom-ran").exists())

    def test_custom_script_failure_propagates(self):
        custom = self.base / "custom.sh"
        custom.write_text("exit 17\n")
        os.environ.update(TEST_CUSTOM_SCRIPT=str(custom), CUSTOM_SCRIPT_URL="https://example.test/custom.sh")
        self.fake_curl()
        result = self.script("build_gki.sh")
        self.assertEqual(result.returncode, 17, result.stdout)

    def test_source_sync_retries_and_uses_an_independent_reference_checkout(self):
        self.fake_repo()
        result = self.script("sync_sources.sh")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.state / "sync-count").read_text(), "4")
        calls = [json.loads(line) for line in (self.state / "repo-calls.jsonl").read_text().splitlines()]
        checkouts = [args for args in calls if args[0] == "init" and "--mirror" not in args]
        self.assertEqual(len(checkouts), 1)
        self.assertIn("--dissociate", checkouts[0])
        self.assertTrue(any(arg.startswith("--reference=") for arg in checkouts[0]))

    def test_sync_failure_propagates_through_the_public_entrypoint(self):
        self.fake_repo()
        os.environ["TEST_SYNC_ALWAYS_FAIL"] = "1"
        result = self.script("entrypoint.sh", "kernel")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.state / "sync-count").read_text(), "3")
        self.assertFalse((self.state / "kernel-success.json").exists())

    def test_kleaf_target_and_arguments_are_passed_without_shell_evaluation(self):
        self.executable(self.source / "tools/bazel", """#!/usr/bin/env python3
import json, os, pathlib, sys
if sys.argv[1] == 'run':
    pathlib.Path(os.environ['STATE_DIR'], 'bazel.json').write_text(json.dumps(sys.argv[1:]))
""")
        os.environ["EXTRA_BAZEL_ARGS"] = '--config=fast --define=NAME=$(touch-should-not-run)'
        result = self.script("build_gki.sh")
        self.assertEqual(result.returncode, 0, result.stdout)
        args = json.loads((self.state / "bazel.json").read_text())
        target = args.index("//common:kernel_aarch64_dist")
        self.assertLess(args.index("--lto=thin"), target)
        self.assertEqual(args[-2], "--")
        self.assertIn('--define=NAME=$(touch-should-not-run)', args)

    def test_legacy_path_uses_expected_configuration_and_thin_lto(self):
        (self.source / "common/build.config.gki.aarch64").write_text("# fixture\n")
        self.executable(self.source / "build/build.sh", '#!/usr/bin/env bash\nprintf "%s\\n" "$BUILD_CONFIG" "$LTO" > "$STATE_DIR/legacy.txt"\n')
        result = self.script("build_gki.sh")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.state / "legacy.txt").read_text().splitlines(), ["common/build.config.gki.aarch64", "thin"])

    def test_unsafe_paths_links_untracked_files_and_duplicate_archive_members_are_rejected(self):
        for value in ("../Makefile", "/Makefile", "common/../Makefile", "common/.repo/a.mk", "common/a.c", "common//Makefile"):
            with self.subTest(path=value), self.assertRaises(ValueError):
                relative_path(value)
        untracked = self.makefile.parent / "extra.mk"
        untracked.write_text("untracked\n")
        with self.assertRaises(subprocess.CalledProcessError):
            source_file(self.source, "common/extra.mk")
        self.makefile.unlink()
        self.makefile.symlink_to(untracked)
        with self.assertRaises(ValueError):
            source_file(self.source, "common/Makefile")
        archive = self.base / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            add_bytes(stream, "index.json", b"{}")
            add_bytes(stream, "index.json", b"{}")
        with self.assertRaises(ValueError):
            read_package(archive)
        with tarfile.open(archive, "w:gz") as stream:
            link = tarfile.TarInfo("files/common/Makefile")
            link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
            stream.addfile(link)
        with self.assertRaises(ValueError):
            read_package(archive)

    def test_mismatched_task_or_corrupt_package_never_applies(self):
        self.modification()
        original = self.makefile.read_bytes()
        with patch.dict(os.environ, {"REQUEST_ID": "another-task"}), self.assertRaises(ValueError):
            overrides(True)
        with patch.dict(os.environ, {"SOURCE_OVERRIDES_SHA256": "0" * 64}), self.assertRaises(ValueError):
            overrides(True)
        self.assertEqual(self.makefile.read_bytes(), original)

    def test_renderer_requires_pinned_revision_and_preserves_event_names(self):
        with self.assertRaises(ValueError):
            render("https://cnb.cool/demo/kernel-builder.git", "main", "docker.cnb.cool/demo/builder:latest")
        with self.assertRaises(ValueError):
            render("https://user:secret@cnb.cool/demo/builder.git", "a" * 40, "builder:latest")
        config = render("https://cnb.cool/demo/kernel-builder.git", "a" * 40, "docker.cnb.cool/demo/builder:" + "a" * 40)
        self.assertNotIn("@@", config)
        self.assertIn("api_trigger_kernel:", config)
        self.assertIn("api_trigger_prepare:", config)
        self.assertIn("publish-kernel", config)
        self.assertIn("git -C .kernel-builder checkout --quiet --detach", config)

    def test_bootstrap_checks_fetched_commit_and_keeps_credentials_out_of_remote(self):
        ref = "a" * 40
        config = render("https://cnb.cool/demo/kernel-builder.git", ref, "builder:test")
        body = config.split("script: &fetch_builder |\n", 1)[1].split("        - name: 同步", 1)[0]
        bootstrap = self.base / "bootstrap.sh"
        bootstrap.write_text(textwrap.dedent(body))
        binary = self.base / "bin"
        self.executable(binary / "git", """#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
if 'rev-parse' in sys.argv:
    print(os.environ.get('TEST_FETCHED_HEAD', os.environ['BUILDER_REF']))
elif 'fetch' in sys.argv:
    askpass = os.environ['GIT_ASKPASS']
    result = subprocess.run([askpass, 'Password for repository:'], capture_output=True, text=True, check=True)
    pathlib.Path(os.environ['STATE_DIR'], 'bootstrap.json').write_text(json.dumps({
        'args': sys.argv[1:], 'askpass': askpass,
        'credential_correct': result.stdout.strip() == os.environ['BUILDER_GIT_TOKEN']}))
""")
        os.environ.update(PATH=str(binary) + os.pathsep + os.environ["PATH"], BUILDER_REF=ref,
                          BUILDER_REPO_URL="https://cnb.cool/demo/kernel-builder.git", BUILDER_GIT_TOKEN="dummy-readonly-token")
        result = subprocess.run(["bash", str(bootstrap)], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        info = json.loads((self.state / "bootstrap.json").read_text())
        self.assertTrue(info["credential_correct"])
        self.assertFalse(Path(info["askpass"]).exists())
        self.assertNotIn("dummy-readonly-token", " ".join(info["args"]) + result.stdout + result.stderr)
        with patch.dict(os.environ, {"TEST_FETCHED_HEAD": "b" * 40}):
            result = subprocess.run(["bash", str(bootstrap)], cwd=self.workspace, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_export_contains_only_standalone_sources(self):
        from package_repository import package
        archive = package(self.base / "builder.tar.gz")
        with tarfile.open(archive) as stream:
            names = stream.getnames()
        self.assertIn("kernel-builder/Dockerfile", names)
        self.assertIn("kernel-builder/.cnb.yml", names)
        self.assertFalse(any("__pycache__" in name or "/.git/" in name for name in names))
