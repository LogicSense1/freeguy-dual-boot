#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Commit a daily loader and initial HyperOS preference from temporary Google.

Retains the setup loader. Never selects Google, writes boot configuration or
firmware, replaces data images, or reboots. A partial run is not auto-resumed.
"""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


access = load('first_access', 'prepare-first-switch-access.py')
recovery = load('serial_recovery', 'serial-recovery.py')
app = load('switch_builder', 'build-switch-app.py')
need, inventory, q = access.need, access.inventory, access.q
TARGET = access.LOADER + '/envdaily'
NEW = TARGET + '.setup-next'
BACKUP = TARGET + '.first-install'
STATE_NEW = access.SHARED + '.setup-next'
STATE_LABEL = 'u:object_r:system_data_file:s0'
BODY = b'CTV_DEFAULT_V1\nXIAOMI\nIDLE\n-\n-\n-\n'
INITIAL_STATE = BODY + hashlib.sha256(BODY).hexdigest().encode() + b'\n'
STATE_FILES = {STATE_NEW + '/state': INITIAL_STATE, STATE_NEW + '/fault': b'OK\n'}
sha = lambda data: hashlib.sha256(data).hexdigest()


def archive(record, parent):
    checked = inventory.checked_file(record, parent, inventory.runtime.archive.MAX_BYTES)
    raw = Path(checked['source']).read_bytes()
    need(sha(raw) == checked['sha256'], 'Runtime changed after checking')
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as source:
        data = source.read(inventory.runtime.archive.MAX_BYTES + 1)
    return checked, inventory.runtime.archive.entries(data)


def daily_plan(first_inputs, first_parent, plan, inputs, parent, profile):
    need(set(inputs) == {'dailyRoot', 'dailyLoader'}, 'Expected daily root and loader records')
    root, rows = archive(inputs['dailyRoot'], parent)
    _, first_rows = archive(first_inputs['setupRoot'], first_parent)
    derived, changes = inventory.first.transform(rows)
    table = inventory.runtime.archive.table
    need(table(derived)[2] == table(first_rows)[2] and len(changes) == 4,
         'Daily runtime must differ from setup only by the four checked script entries')
    loader = inventory.checked_file(inputs['dailyLoader'], parent, 128 * 1024**2)
    folder = Path(loader['source']).parent
    manifest = inventory.read_json(folder / 'manifest.json')
    audit = inventory.read_json(folder / 'audit.json')
    need(manifest.get('sha256') == audit.get('sha256') == loader['sha256']
         and manifest.get('inputRamdiskSha256') == root['sha256']
         and manifest.get('entries') == table(rows)[2]
         and audit.get('passed') is True and audit.get('originalEntriesVerified') == len(rows),
         'Daily loader manifest or audit mismatch')
    cases = audit.get('cases', [])
    need(len(cases) == 64 and {c.get('case') for c in cases} == inventory.AUDIT_CASES,
         'Require all daily loader audit cases')
    pid_case = next(c for c in cases if c['case'] == 'not-pid1')
    need(pid_case.get('end') == 'unprivileged-exit' and pid_case.get('syscalls') == 2
         and pid_case.get('environmentWrites') == 0, 'Non-PID-1 refusal audit missing')
    for name in ('internal-env-inspect.h', 'internal-env-transform.c', 'internal-env-once.h'):
        expected = sha((ROOT / 'runtime' / name).read_bytes())
        need(manifest.get('componentSources', {}).get(name) == expected
             and audit.get('componentSources', {}).get(name) == expected, 'Loader source drift')
    expected = sha((ROOT / 'runtime/loader.c').read_bytes())
    need(manifest.get('sourceSha256') == audit.get('sourceSha256') == expected, 'Loader source drift')
    app.pins(profile, False)
    files, _ = access.layout(plan)
    expected_files = {name: row['sha256'] for name, row in files.items()}
    expected_files[TARGET] = loader['sha256']
    need(profile['immutableFiles'] == expected_files, 'Switch profile does not match daily files')
    need(loader['sha256'] != files[TARGET]['sha256'], 'Daily loader is still the setup loader')
    return dict(loader, target=TARGET, rootSha256=root['sha256'],
                normalizedBootargsSha256=profile['normalizedBootargsSha256'],
                immutableFiles=expected_files)


class Device(access.Device):
    def __init__(self, adb, serial, plan, pins):
        super().__init__(adb, serial, plan, pins)
        self.allowed |= {NEW, BACKUP, STATE_NEW} | set(STATE_FILES)
        self.files = access.layout(plan)[0]
        self.files = dict(self.files, **{p: {} for p in (NEW, BACKUP, *STATE_FILES)})

    def physical_stock(self, allowed):
        before = self.guard()
        report = recovery.verify_persisted(self.adb[0], self.adb[2], allowed)
        need(report['bootId'] == before['bootId'] and self.guard() == before,
             'Boot changed during physical stock verification')
        return report

    def free_bytes(self):
        fields = self.shell("stat -f -c '%a %S' " + self.path(access.LOADER)).split()
        need(len(fields) == 2 and all(s.isdigit() for s in fields), 'Free space unavailable')
        return int(fields[0]) * int(fields[1])

    def set_metadata(self, path, value):
        need(path in (NEW, STATE_NEW, *STATE_FILES), 'Unexpected finalization metadata target')
        self.shell('chown ' + str(value['uid']) + ':' + str(value['gid']) + ' ' + self.path(path)
                   + ' && chmod ' + format(value['mode'] & 0o7777, 'o') + ' ' + self.path(path)
                   + ' && chcon ' + q(value['label']) + ' ' + self.path(path))

    def preserve_setup(self):
        self.shell('ln ' + self.path(TARGET) + ' ' + self.path(BACKUP))

    def replace_loader(self):
        self.shell('mv -f ' + self.path(NEW) + ' ' + self.path(TARGET))

    def create_state_directory(self):
        self.shell('mkdir -m 700 ' + self.path(STATE_NEW))

    def commit_state_directory(self):
        self.shell('test ! -e ' + self.path(access.SHARED) + ' && test ! -L '
                   + self.path(access.SHARED) + ' && mv ' + self.path(STATE_NEW)
                   + ' ' + self.path(access.SHARED))

    def read_state(self, name):
        need(name in ('state', 'fault'), 'Unknown preference file')
        path = access.SHARED + '/' + name
        self.allowed.add(path)
        self.files[path] = {}
        return self.digest(path)

    def probe(self, digest):
        # The audited loader calls only getpid and exit(90) outside PID 1.
        before = self.guard()
        command = ('test "$(cat /proc/sys/kernel/random/boot_id)" = ' + q(before['bootId'])
                   + ' && test "$(sha256sum ' + self.path(NEW) + ')" = '
                   + q(digest + '  ' + self.rawpath(NEW)) + ' && '
                   + self.path(NEW) + '; rc=$?; printf "CTV_DAILY_PROBE %s\\n" "$rc"')
        need(self.shell(command, 15) == 'CTV_DAILY_PROBE 90', 'Daily loader refusal probe failed')
        need(self.guard() == before, 'Boot changed during probe')


def validate_access(record, plan):
    need(record.get('mode') == 'first-switch-access' and record.get('complete') is True
         and record.get('inspectOnly') is False and record.get('pending') is None
         and 'failure' not in record and record.get('mutableMetadataUnchanged') is True,
         'Completed access preparation required')
    need(record.get('inventorySha256') == sha(json.dumps(plan, sort_keys=True).encode()),
         'Access preparation belongs to another inventory')
    need(all(record.get(k) is False for k in ('environmentWrites', 'firmwareWrites',
         'mutableImageWrites', 'sharedStateCreated', 'automaticDefaultEnabled', 'rebootIssued')),
         'Unexpected earlier changes')
    files, data = access.layout(plan)
    need(set(record['targets']) == set(access.DIRECTORIES) | set(files), 'Incomplete access targets')
    need(set(record['before']) == set(access.PARENTS + access.DIRECTORIES) | set(files) | set(data),
         'Incomplete initial metadata')
    return files, data


def finalize(device, plan, daily, prepared, output, apply=False):
    files, data = validate_access(prepared, plan)
    binding = device.guard()
    need(binding == prepared['binding'], 'Finalize in the same bounded setup boot as preparation')
    expected = dict(prepared['targets'])
    expected.update({p: prepared['before'][p] for p in access.PARENTS + tuple(data)})
    for path in (NEW, BACKUP, STATE_NEW, access.SHARED):
        need(device.metadata(path) is None, 'Existing finalization path; preserve partial work')
    links = {}
    proxy = device.metadata(access.PROXY)
    need(proxy is not None and proxy['mode'] == 0o40750 and proxy['uid'] == 0
         and proxy['gid'] == 1000 and proxy['label'] == access.LABEL
         and proxy['dev'] == expected['/data/vendor']['dev'], 'Proxy access changed')
    expected[access.PROXY] = proxy
    for path, target in access.LINKS.items():
        value = device.metadata(path)
        need(value is not None and value['mode'] == 0o120777 and value['uid'] == value['gid'] == 0
             and value['links'] == 1 and value['dev'] == proxy['dev'] and value['label'] == access.LABEL
             and device.readlink(path) == target, 'Proxy endpoint changed')
        links[path] = value
    expected.update(links)

    def verify():
        need(device.guard() == binding, 'Setup session changed')
        for path, value in expected.items():
            need(access.same(device.metadata(path), value), 'Finalization path changed: ' + path)
        for path, target in access.LINKS.items():
            need(device.readlink(path) == target, 'Proxy target changed')

    verify()
    for path, row in files.items():
        need(device.digest(path) == row['sha256'], 'Staged code changed')
    physical = device.physical_stock(daily['normalizedBootargsSha256'])
    verify()
    need(device.free_bytes() >= daily['bytes'] + 64 * 1024**2, 'Insufficient finalization reserve')
    record = dict(mode='first-install-finalize', complete=False, inspectOnly=not apply,
                  binding=binding, before=expected.copy(), daily=daily,
                  physicalStockBefore=physical, pending=None, operations=[], uploadProcess=None,
                  firmwareWrites=False, environmentWrites=False, mutableImageWrites=False,
                  automaticGoogleEnabled=False, rebootIssued=False, appsVerified=False)
    save = lambda: access.stage.save(output, record)
    save()
    if not apply:
        record['inspectionComplete'] = True; save(); return record

    def operation(kind, path, action):
        verify()
        record['pending'] = dict(kind=kind, path=path)
        save()
        action()
        record['operations'].append(record['pending'])
        record['pending'] = None
        save()

    def upload(path, stream):
        def event(value):
            record['uploadProcess'] = value; save()
        need(device.metadata(path) is None, 'Upload path appeared')
        device.upload(path, stream, event)
        need(record['uploadProcess'].get('finished') is True, 'Upload completion unknown')

    def new_file(path, digest, size):
        value = device.metadata(path)
        need(value is not None and value['mode'] == 0o100600 and value['uid'] == 0
             and value['links'] == 1 and value['bytes'] == size
             and value['dev'] == expected[access.LOADER]['dev']
             and device.digest(path) == digest, 'New file readback differs')
        expected[path] = value
        return value

    def metadata(path, value):
        operation('metadata', path, lambda: device.set_metadata(path, value))
        expected[path] = value
        verify()

    try:
        with Path(daily['source']).open('rb') as stream:
            st = os.fstat(stream.fileno())
            need(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_size == daily['bytes'],
                 'Daily loader input changed')
            need(sha(stream.read()) == daily['sha256'], 'Daily loader hash changed')
            stream.seek(0)
            operation('upload-loader', NEW, lambda: upload(NEW, stream))
        value = new_file(NEW, daily['sha256'], daily['bytes'])
        metadata(NEW, dict(value, uid=2000, gid=1000, mode=0o100750, label=access.LABEL))
        operation('flush-loader', NEW, lambda: device.fsync(NEW))
        operation('probe-loader', NEW, lambda: device.probe(daily['sha256']))
        need(device.metadata(BACKUP) is None, 'Setup backup path appeared')
        operation('preserve-setup', BACKUP, device.preserve_setup)
        expected[TARGET] = dict(expected[TARGET], links=2)
        expected[BACKUP] = dict(expected[TARGET])
        verify()
        operation('flush-backup-parent', access.LOADER, lambda: device.fsync(access.LOADER))
        operation('replace-loader', TARGET, device.replace_loader)
        expected[TARGET] = expected.pop(NEW)
        expected[BACKUP] = dict(expected[BACKUP], links=1)
        need(device.metadata(NEW) is None, 'Loader rename did not consume candidate')
        verify()
        operation('flush-loader-parent', access.LOADER, lambda: device.fsync(access.LOADER))
        need(device.digest(TARGET) == daily['sha256']
             and device.digest(BACKUP) == files[TARGET]['sha256'], 'Loader or retained setup differs')
        need(device.metadata(STATE_NEW) is None and device.metadata(access.SHARED) is None,
             'Preference path appeared')
        operation('create-state-stage', STATE_NEW, device.create_state_directory)
        value = device.metadata(STATE_NEW)
        need(value is not None and value['mode'] == 0o40700 and value['uid'] == 0
             and value['dev'] == expected['/data/local']['dev'], 'State staging directory differs')
        expected[STATE_NEW] = value
        for path, content in STATE_FILES.items():
            with tempfile.TemporaryFile() as stream:
                stream.write(content); stream.seek(0)
                operation('upload-state', path, lambda p=path: upload(p, stream))
            value = new_file(path, sha(content), len(content))
            metadata(path, dict(value, uid=1000, gid=1000, mode=0o100600, label=STATE_LABEL))
            operation('flush-state', path, lambda p=path: device.fsync(p))
        metadata(STATE_NEW, dict(expected[STATE_NEW], uid=1000, gid=1000,
                                 mode=0o40700, label=STATE_LABEL))
        operation('flush-state-stage', STATE_NEW, lambda: device.fsync(STATE_NEW))
        need(device.metadata(access.SHARED) is None, 'Shared preference appeared')
        operation('commit-hyperos-preference', access.SHARED, device.commit_state_directory)
        for path in (STATE_NEW, *STATE_FILES):
            target = access.SHARED + path[len(STATE_NEW):]
            expected[target] = expected.pop(path)
            device.allowed.add(target)
        operation('flush-state-parent', '/data/local', lambda: device.fsync('/data/local'))
        for name, content in (('state', INITIAL_STATE), ('fault', b'OK\n')):
            need(device.read_state(name) == sha(content), 'Initial HyperOS preference differs')
        verify()
        for path, digest in daily['immutableFiles'].items():
            need(device.digest(path) == digest, 'Final daily code changed')
        record['physicalStockAfter'] = device.physical_stock(daily['normalizedBootargsSha256'])
        need(record['physicalStockAfter']['partitionSha256'] == physical['partitionSha256'],
             'Physical boot environment changed during finalization')
        verify()
        need(device.digest(BACKUP) == files[TARGET]['sha256'], 'Retained setup loader changed')
        for name, content in (('state', INITIAL_STATE), ('fault', b'OK\n')):
            need(device.read_state(name) == sha(content), 'Final preference changed')
        record.update(complete=True, initialDefault='XIAOMI', phase='IDLE', fault='OK',
                      setupLoaderRetained=True, dataMetadataUnchanged=True, dailyBootVerified=False)
        save()
        return record
    except BaseException as exc:
        record.update(complete=False, failure=type(exc).__name__)
        try: save()
        except BaseException: pass
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'finalize'))
    parser.add_argument('--adb', type=Path, required=True)
    parser.add_argument('--device', required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--daily-inputs', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--access-record', type=Path, required=True)
    parser.add_argument('--known-device-binding', required=True)
    parser.add_argument('--known-device-binding-scheme', choices=inventory.BINDING_SCHEMES, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    recovery.private_output(args.output)
    inputs = inventory.read_json(args.inputs)
    plan = inventory.plan(inputs, args.inputs.parent, args.known_device_binding,
                          args.known_device_binding_scheme)
    daily = daily_plan(inputs, args.inputs.parent, plan, inventory.read_json(args.daily_inputs),
                       args.daily_inputs.parent, inventory.read_json(args.profile))
    prepared = inventory.read_json(args.access_record)
    device = Device(args.adb, args.device, plan, access.runtime_pins(inputs, args.inputs.parent))
    os.umask(0o077); args.output.mkdir(mode=0o700)
    result = finalize(device, plan, daily, prepared, args.output, args.action == 'finalize')
    print(json.dumps({k: result[k] for k in ('complete', 'inspectOnly', 'environmentWrites',
                                           'firmwareWrites', 'rebootIssued')}))


if __name__ == '__main__':
    main()
