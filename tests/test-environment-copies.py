#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("copies", ROOT / "tools/prepare-environment-copies.py")
copies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copies)
IMAGE = "sha256:" + "a" * 64


def node(filename="", mode=stat.S_IFREG | 0o600, data=b"owner data"):
    inode = SimpleNamespace(i_mode=mode, i_uid=1000, i_uid_high=0, i_gid=1000, i_gid_high=0,
                            i_links_count=1, i_extra=b"opaque nonzero inode padding",
                            i_extra_isize=0, i_file_acl_high=0, i_file_acl_lo=0)
    return SimpleNamespace(filename=filename, inode=inode, size=len(data),
                           open=lambda: io.BytesIO(data), link="/not-followed")


class InventoryTests(unittest.TestCase):
    def fixture(self):
        file = node("uboot.env")
        link = node("alias", stat.S_IFLNK | 0o777)
        root = node(mode=stat.S_IFDIR | 0o700)
        children = [file, link]
        root.iterdir = lambda: iter(children)
        fs = SimpleNamespace(block_size=4096, sb=SimpleNamespace(s_inode_size=256), get=lambda _: root)
        return fs, file, children

    def test_opaque_extra_bytes_are_preserved_without_xattr_decoding(self):
        fs, file, _ = self.fixture()
        file.inode.i_uid_high = 1
        result = copies.inventory(bytes(copies.SIZE), lambda _: fs)
        row = result["/uboot.env"]
        self.assertEqual(row["uid"], 65536 + 1000)
        self.assertEqual(row["rawExtraSha256"], copies.sha(file.inode.i_extra))
        self.assertEqual(row["sha256"], copies.sha(b"owner data"))
        self.assertEqual(result["/alias"]["link"], "/not-followed")

    def test_external_attribute_blocks_are_compared_and_bounded(self):
        fs, file, _ = self.fixture()
        file.inode.i_file_acl_lo = 1
        raw = bytearray(copies.SIZE)
        raw[4096:8192] = b"x" * 4096
        result = copies.inventory(bytes(raw), lambda _: fs)
        self.assertEqual(result["/uboot.env"]["rawExternalAclSha256"], copies.sha(b"x" * 4096))
        file.inode.i_file_acl_lo = copies.SIZE // 4096
        with self.assertRaises(ValueError):
            copies.inventory(bytes(raw), lambda _: fs)

    def test_special_files_invalid_paths_duplicates_and_short_data_refused(self):
        for mode in ("special", "path", "duplicate", "short", "oversized-extra"):
            fs, file, children = self.fixture()
            if mode == "special": file.inode.i_mode = stat.S_IFBLK | 0o600
            if mode == "path": file.filename = "../escape"
            if mode == "duplicate": children.append(file)
            if mode == "short": file.size += 1
            if mode == "oversized-extra": file.inode.i_extra = b"x" * 513
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                copies.inventory(bytes(copies.SIZE), lambda _: fs)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = {}
        for i, name in enumerate(copies.NAMES):
            path = self.root / name
            with path.open("xb") as stream:
                stream.write(bytes([65 + i]) * 128)
                stream.truncate(copies.SIZE)
            self.sources[name] = path, copies.sha(path.read_bytes())
        self.output = self.root / "prepared"
        self.commands = []

    def inventory(self, raw):
        return {"/owner-file": {"sha256": copies.sha(raw[:128]), "mode": stat.S_IFREG | 0o600}}

    def checker(self, docker, container, path, repair, log):
        self.assertEqual(path.parent, self.output)
        self.assertNotIn(path, [p for p, _ in self.sources.values()])
        self.commands.append((path.name, repair))
        log.write_bytes(b"synthetic checker output\n")
        if repair:
            with path.open("r+b") as stream:
                stream.seek(2048)
                stream.write(b"filesystem metadata repair")
        return 1 if repair else 0

    def run_preparation(self, checker=None, **changes):
        options = dict(sources=self.sources, docker=Path("/unused/docker"), container=IMAGE,
                       output=self.output)
        options.update(changes)
        with patch.object(copies, "inventory", side_effect=self.inventory), \
             patch.object(copies, "run_checker", side_effect=checker or self.checker), \
             patch.object(copies.subprocess, "run", return_value=SimpleNamespace(stdout=IMAGE + "\n")):
            return copies.prepare(**options)

    def test_only_new_copies_repaired_and_originals_rechecked(self):
        result = self.run_preparation()
        self.assertTrue(result["complete"])
        self.assertTrue(result["originalBackupsUnchanged"])
        self.assertFalse(result["deviceAccess"])
        self.assertFalse(result["installed"])
        self.assertEqual(len(result["files"]), 2)
        self.assertEqual(self.commands, [(n, repair) for n in copies.NAMES for repair in (True, False)])
        for row in result["files"]:
            self.assertNotEqual(row["sourceSha256"], row["outputSha256"])
            self.assertTrue(row["reachableContentsMetadataAndRawAttributesUnchanged"])
            self.assertEqual(copies.sha(self.sources[row["name"]][0].read_bytes()), row["sourceSha256"])
        self.assertEqual((self.output / "manifest.json").stat().st_mode & 0o777, 0o600)

    def test_source_hash_symlink_hardlink_and_bad_size_refused(self):
        for mode in ("hash", "symlink", "hardlink", "size"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path = root / "source"
                path.write_bytes(b"x" * copies.SIZE)
                digest = copies.sha(path.read_bytes())
                if mode == "hash": digest = "0" * 64
                if mode == "symlink":
                    alias = root / "alias"; alias.symlink_to(path); path = alias
                if mode == "hardlink": os.link(path, root / "alias")
                if mode == "size": path.write_bytes(b"short")
                with self.assertRaises(ValueError):
                    copies.read_image(path, digest)

    def test_existing_output_repository_alias_and_mount_injection_refused(self):
        self.output.mkdir()
        sentinel = self.output / "keep"
        sentinel.write_text("existing")
        with self.assertRaises(ValueError):
            self.run_preparation()
        self.assertEqual(sentinel.read_text(), "existing")
        alias = self.root / "checkout"; alias.symlink_to(ROOT, target_is_directory=True)
        for output in (ROOT / "private-fixture", alias / "private-fixture", self.root / "comma,path"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.run_preparation(output=output)
            self.assertFalse(output.exists())

    def test_unpinned_container_and_space_refused(self):
        with self.assertRaises(ValueError):
            self.run_preparation(container="local/unpinned")
        with patch.object(copies.shutil, "disk_usage", return_value=SimpleNamespace(free=1)), \
             self.assertRaises(ValueError):
            self.run_preparation()
        self.assertFalse(self.output.exists())

    def test_content_change_or_failed_check_leaves_incomplete_output(self):
        for mode in ("content", "repair-failure", "check-failure", "timeout", "readonly-write"):
            self.output = self.root / ("failed-" + mode)
            def checker(docker, container, path, repair, log):
                code = self.checker(docker, container, path, repair, log)
                if mode == "timeout": raise subprocess.TimeoutExpired("fixture", 1)
                if mode == "repair-failure" and repair: return 4
                if mode == "check-failure" and not repair: return 4
                if (mode == "content" and repair) or (mode == "readonly-write" and not repair):
                    with path.open("r+b") as stream: stream.write(b"changed data")
                return code
            with self.subTest(mode=mode), self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                self.run_preparation(checker)
            report = json.loads((self.output / "manifest.json").read_text())
            self.assertFalse(report["complete"])
            self.assertIn("failure", report)
            if mode == "timeout":
                self.assertEqual(report["pendingContainer"],
                                 copies.container_name(self.output / "uenv.img", True))
            for path, digest in self.sources.values():
                self.assertEqual(copies.sha(path.read_bytes()), digest)

    def test_prior_result_or_original_change_during_second_file_refused(self):
        for mode in ("source", "result", "result-mode"):
            self.output = self.root / ("drift-" + mode)
            original = self.sources["uenv.img"][0].read_bytes()
            def checker(docker, container, path, repair, log):
                code = self.checker(docker, container, path, repair, log)
                if path.name == "uuenv.img" and not repair:
                    victim = self.sources["uenv.img"][0] if mode == "source" else self.output / "uenv.img"
                    if mode == "result-mode":
                        victim.chmod(0o644)
                    else:
                        with victim.open("r+b") as stream: stream.write(b"external modification")
                return code
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.run_preparation(checker)
            self.assertFalse(json.loads((self.output / "manifest.json").read_text())["complete"])
            self.sources["uenv.img"][0].write_bytes(original)

    def test_checker_container_sees_only_candidate_file(self):
        candidate = self.root / "candidate.img"
        candidate.write_bytes(b"fixture")
        for repair in (True, False):
            with patch.object(copies.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                code = copies.run_checker(Path("/unused/docker"), IMAGE, candidate, repair,
                                          self.root / ("repair.log" if repair else "check.log"))
            argv = run.call_args.args[0]
            self.assertEqual(code, 0)
            self.assertIn("--pull=never", argv)
            self.assertIn("--network=none", argv)
            self.assertIn("--cap-drop=ALL", argv)
            self.assertEqual(argv[argv.index("--name") + 1], copies.container_name(candidate, repair))
            mount = argv[argv.index("--mount") + 1]
            self.assertEqual(mount, "type=bind,source=" + str(candidate.resolve()) +
                             ",target=/candidate.img" + ("" if repair else ",readonly"))
            self.assertEqual(argv[-3:], ["/sbin/e2fsck", "-fy" if repair else "-fn", "/candidate.img"])


if __name__ == "__main__":
    unittest.main()
