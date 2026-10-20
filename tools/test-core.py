#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run synthetic boot-policy and transaction tests without an Android device."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", required=True, type=Path, help="JDK 17 directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    package = "local.codex.tvswitchprobe."
    source = root / "app/src/local/codex/tvswitchprobe"
    classes = ["BootEnvironment", "DefaultBootPolicy", "DefaultStateTransaction",
               "EnvironmentTransaction"]
    tests = ["BootEnvironmentTest", "DefaultBootPolicyTest",
             "EnvironmentTransactionTest", "ImageTransactionTest"]
    files = [source / (name + ".java") for name in classes]
    files += [root / "updater/src/local/codex/tvswitchprobe/ImageTransaction.java"]
    files += [root / "tests" / (name + ".java") for name in tests]
    javac, java = [args.java_home / "bin" / name for name in ("javac", "java")]
    if not javac.is_file() or not java.is_file():
        parser.error("--java-home must contain bin/javac and bin/java")
    with tempfile.TemporaryDirectory(prefix="freeguy-core-tests-") as temporary:
        subprocess.run([str(javac), "-encoding", "UTF-8", "-d", temporary,
                        *map(str, files)], check=True)
        for name in tests:
            subprocess.run([str(java), "-ea", "-cp", temporary, package + name], check=True)


if __name__ == "__main__":
    main()
