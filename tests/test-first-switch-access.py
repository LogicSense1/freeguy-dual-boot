#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic first-setup access checks; never contacts a television."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


access = load('first_access', ROOT / 'tools/prepare-first-switch-access.py')
fixture = load('inventory_fixture', ROOT / 'tests/test-first-install-plan.py')
sha = lambda b: hashlib.sha256(b).hexdigest()


class FakeDevice:
    def __init__(self, plan, fault=None):
        self.nodes, self.contents, self.links = {}, {}, {}
        self.fault, self.writes, self.guard_calls = fault, 0, 0
        self.reboot_at = None
        self.corrupt_upload = False
        for path in access.PARENTS + access.DIRECTORIES:
            self.nodes[path] = self.node(0o40700, uid=0 if path in access.PARENTS else 2000)
        for row in plan['files']:
            data = Path(row['source']).read_bytes()
            self.contents[row['target']] = data
            self.nodes[row['target']] = self.node(0o100700 if row['target'].endswith('/envdaily')
                                                  else 0o100600, size=len(data))
        self.nodes['/unrelated'] = self.node(0o100600)
        self.contents['/unrelated'] = b'owner data'

    def node(self, mode, uid=2000, gid=None, size=64):
        return dict(dev=1, ino=len(self.nodes) + 10, mode=mode, uid=uid,
                    gid=uid if gid is None else gid, links=1, bytes=size,
                    label='u:object_r:shell_data_file:s0')

    def write(self, action):
        self.writes += 1
        if self.fault == (self.writes, 'before'):
            raise RuntimeError('injected before mutation')
        action()
        if self.fault == (self.writes, 'after'):
            raise RuntimeError('injected after mutation')

    def guard(self):
        self.guard_calls += 1
        if self.reboot_at == self.guard_calls:
            raise ValueError('boot changed')
        return dict(bootId='synthetic-boot', watchdog='100', deviceBinding=fixture.BINDING)

    def shell(self, command, timeout=30):
        if command == 'chcon --help 2>&1': return '-h Change symlink itself'
        if command == 'sync': self.write(lambda: None); return ''
        raise AssertionError(command)

    def metadata(self, path): return copy.deepcopy(self.nodes.get(path))
    def digest(self, path): return sha(self.contents[path])
    def readlink(self, path): return self.links[path]

    def create_proxy(self):
        assert access.PROXY not in self.nodes
        self.write(lambda: self.nodes.update({access.PROXY: self.node(0o40700, uid=0)}))

    def set_metadata(self, path, value):
        assert path not in self.links
        # Cover interruptions within the three-command metadata operation too.
        for keys in (('uid', 'gid'), ('mode',), ('label',)):
            self.write(lambda keys=keys: self.nodes[path].update({k: value[k] for k in keys}))

    def create_link(self, path):
        assert path not in self.nodes
        def create():
            self.nodes[path] = self.node(0o120777, uid=0, size=len(access.LINKS[path]))
            self.links[path] = access.LINKS[path]
        self.write(create)
        self.write(lambda: self.nodes[path].update(uid=0, gid=0))
        self.write(lambda: self.nodes[path].update(label=access.LABEL))

    def upload(self, path, source, event):
        assert path == access.SCRATCH and path not in self.nodes
        event(dict(localAdbPid=100, remotePid=None, finished=False))
        event(dict(localAdbPid=100, remotePid=200, finished=False))
        data = source.read()
        if self.corrupt_upload: data = b'X' * len(data)
        def create():
            self.nodes[path] = self.node(0o100600, uid=0, size=len(data))
            self.contents[path] = data
        self.write(create)
        event(dict(localAdbPid=100, remotePid=200, finished=True))


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = fixture.fixture(self.root)
        self.plan = access.inventory.plan(self.inputs, self.root, fixture.BINDING, fixture.SCHEME)
        self.files, self.data = access.layout(self.plan)

    def run_access(self, device, name, apply=True):
        output = self.root / name
        output.mkdir()
        return access.prepare(device, self.plan, output, apply)

    def test_inspection_has_no_device_writes(self):
        device = FakeDevice(self.plan)
        before = copy.deepcopy((device.nodes, device.contents))
        result = self.run_access(device, 'inspect', False)
        self.assertTrue(result['inspectionComplete'])
        self.assertFalse(result['complete'])
        self.assertEqual(device.writes, 0)
        self.assertEqual((device.nodes, device.contents), before)

    def test_prepares_only_access_and_fixed_proxy_preserves_data(self):
        device = FakeDevice(self.plan)
        before = copy.deepcopy(device.nodes)
        contents = copy.deepcopy(device.contents)
        result = self.run_access(device, 'prepare')
        self.assertTrue(result['complete'])
        self.assertFalse(result['stockServiceAccessVerified'])
        for key in ('sharedStateCreated', 'automaticDefaultEnabled', 'environmentWrites',
                    'firmwareWrites', 'mutableImageWrites', 'rebootIssued'):
            self.assertFalse(result[key])
        self.assertNotIn(access.SHARED, device.nodes)
        self.assertEqual(set(device.nodes) - set(before),
                         {access.PROXY, access.SCRATCH} | set(access.LINKS))
        for path in access.PARENTS + tuple(self.data) + ('/unrelated',):
            self.assertEqual(device.nodes[path], before[path])
        for path, value in contents.items(): self.assertEqual(device.contents[path], value)
        for path in access.DIRECTORIES + tuple(self.files):
            row = device.nodes[path]
            self.assertEqual(row['gid'], 1000)
            self.assertEqual(row['label'], access.LABEL)
            self.assertEqual(row['ino'], before[path]['ino'])
        self.assertEqual(device.links, access.LINKS)
        self.assertEqual(device.contents[access.SCRATCH], access.SCRATCH_BYTES)

    def test_every_primitive_mutation_failure_keeps_journal_and_data(self):
        accepted = FakeDevice(self.plan)
        self.run_access(accepted, 'count')
        self.assertEqual(accepted.writes, 66)
        for point in range(1, accepted.writes + 1):
            for when in ('before', 'after'):
                with self.subTest(point=point, when=when):
                    device = FakeDevice(self.plan, (point, when))
                    original = copy.deepcopy(device.nodes)
                    contents = copy.deepcopy(device.contents)
                    name = f'fault-{point}-{when}'
                    with self.assertRaises(RuntimeError): self.run_access(device, name)
                    record = json.loads((self.root / name / 'journal.json').read_text())
                    self.assertFalse(record['complete'])
                    self.assertIsNotNone(record['pending'])
                    self.assertEqual(device.writes, point)
                    self.assertNotIn(access.SHARED, device.nodes)
                    for path in access.PARENTS + tuple(self.data) + ('/unrelated',):
                        self.assertEqual(device.nodes[path], original[path])
                    for path, value in contents.items(): self.assertEqual(device.contents[path], value)

    def test_occupied_roots_wrong_metadata_or_content_refused_before_write(self):
        mutations = [(access.PROXY, 'mode', 0o40700), (access.SHARED, 'mode', 0o120777),
                     (access.PARENTS[1], 'mode', 0o120777),
                     (access.DIRECTORIES[0], 'gid', 1000),
                     (next(iter(self.files)), 'links', 2),
                     (self.data[0], 'mode', 0o100640), (self.data[1], 'dev', 2),
                     (self.data[2], 'bytes', 0)]
        for i, (path, key, value) in enumerate(mutations):
            device = FakeDevice(self.plan)
            device.nodes.setdefault(path, device.node(0o40700))[key] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.run_access(device, f'refuse-{i}')
            self.assertEqual(device.writes, 0)
        device = FakeDevice(self.plan)
        device.contents[next(iter(self.files))] = b'changed'
        with self.assertRaises(ValueError): self.run_access(device, 'changed-content')
        self.assertEqual(device.writes, 0)

    def test_boot_change_corrupt_scratch_and_final_link_change_fail(self):
        for case in ('boot', 'scratch', 'link', 'shared'):
            device = FakeDevice(self.plan)
            if case == 'boot': device.reboot_at = 5
            elif case == 'scratch': device.corrupt_upload = True
            else:
                original = device.shell
                def shell(command, timeout=30):
                    value = original(command, timeout)
                    if command == 'sync':
                        if case == 'link': device.nodes[next(iter(access.LINKS))]['gid'] = 1000
                        else: device.nodes[access.SHARED] = device.node(0o40700)
                    return value
                device.shell = shell
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.run_access(device, case)
            record = json.loads((self.root / case / 'journal.json').read_text())
            self.assertFalse(record['complete'])

    def test_journal_failure_before_intent_prevents_that_operation(self):
        device = FakeDevice(self.plan)
        save = access.stage.save
        calls = 0
        def failing_save(folder, record):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError('disk full')
            save(folder, record)
        with patch.object(access.stage, 'save', side_effect=failing_save), self.assertRaises(OSError):
            self.run_access(device, 'journal-fail')
        self.assertEqual(device.writes, 0)

    def test_each_journal_failure_stops_without_retries(self):
        save = access.stage.save
        checkpoints = []
        def counting_save(folder, record):
            checkpoints.append(record['pending'])
            save(folder, record)
        with patch.object(access.stage, 'save', side_effect=counting_save):
            self.run_access(FakeDevice(self.plan), 'checkpoint-count')
        self.assertEqual(len(checkpoints), 53)  # Includes all three upload-process records.
        for fail_at in range(1, len(checkpoints) + 1):
            device = FakeDevice(self.plan)
            original = copy.deepcopy(device.nodes)
            calls, writes_at_failure = 0, None
            def failing_save(folder, record):
                nonlocal calls, writes_at_failure
                calls += 1
                if calls == fail_at:
                    writes_at_failure = device.writes
                    raise OSError('injected journal failure')
                save(folder, record)
            with self.subTest(checkpoint=fail_at), patch.object(access.stage, 'save', side_effect=failing_save):
                with self.assertRaises(OSError):
                    self.run_access(device, f'checkpoint-{fail_at}')
            self.assertEqual(device.writes, writes_at_failure)
            for path in self.data: self.assertEqual(device.nodes[path], original[path])
            self.assertNotIn(access.SHARED, device.nodes)

    def test_digest_checks_resolved_host_path_and_rejects_mutable_files(self):
        device = access.Device('unused', 'fixture', self.plan, {})
        device.host, device.files = '/proc/100/root/fileboot-host', self.files
        path = next(iter(self.files))
        digest = self.files[path]['sha256']
        with patch.object(device, 'shell', return_value=digest + '  ' + device.rawpath(path)):
            self.assertEqual(device.digest(path), digest)
        with patch.object(device, 'shell', return_value=digest + '  ' + path), self.assertRaises(ValueError):
            device.digest(path)
        with self.assertRaises(ValueError): device.digest(self.data[0])

    def test_symlink_creation_never_changes_target_permissions(self):
        device = access.Device('unused', 'fixture', self.plan, {})
        device.host = '/proc/100/root/fileboot-host'
        with patch.object(device, 'shell') as shell:
            device.create_link(next(iter(access.LINKS)))
        command = shell.call_args.args[0]
        self.assertIn('chown -h 0:0 ', command)
        self.assertIn('chcon -h ', command)
        self.assertNotIn('chmod', command)
        self.assertNotIn('saveenv', command)

    def test_metadata_refuses_redirected_parent_and_denied_lookup(self):
        device = access.Device('unused', 'fixture', self.plan, {})
        device.host = '/proc/100/root/fileboot-host'
        path = access.PROXY
        with patch.object(device, 'shell', return_value=''), self.assertRaises(ValueError):
            device.metadata(path)
        raw = device.rawpath(path)
        with patch.object(device, 'shell', side_effect=['OK',
                f"stat: '{raw}': Permission denied\nCTV_STAT_RC 1"]), self.assertRaises(ValueError):
            device.metadata(path)
        with patch.object(device, 'shell', side_effect=['OK',
                f"stat: '{raw}': No such file or directory\nCTV_STAT_RC 1"]):
            self.assertIsNone(device.metadata(path))

    def test_guard_binds_both_identity_schemes_and_refuses_daily(self):
        serial, cid = 'synthetic-owner-1234', '1234567890abcdef' * 2
        boot = '11111111-2222-3333-4444-555555555555'
        for scheme in access.inventory.BINDING_SCHEMES:
            plan = copy.deepcopy(self.plan)
            plan['deviceBindingScheme'] = scheme
            material = 'freeguy\0' + cid + '\0' + serial if 'emmc' in scheme else scheme + '\0' + serial
            plan['deviceBinding'] = sha(material.encode())
            pins = dict(helper=sha(b'helper'), **{'entry.sh': sha(b'entry')})
            device = access.Device('unused', 'fixture', plan, pins)
            daily = False
            def shell(command, timeout=30):
                if command.startswith('id -u;'):
                    return '\n'.join(['0', '1', '14', '_b', '01.01.250225', '1', boot, '100'])
                if command == 'cat /dev/codex-first-install-v1': return 'first-install-v1'
                if command.startswith('test ! -e /dev/codex-daily-v1'): return '' if daily else 'OK'
                if command == 'readlink /proc/100/exe': return '/codex/helper'
                if command.startswith('tr '): return '/codex/helper\nwatchdog'
                if command.startswith('sha256sum '):
                    return (pins['helper'] if command.endswith('/exe') else pins['entry.sh']) + '  file'
                if command == 'cat /proc/uptime': return '120.00 80.00'
                if command == 'cat /proc/100/mounts': return '/dev/block/mmcblk0p42 /fileboot-host f2fs rw 0 0'
                if command == 'getprop ro.serialno': return serial
                if command == 'cat /sys/block/mmcblk0/device/cid': return cid
                raise AssertionError(command)
            with patch.object(device, 'shell', side_effect=shell):
                self.assertEqual(device.guard()['deviceBinding'], plan['deviceBinding'])
                self.assertEqual(device.host, '/proc/100/root/fileboot-host')
                daily = True
                with self.assertRaises(ValueError): device.guard()
                daily = False
            overrides = {
                'cat /proc/uptime': '2400.0 90.0',
                'cat /proc/100/mounts': '/dev/block/mmcblk0p41 /fileboot-host f2fs rw 0 0',
                'readlink /proc/100/exe': '/unrelated/helper',
                'sha256sum /proc/100/exe': '0' * 64 + '  file',
                'getprop ro.serialno': 'another-owner-5678',
            }
            for command, wrong in overrides.items():
                def override(cmd, timeout=30):
                    return wrong if cmd == command else shell(cmd, timeout)
                with self.subTest(scheme=scheme, command=command), \
                     patch.object(device, 'shell', side_effect=override), self.assertRaises(ValueError):
                    device.guard()

    def test_runtime_pins_require_the_expected_root(self):
        record = copy.deepcopy(self.inputs)
        record['setupRoot']['sha256'] = '0' * 64
        with self.assertRaises(ValueError): access.runtime_pins(record, self.root)


if __name__ == '__main__':
    unittest.main()
