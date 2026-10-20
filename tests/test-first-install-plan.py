#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic inventory inputs; no firmware, keys from devices or device access."""
import base64
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("planner", ROOT / "tools/plan-first-install.py")
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)
sha = lambda b: hashlib.sha256(b).hexdigest()
BINDING = sha(b"synthetic owner-device identity")
SCHEME = "freeguy-emmc-cid-serial-v1"


def entry(name, data=b"", directory=False):
    return dict(name=name, data=data,
                fields=[1, 0o40700 if directory else 0o100600, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0])


def fixture(root):
    def file(name, data):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return dict(path=name, bytes=len(data), sha256=sha(data))
    images = {name: file(name, bytes([i]) * 4096)
              for i, (name, _, _, _) in enumerate(planner.runtime.LAYOUT)}
    voice = {name: file(name, name.encode()) for name in ("oem.apk", "libmibluetooth_jni.so")}
    hashes = {name: images[name]["sha256"] for name, _, _, mode in planner.runtime.LAYOUT if mode == "ro"}
    mounts, index = planner.runtime.mount_files(hashes)
    key = bytearray(524)
    struct.pack_into("<I", key, 0, 64)
    struct.pack_into("<I", key, 520, 65537)
    key = base64.b64encode(key) + b" fixture\n"
    contents = {name: value[1] for name, value in planner.first.scripts().items()}
    contents.update({"codex/mount-images.sh": mounts, "codex/immutable.sha256": index,
                     "codex/host-adb-key": key, "codex/autovoice/payload.sha256":
                     "".join(v["sha256"] + "  " + k + "\n" for k, v in voice.items()).encode()})
    rows = [entry("first_stage_ramdisk", directory=True)]
    for prefix in ("", "first_stage_ramdisk/"):
        rows += [entry(prefix + "codex", directory=True),
                 entry(prefix + "codex/autovoice", directory=True)]
        rows += [entry(prefix + name, data) for name, data in contents.items()]
    root_record = file("setup.cpio.gz", gzip.compress(planner.runtime.components.pack(rows), mtime=0))
    loader_record = file("build/loader", b"synthetic loader fixture")
    _, _, table = planner.runtime.archive.table(rows)
    source_sha = sha((ROOT / "runtime/loader.c").read_bytes())
    components = {n: sha((ROOT / "runtime" / n).read_bytes()) for n in
                  ("internal-env-inspect.h", "internal-env-transform.c", "internal-env-once.h")}
    manifest = dict(sha256=loader_record["sha256"], inputRamdiskSha256=root_record["sha256"],
                    entries=table, sourceSha256=source_sha, componentSources=components)
    audit = dict(sha256=loader_record["sha256"], passed=True,
                 cases=[{"case": name} for name in sorted(planner.AUDIT_CASES)], liveTested=False,
                 originalEntriesVerified=len(rows), sourceSha256=source_sha, componentSources=components)
    (root / "build/manifest.json").write_text(json.dumps(manifest))
    (root / "build/audit.json").write_text(json.dumps(audit))
    return dict(version=1, deviceBinding=BINDING, deviceBindingScheme=SCHEME, images=images, voice=voice,
                setupRoot=root_record, setupLoader=loader_record)


