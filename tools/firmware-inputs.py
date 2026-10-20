#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Download or verify pinned OTA inputs. Does not contact a TV or install firmware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def need(ok, message):
    if not ok:
        raise ValueError(message)


def official(url, package):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    need(parsed.scheme == "https" and not parsed.username and not parsed.password
         and parsed.port in (None, 443), "HTTPS official distribution required")
    allowed = (host == "ota-mitv.cdn.pandora.xiaomi.com" if package == "hyperos"
               else host == "android.googleapis.com" or host.endswith(".gvt1.com"))
    need(allowed, "Unexpected distribution host")
    return url


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, package):
        self.package = package

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official(newurl, self.package)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verify(path, record):
    need(path.is_file() and not path.is_symlink(), "Expected a regular local file")
    need(path.stat().st_size == record["bytes"], "Package size mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    need(digest.hexdigest() == record["sha256"], "Package SHA-256 mismatch")
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("META-INF/com/android/metadata")
        need(info.file_size < 65536, "Unexpected metadata size")
        fields = dict(line.split("=", 1) for line in
                      archive.read(info).decode("utf-8").splitlines() if "=" in line)
        need(fields.get("pre-device") == record["device"], "Device metadata mismatch")
        need(fields.get("post-build") == record["build"], "Build metadata mismatch")
        need(fields.get("ota-type") == "AB", "Expected an A/B OTA")
    return {"sha256": record["sha256"], "bytes": record["bytes"],
            "device": record["device"], "verified": True,
            "installationPerformed": False, "signatureTrustEstablished": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("download", "verify"))
    parser.add_argument("package", choices=("hyperos", "google-tv-donor"))
    parser.add_argument("--file", type=Path, required=True,
                        help="Private input path outside this repository")
    args = parser.parse_args()
    os.umask(0o077)
    record = json.loads((ROOT / "tools/firmware-inputs.json").read_text())["packages"][args.package]
    path = args.file.expanduser().absolute()
    need(ROOT not in path.resolve().parents and path.resolve() != ROOT,
         "Keep firmware outside the source repository")
    if args.action == "download":
        part = path.with_name(path.name + ".part")
        need(not path.exists() and not path.is_symlink()
             and not part.exists() and not part.is_symlink(),
             "Use a new destination; existing and partial downloads are preserved")
        need(path.parent.is_dir(), "Create the private destination directory first")
        opener = urllib.request.build_opener(OfficialRedirect(args.package))
        request = urllib.request.Request(official(record["url"], args.package))
        with opener.open(request, timeout=60) as response, part.open("xb") as out:
            official(response.url, args.package)
            need(response.status == 200, "Expected the complete package response")
            total = 0
            while True:
                chunk = response.read(8 * 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                need(total <= record["bytes"], "Download exceeds pinned size")
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        result = verify(part, record)
        # No overwrite even if another process created the final name.
        os.link(part, path)
        part.unlink()
    else:
        result = verify(path, record)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
