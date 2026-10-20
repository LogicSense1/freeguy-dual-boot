#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Finalization with synthetic files and injected interruptions; no device I/O."""
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


finish = load('finish', ROOT / 'tools/finalize-first-install.py')
fixture = load('access_fixture', ROOT / 'tests/test-first-switch-access.py')
access = finish.access
sha = lambda b: hashlib.sha256(b).hexdigest()


def daily_inputs(root, first_inputs):
    raw = gzip.decompress((root / first_inputs['setupRoot']['path']).read_bytes())
    rows = finish.inventory.runtime.archive.entries(raw)
    changes = finish.inventory.first.scripts()
    for row in rows:
        name = row['name'].removeprefix('first_stage_ramdisk/')
        if name in changes: row['data'] = changes[name][0]
    folder = root / 'daily'
    folder.mkdir()
    archive = gzip.compress(finish.inventory.runtime.components.pack(rows), mtime=0)
    (folder / 'root.gz').write_bytes(archive)
    loader = b'synthetic daily loader; not executable firmware'
    (folder / 'loader').write_bytes(loader)
    manifest = json.loads((root / 'build/manifest.json').read_text())
    audit = json.loads((root / 'build/audit.json').read_text())
    manifest.update(sha256=sha(loader), inputRamdiskSha256=sha(archive),
                    entries=finish.inventory.runtime.archive.table(rows)[2])
    audit.update(sha256=sha(loader), originalEntriesVerified=len(rows))
    for case in audit['cases']:
        if case['case'] == 'not-pid1':
            case.update(end='unprivileged-exit', syscalls=2, environmentWrites=0)
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    (folder / 'audit.json').write_text(json.dumps(audit))
    return {'dailyRoot':dict(path='daily/root.gz',bytes=len(archive),sha256=sha(archive)),
            'dailyLoader':dict(path='daily/loader',bytes=len(loader),sha256=sha(loader))}