class PlanTests(unittest.TestCase):
    def test_inventory_fixed_paths_seven_private_files_and_no_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = fixture(root)
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            result = planner.plan(inputs, root, BINDING, SCHEME)
            self.assertEqual(len(result["files"]), 21)
            self.assertEqual(len(result["mutableFiles"]), 7)
            self.assertEqual(result["totalBytes"], sum(v["bytes"] for v in result["files"]))
            self.assertFalse(result["installAuthorized"])
            self.assertFalse(result["changesStartup"])
            self.assertFalse(result["deviceAccess"])
            self.assertIn(planner.LOADER + "/scratch", result["directories"])
            self.assertEqual(result["markers"][0]["content"], "codex-freeguy-fileboot-v1\n")
            self.assertIn("/data/local/codex-system-default-v1", result["mustBeAbsent"])
            self.assertTrue(all(not row["overwriteAllowed"] for row in result["files"]))
            self.assertEqual({row["target"] for row in result["files"]},
                             {planner.BASE + "/" + name for name in planner.IMAGE_NAMES} |
                             {planner.BASE + "/voice-assets-v1/" + name for name in inputs["voice"]} |
                             {planner.LOADER + "/envdaily"})
            self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes()
                                     for p in root.rglob("*") if p.is_file()})

    def test_wrong_binding_layout_hash_size_or_extra_field_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = fixture(root)
            for case in ("binding", "scheme", "missing", "extra", "hash", "size", "root", "boolsize"):
                value = copy.deepcopy(original)
                if case == "binding": value["deviceBinding"] = "0" * 64
                elif case == "scheme": value["deviceBindingScheme"] = "freeguy-android-serial-v1"
                elif case == "missing": value["images"].pop("persist.img")
                elif case == "extra": value["images"]["arbitrary.img"] = value["images"]["cache.img"]
                elif case == "hash": value["images"]["cache.img"]["sha256"] = "0" * 64
                elif case == "size": value["images"]["cache.img"]["bytes"] = 4097
                elif case == "boolsize": value["images"]["cache.img"]["bytes"] = True
                else: value["unexpected"] = "ignored?"
                with self.subTest(case=case), self.assertRaises(ValueError):
                    planner.plan(value, root, BINDING, SCHEME)

    def test_symlink_and_hard_link_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = fixture(root)
            record = inputs["images"]["cache.img"]
            (root / "link").symlink_to(root / record["path"])
            with self.assertRaises(ValueError):
                planner.checked_file(dict(record, path="link"), root)
            os.link(root / record["path"], root / "hard")
            with self.assertRaises(ValueError):
                planner.checked_file(record, root)

    def test_changed_immutable_image_or_voice_does_not_match_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = fixture(root)
            for category, name in (("images", "system.img"), ("voice", "oem.apk")):
                inputs = copy.deepcopy(original)
                path = root / inputs[category][name]["path"]
                old = path.read_bytes()
                changed = b"x" * len(old)
                path.write_bytes(changed)
                inputs[category][name]["sha256"] = sha(changed)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    planner.plan(inputs, root, BINDING, SCHEME)
                path.write_bytes(old)

    def test_loader_and_audit_disagreement_or_source_drift_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = fixture(root)
            path = root / "build/audit.json"
            original = json.loads(path.read_text())
            for key, value in (("passed", False), ("sha256", "0" * 64), ("cases", [{}] * 63),
                               ("cases", [{"case": "normal"}] * 64),
                               ("sourceSha256", "0" * 64), ("componentSources", {}),
                               ("originalEntriesVerified", 0)):
                audit = dict(original, **{key: value})
                path.write_text(json.dumps(audit))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    planner.plan(inputs, root, BINDING, SCHEME)
            path.write_text(json.dumps(original))
            path = root / "build/manifest.json"
            manifest = json.loads(path.read_text())
            manifest["entries"].pop()
            path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                planner.plan(inputs, root, BINDING, SCHEME)

    def test_cli_private_output_and_existing_or_repository_output_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = fixture(root)
            path = root / "inputs.json"
            path.write_text(json.dumps(inputs))
            def run(out):
                args = ["plan-first-install.py", "--inputs", str(path),
                        "--known-device-binding", BINDING, "--known-device-binding-scheme", SCHEME,
                        "--output", str(out)]
                with patch("sys.argv", args), patch("builtins.print"):
                    planner.main()
            out = root / "private-plan"
            run(out)
            saved = (out / "plan.json").read_bytes()
            self.assertEqual(len(json.loads(saved)["files"]), 21)
            self.assertEqual((out / "plan.json").stat().st_mode & 0o777, 0o600)
            for rejected in (out, ROOT / "never-write-a-private-plan"):
                with self.assertRaises(ValueError):
                    run(rejected)
            self.assertEqual((out / "plan.json").read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
