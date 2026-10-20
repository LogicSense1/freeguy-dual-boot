#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Join a fresh owner-capture stage and probe with its original boot observation.

Offline only. Produces the same reversible entry used by first installation,
but binds it to the audited owner collector. Does not access the serial port,
authorize activation, establish recoverability or start the TV.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


owner = load("stage-owner-capture")
activation = load("plan-first-activation")
need = activation.need


def assemble(plan, staged, verified, observed, observation_sha, allowed):
    need(plan.get("mode") == "owner-capture-stage-plan",
         "Expected a fresh owner-capture inventory")
    report = activation.evidence(plan, staged, verified)
    need(activation.inventory.SHA.fullmatch(observation_sha),
         "Pin the complete boot observation")
    need(observed.get("deviceBindingSha256") == plan["deviceBinding"]
         and observed.get("deviceBindingScheme") == plan["deviceBindingScheme"]
         and observed.get("bootId") == report["verificationBootId"],
         "Boot observation must belong to this TV and verified boot")
    report.update(activation.entry(observed["stockBootargs"], allowed,
                                   observed["observedBootargs"], observed["kernelCmdline"]))
    # Fresh capture starts in stock Android. A prior temporary entry is not
    # evidence that the freshly staged file has run or is safe to run again.
    need(activation.recovery.restore_plan(observed["observedBootargs"], allowed) is None,
         "Require an original stock boot observation")
    report.update(
        mode="first-activation-plan", purpose="owner-capture",
        deviceBindingSha256=plan["deviceBinding"],
        deviceBindingScheme=plan["deviceBindingScheme"],
        ownerCapture=plan["ownerCapture"],
        deviceAccess=False, deviceWritesIssued=False, activationAuthorized=False,
        liveConsoleVerified=False, filesystemHealthVerified=False,
        bootObservationSha256=observation_sha,
        remainingRequirements=[
            "fresh stopped original-B console and unchanged stock arguments",
            "independent original backups and demonstrated same-device serial undo",
            "checked activation transaction and observed original-B boot",
            "same-boot collection and independent validation before cleanup",
        ])
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("stock-boot", "capture-build", "loader-build", "recovery-profile", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--known-device-binding", required=True)
    p.add_argument("--known-device-binding-scheme",
                   choices=owner.inventory.BINDING_SCHEMES, required=True)
    for name in ("staging-record", "verification-record", "boot-observation"):
        p.add_argument("--" + name, type=Path, required=True)
        p.add_argument("--" + name + "-sha256", required=True)
    a = p.parse_args()
    os.umask(0o077)
    activation.recovery.private_output(a.output)
    need(not a.output.exists() and not a.output.is_symlink() and a.output.parent.is_dir(),
         "Use a new private output directory")
    plan = owner.plan(a.stock_boot, a.capture_build, a.loader_build,
                      a.known_device_binding, a.known_device_binding_scheme)
    staged = activation.pinned(a.staging_record, a.staging_record_sha256)
    verified = activation.pinned(a.verification_record, a.verification_record_sha256)
    observed = activation.pinned(a.boot_observation, a.boot_observation_sha256)
    report = assemble(plan, staged, verified, observed, a.boot_observation_sha256,
                      activation.recovery.read_profile(a.recovery_profile))
    a.output.mkdir(mode=0o700)
    owner.stage.save(a.output, report)
    print(json.dumps({key: report[key] for key in
                      ("purpose", "measuredTargetBytes", "conservativeTargetBytes",
                       "exactUndoVerified", "activationAuthorized", "deviceWritesIssued")}))


if __name__ == "__main__":
    main()
