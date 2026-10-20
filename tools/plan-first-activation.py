#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Join first-install evidence and calculate an exactly reversible boot entry.

Offline only. The output contains private boot arguments, not a runnable script.
It neither authorizes a write nor establishes that a serial console is available.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = load("first_verification", "verify-first-install.py")
recovery = load("serial_recovery", "serial-recovery.py")
need, inventory = verify.need, verify.inventory
APPENDED_FLOOR = 626
MARGIN = 64


def entry(stock, allowed, observed, kernel):
    """Match a complete observed command line, then check both length bounds."""
    need(recovery.restore_plan(stock, allowed) is None, "Expected original stock arguments")
    restored = recovery.restore_plan(observed, allowed)
    need(recovery.fingerprint(restored or observed) == recovery.fingerprint(stock),
         "Observed arguments belong to another stock profile")
    actual = recovery.words(kernel)
    expected = recovery.words(observed)
    # mboot can move androidboot.first_setup and insert cgroup_disable before
    # the rest of its additions. Match every complete token, not a prefix.
    need(len(actual) == len(set(actual))
         and all(actual.count(token) == 1 for token in expected),
         "Require a complete matching kernel command line")
    words = [token for token in actual if token not in expected]
    need(words, "Missing bootloader-added arguments")
    appended_bytes = sum(len(token) + 1 for token in words)
    need(len(kernel) == len(observed) + appended_bytes, "Ambiguous command-line accounting")
    protected = {"root", "init", "rdinit", "rootfstype", "rootflags", "ro", "rw", "loglevel"}
    need(all(words) and not any(word.split("=", 1)[0] in protected for word in words),
         "Appended kernel arguments override startup")
    reverse = {target: source for source, target in recovery.REPLACE.items()}
    tokens = recovery.words(stock)
    for source, target in reverse.items():
        need(tokens.count(source) == 1 and target not in tokens, "Ambiguous stock substitution")
    target = " ".join(reverse.get(token, token) for token in tokens) + recovery.SUFFIX
    need(recovery.restore_plan(target, allowed) == stock, "Exact undo failed")
    measured = len(target) + appended_bytes
    conservative = len(target) + max(APPENDED_FLOOR, appended_bytes)
    need(conservative + MARGIN <= 2047, "Kernel command-line safety margin missing")
    # Both commands must fit the bootloader input buffer after escaping.
    recovery.encode_setenv(target)
    recovery.encode_setenv(stock)
    return dict(stockBootargs=stock, temporaryBootargs=target, undoBootargs=stock,
                observedKernelSha256=hashlib.sha256(kernel.encode("ascii")).hexdigest(),
                appendedBytes=appended_bytes, measuredTargetBytes=measured,
                conservativeTargetBytes=conservative, reservedMarginBytes=MARGIN,
                exactUndoVerified=True)


def evidence(plan, staged, checked):
    rows = verify.check_journal(staged, plan)
    loader = verify.probe_audit(plan)
    need(checked.get("mode") == "first-install-verification"
         and checked.get("complete") is True and checked.get("pending") is None
         and "failure" not in checked, "Incomplete verification record")
    need(checked.get("stagingContentSha256") == verify.content_hash(staged)
         and checked.get("inventorySha256") == verify.content_hash(plan)
         and checked.get("setupLoaderSha256") == loader["sha256"]
         and checked.get("filesVerified") == len(rows), "Verification does not match staging")
    identity = checked.get("identity", {})
    need(identity.get("deviceBindingSha256") == plan["deviceBinding"]
         and identity.get("deviceBindingScheme") == plan["deviceBindingScheme"]
         and isinstance(identity.get("bootId"), str)
         and verify.re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", identity["bootId"])
         and checked.get("stagingBootId") == staged["identity"]["bootId"],
         "Verification device or boot identity mismatch")
    process = checked.get("process", {})
    need(checked.get("probeRequested") is True and checked.get("probeStarted") is True
         and type(checked.get("probeExit")) is int and checked["probeExit"] == 90
         and isinstance(process, dict) and process.get("finished") is True
         and type(process.get("remotePid")) is int and process["remotePid"] > 1,
         "Require a completed non-PID-1 refusal probe")
    need(all(checked.get(key) is False for key in
             ("startupChanged", "rebootIssued", "bootActivationAuthorized",
              "runtimeVerified", "filesystemsVerified")), "Unexpected verification scope")
    return dict(setupLoaderSha256=loader["sha256"], verificationBootId=identity["bootId"],
                stagingContentSha256=verify.content_hash(staged),
                verificationContentSha256=verify.content_hash(checked))


def pinned(path, digest):
    need(inventory.SHA.fullmatch(digest), "Pin each evidence file with its reviewed SHA-256")
    value = inventory.read_json(path)
    raw = path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == digest and json.loads(raw) == value,
         "Evidence file hash mismatch")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--known-device-binding", required=True)
    parser.add_argument("--known-device-binding-scheme", choices=inventory.BINDING_SCHEMES, required=True)
    for name in ("staging-record", "verification-record", "boot-observation"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--recovery-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    recovery.private_output(args.output)
    staged = pinned(args.staging_record, args.staging_record_sha256)
    checked = pinned(args.verification_record, args.verification_record_sha256)
    observed = pinned(args.boot_observation, args.boot_observation_sha256)
    plan = inventory.plan(inventory.read_json(args.inputs), args.inputs.parent,
                          args.known_device_binding, args.known_device_binding_scheme)
    report = evidence(plan, staged, checked)
    need(observed.get("deviceBindingSha256") == plan["deviceBinding"]
         and observed.get("deviceBindingScheme") == plan["deviceBindingScheme"],
         "Boot observation belongs to another device")
    allowed = recovery.read_profile(args.recovery_profile)
    report.update(entry(observed["stockBootargs"], allowed, observed["observedBootargs"],
                        observed["kernelCmdline"]))
    report.update(mode="first-activation-plan", deviceAccess=False, deviceWritesIssued=False,
                  activationAuthorized=False, liveConsoleVerified=False,
                  filesystemHealthVerified=False, bootObservationSha256=args.boot_observation_sha256,
                  remainingRequirements=["fresh stopped original-B console and unchanged stock arguments",
                                         "verified backups and same-device demonstrated serial undo",
                                         "filesystem health and kernel startup compatibility",
                                         "checked activation transaction and observed first boot"])
    args.output.mkdir(mode=0o700)
    verify.stager.save(args.output, report)
    print(json.dumps({key: report[key] for key in
                      ("mode", "measuredTargetBytes", "conservativeTargetBytes",
                       "exactUndoVerified", "activationAuthorized", "deviceWritesIssued")}))


if __name__ == "__main__":
    main()
