#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Repair NEW local uenv/uuenv file-backed copies and verify preserved contents.

Never opens a TV, mounts a filesystem, writes a source backup or prepares a
physical-partition flash. Private output is incomplete until every check passes.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("data_backup", ROOT / "tools/backup-data.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
need = backup.need
SIZE = 16 * 1024**2
NAMES = ("uenv.img", "uuenv.img")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def read_image(path, expected=None):
    need(path.is_file() and not path.is_symlink(), "Expected a regular local image")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == SIZE,
             "Expected one regular 16 MiB file without hard links")
        data = stream.read(SIZE + 1)
        after = os.fstat(stream.fileno())
    need(len(data) == SIZE and identity(before) == identity(after) == identity(path.stat()),
         "Image changed while reading")
    if expected is not None:
        need(re.fullmatch("[0-9a-f]{64}", expected) and sha(data) == expected, "Source hash mismatch")
    return data, identity(after)


def inventory(raw, factory=None):
    """Compare reachable data plus raw attribute storage, without decoding xattrs.

    Some OEM inodes have i_extra_isize=0 with nonzero unused extra bytes. Treating
    all those bytes as an xattr header rejects these images. Compare the entire
    raw area instead; do not infer that attributes are absent.
    """
    import io
    if factory is None:
        from dissect.extfs import ExtFS
        factory = ExtFS
    need(len(raw) == SIZE, "Unexpected image size")
    stream = io.BytesIO(raw)
    fs = factory(stream)
    need(fs.block_size in (1024, 2048, 4096, 8192)
         and fs.sb.s_inode_size in (128, 256, 512), "Unsupported filesystem geometry")
    pending, result = [("/", fs.get("/"))], {}
    while pending:
        path, node = pending.pop()
        need(path not in result and len(result) < 128, "Repeated or oversized environment tree")
        inode = node.inode
        mode = inode.i_mode
        extra = inode.i_extra
        need(len(extra) <= fs.sb.s_inode_size, "Oversized inline attribute area")
        acl = (inode.i_file_acl_high << 32) | inode.i_file_acl_lo
        external = b""
        if acl:
            offset = acl * fs.block_size
            need(0 < offset and offset + fs.block_size <= SIZE, "External attributes outside image")
            external = raw[offset:offset + fs.block_size]
        row = dict(mode=mode, uid=inode.i_uid | (inode.i_uid_high << 16),
                   gid=inode.i_gid | (inode.i_gid_high << 16), links=inode.i_links_count,
                   extraIsize=inode.i_extra_isize, rawExtraSha256=sha(extra),
                   rawExternalAclSha256=sha(external))
        if stat.S_ISDIR(mode):
            for child in node.iterdir():
                if child.filename in (".", ".."):
                    continue
                need("/" not in child.filename and "\0" not in child.filename
                     and child.filename, "Invalid directory entry")
                pending.append((path.rstrip("/") + "/" + child.filename, child))
        elif stat.S_ISREG(mode):
            need(0 <= node.size <= SIZE, "Oversized environment file")
            data = node.open().read(SIZE + 1)
            need(len(data) == node.size, "Incomplete environment file")
            row.update(bytes=len(data), sha256=sha(data))
        elif stat.S_ISLNK(mode):
            need(isinstance(node.link, str) and len(node.link) <= 4096, "Invalid symlink")
            row["link"] = node.link
        else:
            raise ValueError("Unexpected special filesystem entry")
        result[path] = row
    return result


def container_name(path, repair):
    material = str(path.resolve()) + ("\0repair" if repair else "\0check")
    return "freeguy-env-" + sha(material.encode())[:24]


def run_checker(docker, container, path, repair, log):
    # Only a newly created output file is exposed, never a source or directory.
    mount = "type=bind,source=" + str(path.resolve()) + ",target=/candidate.img"
    if not repair:
        mount += ",readonly"
    command = [str(docker), "run", "--pull=never", "--rm", "--network=none", "--read-only",
               "--cap-drop=ALL", "--security-opt=no-new-privileges",
               "--name", container_name(path, repair), "--mount", mount,
               container, "/sbin/e2fsck", "-fy" if repair else "-fn", "/candidate.img"]
    with log.open("xb") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=120)
        stream.flush()
        os.fsync(stream.fileno())
    return result.returncode