class FakeDevice(fixture.FakeDevice):
    def __init__(self, plan):
        super().__init__(plan)
        self.allowed = set()
        self.physical_reads = 0
        self.stock_ok = True
        self.space = 1 << 40

    def physical_stock(self, allowed):
        self.physical_reads += 1
        if not self.stock_ok: raise ValueError('physical environment has temporary entry')
        return dict(bootId='synthetic-boot', persistentReadbackVerified=True,
                    originalBBootImageMatched=True, deviceWritesIssued=False,
                    partitionSha256=sha(b'synthetic unchanged stock environment'))

    def free_bytes(self): return self.space
    def fsync(self, path): self.write(lambda: None)
    def probe(self, digest):
        assert self.digest(finish.NEW) == digest
        self.write(lambda: None)

    def upload(self, path, stream, event):
        assert path not in self.nodes
        event(dict(localAdbPid=100, remotePid=None, finished=False))
        event(dict(localAdbPid=100, remotePid=200, finished=False))
        content = stream.read()
        def create():
            self.nodes[path] = self.node(0o100600, uid=0, size=len(content))
            self.contents[path] = content
        self.write(create)
        event(dict(localAdbPid=100, remotePid=200, finished=True))

    def preserve_setup(self):
        def link():
            self.nodes[finish.TARGET]['links'] = 2
            self.nodes[finish.BACKUP] = copy.deepcopy(self.nodes[finish.TARGET])
            self.contents[finish.BACKUP] = self.contents[finish.TARGET]
        self.write(link)

    def replace_loader(self):
        def replace():
            self.nodes[finish.TARGET] = self.nodes.pop(finish.NEW)
            self.contents[finish.TARGET] = self.contents.pop(finish.NEW)
            self.nodes[finish.BACKUP]['links'] = 1
        self.write(replace)

    def create_state_directory(self):
        self.write(lambda: self.nodes.update({finish.STATE_NEW: self.node(0o40700, uid=0)}))

    def commit_state_directory(self):
        def move():
            for path in (finish.STATE_NEW, *finish.STATE_FILES):
                target = access.SHARED + path[len(finish.STATE_NEW):]
                self.nodes[target] = self.nodes.pop(path)
                if path in self.contents: self.contents[target] = self.contents.pop(path)
        self.write(move)

    def read_state(self, name): return self.digest(access.SHARED + '/' + name)


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = fixture.fixture.fixture(self.root)
        self.plan = finish.inventory.plan(self.inputs, self.root,
                                         fixture.fixture.BINDING, fixture.fixture.SCHEME)
        self.daily_inputs = daily_inputs(self.root, self.inputs)
        files, self.data = access.layout(self.plan)
        hashes = {p:r['sha256'] for p,r in files.items()}
        hashes[finish.TARGET] = self.daily_inputs['dailyLoader']['sha256']
        self.profile = dict(model='L75MA-SPL', device='freeguy', bootloader='01.01.250225',
                            serialUndoVerified=True, normalizedBootargsSha256=['0' * 64],
                            immutableFiles=hashes)
        self.daily = finish.daily_plan(self.inputs,self.root,self.plan,self.daily_inputs,
                                       self.root,self.profile)
        self.device = FakeDevice(self.plan)
        out = self.root / 'access'; out.mkdir()
        self.prepared = access.prepare(self.device,self.plan,out,True)
        self.device.writes = 0
        self.original = copy.deepcopy(self.device.nodes)
        self.contents = copy.deepcopy(self.device.contents)

    def run_finish(self, device, name, apply=True):
        out = self.root / name; out.mkdir()
        return finish.finalize(device,self.plan,self.daily,self.prepared,out,apply)

    def untouched_data(self, device):
        for path in access.PARENTS + tuple(self.data) + ('/unrelated',):
            self.assertEqual(device.nodes[path],self.original[path])
        for path in self.data + ['/unrelated']:
            self.assertEqual(device.contents[path],self.contents[path])

    def test_inspect_does_not_change_files_or_state(self):
        before = copy.deepcopy((self.device.nodes,self.device.contents))
        result = self.run_finish(self.device,'inspect',False)
        self.assertTrue(result['inspectionComplete'])
        self.assertFalse(result['complete'])
        self.assertEqual(self.device.writes,0)
        self.assertEqual((self.device.nodes,self.device.contents),before)
        self.assertEqual(self.device.physical_reads,1)

    def test_complete_retains_setup_and_commits_only_hyperos_default(self):
        result = self.run_finish(self.device,'complete')
        self.assertTrue(result['complete'])
        self.assertEqual((result['initialDefault'],result['phase'],result['fault']),('XIAOMI','IDLE','OK'))
        for key in ('environmentWrites','firmwareWrites','mutableImageWrites','automaticGoogleEnabled',
                    'rebootIssued','appsVerified','dailyBootVerified'):
            self.assertFalse(result[key])
        self.assertEqual(self.device.digest(finish.TARGET),self.daily['sha256'])
        self.assertEqual(self.device.contents[finish.BACKUP],self.contents[finish.TARGET])
        self.assertEqual(self.device.nodes[finish.BACKUP]['ino'],self.original[finish.TARGET]['ino'])
        self.assertEqual(self.device.nodes[finish.BACKUP]['links'],1)
        self.assertEqual(self.device.contents[access.SHARED+'/state'],finish.INITIAL_STATE)
        self.assertEqual(self.device.contents[access.SHARED+'/fault'],b'OK\n')
        self.assertEqual(self.device.nodes[access.SHARED]['label'],finish.STATE_LABEL)
        self.assertEqual(self.device.physical_reads,2)
        self.untouched_data(self.device)

    def test_all_mutation_boundaries_preserve_original_b_path_and_data(self):
        self.run_finish(self.device,'count')
        count = self.device.writes
        self.assertEqual(count,27)
        for point in range(1,count+1):
            for when in ('before','after'):
                device = FakeDevice(self.plan)
                device.nodes = copy.deepcopy(self.original); device.contents = copy.deepcopy(self.contents)
                device.links = dict(access.LINKS)
                device.fault = (point,when)
                name = f'fault-{point}-{when}'
                with self.subTest(point=point,when=when),self.assertRaises(RuntimeError):
                    self.run_finish(device,name)
                self.assertEqual(device.writes,point)
                result = json.loads((self.root/name/'journal.json').read_text())
                self.assertFalse(result['complete'])
                self.assertIsNotNone(result['pending'])
                self.untouched_data(device)
                if finish.BACKUP in device.contents:
                    self.assertEqual(device.contents[finish.BACKUP],self.contents[finish.TARGET])
                else:
                    self.assertEqual(device.contents[finish.TARGET],self.contents[finish.TARGET])
                if access.SHARED+'/state' in device.contents:
                    self.assertEqual(device.contents[access.SHARED+'/state'],finish.INITIAL_STATE)

    def test_original_boot_state_and_space_gate_before_mutation(self):
        self.device.stock_ok = False
        with self.assertRaises(ValueError):self.run_finish(self.device,'stock-not-restored')
        self.assertEqual(self.device.writes,0)
        self.device.stock_ok = True;self.device.space = 0
        with self.assertRaises(ValueError):self.run_finish(self.device,'space')
        self.assertEqual(self.device.writes,0)

    def test_existing_partial_files_refused_without_overwrite(self):
        for i,path in enumerate((finish.NEW,finish.BACKUP,finish.STATE_NEW,access.SHARED)):
            self.device.nodes[path]=self.device.node(0o120777)
            with self.subTest(path=path),self.assertRaises(ValueError):self.run_finish(self.device,f'occupied-{i}')
            self.assertEqual(self.device.writes,0)
            self.device.nodes.pop(path)

    def test_changed_access_record_boot_and_prepared_permissions_refused(self):
        original = copy.deepcopy(self.prepared)
        for i,key in enumerate(('complete','mutableMetadataUnchanged')):
            self.prepared[key]=False
            with self.assertRaises(ValueError):self.run_finish(self.device,f'bad-record-{i}')
            self.prepared=copy.deepcopy(original)
        self.prepared['binding']['bootId']='other-boot'
        with self.assertRaises(ValueError):self.run_finish(self.device,'other-boot')
        self.prepared=original
        self.device.nodes[finish.TARGET]['gid']=2000
        with self.assertRaises(ValueError):self.run_finish(self.device,'changed-mode')
        self.assertEqual(self.device.writes,0)

    def test_profile_or_loader_audit_mismatch_refused(self):
        profile=copy.deepcopy(self.profile);profile['immutableFiles'][finish.TARGET]='0'*64
        with self.assertRaises(ValueError):
            finish.daily_plan(self.inputs,self.root,self.plan,self.daily_inputs,self.root,profile)
        path=self.root/'daily/audit.json';audit=json.loads(path.read_text())
        next(c for c in audit['cases'] if c['case']=='not-pid1')['environmentWrites']=1
        path.write_text(json.dumps(audit))
        with self.assertRaises(ValueError):
            finish.daily_plan(self.inputs,self.root,self.plan,self.daily_inputs,self.root,self.profile)

    def test_unrelated_daily_runtime_difference_refused(self):
        path=self.root/'daily/root.gz';rows=finish.inventory.runtime.archive.entries(gzip.decompress(path.read_bytes()))
        next(r for r in rows if r['name']=='codex/host-adb-key')['data']+=b'changed'
        data=gzip.compress(finish.inventory.runtime.components.pack(rows),mtime=0);path.write_bytes(data)
        inputs=copy.deepcopy(self.daily_inputs);inputs['dailyRoot'].update(bytes=len(data),sha256=sha(data))
        with self.assertRaises(ValueError):
            finish.daily_plan(self.inputs,self.root,self.plan,inputs,self.root,self.profile)

    def test_late_backup_preference_or_environment_change_prevents_completion(self):
        for case in ('backup', 'preference', 'environment'):
            device=FakeDevice(self.plan)
            device.nodes=copy.deepcopy(self.original);device.contents=copy.deepcopy(self.contents)
            device.links=dict(access.LINKS)
            original=device.physical_stock
            def physical(allowed):
                result=original(allowed)
                if device.physical_reads==2:
                    if case=='backup':
                        device.contents[finish.BACKUP]=b'X'*len(device.contents[finish.BACKUP])
                    elif case=='preference':
                        path=access.SHARED+'/state';device.contents[path]=b'X'*len(device.contents[path])
                    else:result['partitionSha256']='0'*64
                return result
            device.physical_stock=physical
            with self.subTest(case=case),self.assertRaises(ValueError):
                self.run_finish(device,'late-'+case)
            record=json.loads((self.root/('late-'+case)/'journal.json').read_text())
            self.assertFalse(record['complete'])

    def test_initial_state_is_the_java_codec_value(self):
        home=os.environ.get('JAVA_HOME')
        if not home:self.skipTest('Set JAVA_HOME to JDK 17 for actual application codec parity')
        java=Path(home)/'bin'
        source=self.root/'StateFixture.java'
        source.write_text('import local.codex.tvswitchprobe.DefaultBootPolicy;'
                          'public class StateFixture {public static void main(String[] args)'
                          'throws Exception {System.out.write(DefaultBootPolicy.xiaomi().encode());}}')
        subprocess.run([str(java/'javac'),'--release','8','-d',str(self.root),
                        str(ROOT/'app/src/local/codex/tvswitchprobe/DefaultBootPolicy.java'),
                        str(source)],check=True,capture_output=True)
        result=subprocess.check_output([str(java/'java'),'-cp',str(self.root),'StateFixture'])
        self.assertEqual(result,finish.INITIAL_STATE)

    def test_journal_failure_at_each_checkpoint_never_retries_mutation(self):
        save=access.stage.save
        calls=[]
        def counting(folder,record):
            calls.append(record['pending']);save(folder,record)
        with patch.object(access.stage,'save',side_effect=counting):
            self.run_finish(self.device,'journal-count')
        for point in range(1,len(calls)+1):
            device=FakeDevice(self.plan)
            device.nodes=copy.deepcopy(self.original);device.contents=copy.deepcopy(self.contents)
            device.links=dict(access.LINKS)
            count=0;stopped=None
            def failure(folder,record):
                nonlocal count,stopped
                count+=1
                if count==point:
                    stopped=device.writes
                    raise OSError('injected journal failure')
                save(folder,record)
            with self.subTest(checkpoint=point),patch.object(access.stage,'save',side_effect=failure):
                with self.assertRaises(OSError):self.run_finish(device,f'journal-{point}')
            self.assertEqual(device.writes,stopped)
            self.untouched_data(device)


if __name__=='__main__':unittest.main()