def prepare(sources, docker, container, output):
    backup.private_output(output)
    need(not output.exists() and not output.is_symlink() and output.parent.is_dir(),
         "Use a new private directory; never reuse repair output")
    need(not any(c in str(output.resolve()) for c in (",", "\n", "\r")),
         "Unsupported container mount path")
    need(set(sources) == set(NAMES), "Supply exactly the two environment-copy files")
    need(re.fullmatch(r"sha256:[0-9a-f]{64}", container), "Pin an existing local container image ID")
    need(shutil.disk_usage(output.parent).free > 2 * SIZE + 256 * 1024**2,
         "Insufficient space for copies and logs")
    originals, trees, identities, output_identities = {}, {}, {}, {}
    for name in NAMES:
        path, expected = sources[name]
        originals[name], identities[name] = read_image(path, expected)
        trees[name] = inventory(originals[name])
    need(len(set(identities.values())) == 2, "The two sources alias the same file")
    image = subprocess.run([str(docker), "image", "inspect", container, "--format", "{{.Id}}"],
                           capture_output=True, text=True, timeout=30, check=True)
    need(image.stdout.strip() == container, "Local container identity mismatch")
    os.umask(0o077)
    output.mkdir(mode=0o700)
    report = dict(mode="local-environment-copy-preparation", complete=False, pending=None,
                  pendingContainer=None,
                  deviceAccess=False, originalBackupsWritten=False, containerImage=container,
                  installed=False, bootCompatibilityVerified=False, files=[])
    def save():
        backup.save(output / "manifest.json", report)
    save()
    try:
        for name in NAMES:
            source, expected = sources[name]
            need(read_image(source, expected)[1] == identities[name], "Source changed before copying")
            destination = output / name
            report["pending"] = "copy-" + name
            save()
            with destination.open("xb") as stream:
                stream.write(originals[name])
                stream.flush()
                os.fsync(stream.fileno())
            copied, copied_id = read_image(destination, expected)
            need(copied == originals[name], "Copy does not match original")
            report.update(pending="repair-" + name, pendingContainer=container_name(destination, True))
            save()
            repair_code = run_checker(docker, container, destination, True, output / (name + ".repair.log"))
            need(repair_code in (0, 1), "Repair did not finish successfully; keep incomplete output")
            report["pendingContainer"] = None
            repaired, repaired_id = read_image(destination)
            need(repaired_id[:2] == copied_id[:2], "Repair replaced the output inode")
            after = inventory(repaired)
            need(after == trees[name], "Reachable contents, ownership, modes or raw attributes changed")
            report.update(pending="readonly-check-" + name,
                          pendingContainer=container_name(destination, False))
            save()
            check_code = run_checker(docker, container, destination, False, output / (name + ".check.log"))
            need(check_code == 0, "Read-only filesystem check did not pass")
            report["pendingContainer"] = None
            checked, checked_id = read_image(destination)
            need(checked == repaired and checked_id == repaired_id, "Read-only check changed the image")
            output_identities[name] = checked_id
            report["files"].append(dict(name=name, bytes=SIZE, sourceSha256=expected,
                                        outputSha256=sha(repaired), repairExitCode=repair_code,
                                        checkExitCode=check_code, reachableEntries=len(after),
                                        repairContainer=container_name(destination, True),
                                        checkContainer=container_name(destination, False),
                                        reachableContentsMetadataAndRawAttributesUnchanged=True,
                                        treeSha256=sha(json.dumps(after, sort_keys=True,
                                                                 separators=(",", ":")).encode())))
            report["pending"] = None
            save()
        # A change to the first source or result while processing the second
        # must prevent the pair being reported complete.
        for row in report["files"]:
            name = row["name"]
            need(read_image(sources[name][0], row["sourceSha256"])[1] == identities[name],
                 "Original source changed during preparation")
            need(read_image(output / name, row["outputSha256"])[1] == output_identities[name],
                 "Candidate file identity changed during preparation")
        report.update(complete=True, originalBackupsUnchanged=True)
        save()
        return report
    except BaseException as exc:
        report.update(complete=False, failure=type(exc).__name__)
        try:
            save()
        except BaseException:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("uenv", "uuenv"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--docker", type=Path, required=True)
    parser.add_argument("--container-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = {"uenv.img": (args.uenv, args.uenv_sha256), "uuenv.img": (args.uuenv, args.uuenv_sha256)}
    result = prepare(sources, args.docker, args.container_id, args.output)
    print(json.dumps(dict(complete=result["complete"], files=len(result["files"]),
                          originalBackupsUnchanged=result["originalBackupsUnchanged"],
                          deviceAccess=False, installed=False)))


if __name__ == "__main__":
    main()
