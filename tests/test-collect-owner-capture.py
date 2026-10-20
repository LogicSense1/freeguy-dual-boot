#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('collector',ROOT/'tools/collect-owner-capture.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
BOOT='12345678-1234-1234-1234-123456789abc'


class Device:
    def __init__(self):
        self.state=dict(bootId=BOOT,deviceBindingSha256='a'*64,deviceBindingScheme='freeguy-android-serial-v1')
        self.images={name:(name+'\n').encode() for name in m.SIZES}
        self.marker=('FREEGUY_OWNER_CAPTURE_V1\n'+BOOT+'\n'+
                     ''.join(f'{zlib.crc32(data):08x}\n' for data in self.images.values())).encode()
        self.calls=[];self.fail_at=None;self.change_at=None
    def call(self,kind,name=''):
        self.calls.append((kind,name))
        if len(self.calls)==self.fail_at:raise OSError('Interrupted')
        return len(self.calls)==self.change_at
    def identity(self):
        changed=self.call('identity')
        return {**self.state,**({'bootId':'changed'} if changed else {})},None
    def metadata(self,name,directory=False):
        return name+('changed' if self.call('metadata',name) else ':original')
    def read(self,name):
        changed=self.call('read',name)
        data=self.marker if name=='COMPLETE' else self.images[name]
        return data+b'x' if changed else data
    def digest(self,name):
        return 'b'*64 if self.call('digest',name) else m.sha(self.images[name])


class Tests(unittest.TestCase):
    def run_capture(self,device,out):
        with patch.object(m,'SIZES',{n:len(v) for n,v in device.images.items()}), \
             patch.object(m,'validate_images'):
            return m.collect(device,'a'*64,[],out)

    def test_marker_canonical_and_same_boot(self):
        d=Device();self.assertEqual(len(d.marker),98)
        self.assertEqual(set(m.completion(d.marker,BOOT)),set(m.SIZES))
        for bad in [d.marker+b'\n',d.marker.replace(b'V1',b'V2'),d.marker[:-1],b'',b'x'*256]:
            with self.assertRaises(ValueError):m.completion(bad,BOOT)
        with self.assertRaises(ValueError):m.completion(d.marker,'a'*36)

    def test_copy_complete_without_device_writes(self):
        d=Device()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'new';report=self.run_capture(d,out)
            self.assertTrue(report['complete']);self.assertFalse(report['deviceWritesIssued'])
            self.assertFalse(report['filesystemHealthVerified'])
            self.assertFalse(report['installationAuthorized'])
            for name,data in d.images.items():self.assertEqual((out/name).read_bytes(),data)

    def test_all_transfer_and_recheck_failures(self):
        reference=Device()
        with tempfile.TemporaryDirectory() as tmp:self.run_capture(reference,Path(tmp)/'new')
        for i in range(1,len(reference.calls)+1):
            d=Device();d.fail_at=i
            with self.subTest(operation=i),tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)/'new'
                with self.assertRaises(OSError):self.run_capture(d,out)
                if (out/'manifest.json').exists():
                    self.assertFalse(json.loads((out/'manifest.json').read_text())['complete'])

    def test_changes_after_initial_snapshot_refuse(self):
        reference=Device()
        with tempfile.TemporaryDirectory() as tmp:self.run_capture(reference,Path(tmp)/'new')
        # Earlier baseline metadata changing is caught on its subsequent comparison too.
        for i in range(1,len(reference.calls)+1):
            d=Device();d.change_at=i
            with self.subTest(operation=i,call=reference.calls[i-1]),tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):self.run_capture(d,Path(tmp)/'new')

    def test_wrong_owner_and_existing_output(self):
        d=Device();d.state['deviceBindingSha256']='c'*64
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):self.run_capture(d,Path(tmp)/'new')
            self.assertFalse((Path(tmp)/'new').exists())
            out=Path(tmp)/'existing';out.mkdir();(out/'keep').write_bytes(b'untouched')
            with self.assertRaises(FileExistsError):self.run_capture(Device(),out)
            self.assertEqual((out/'keep').read_bytes(),b'untouched')

    def test_invalid_image_validation_leaves_incomplete(self):
        d=Device()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'new'
            with patch.object(m,'SIZES',{n:len(v) for n,v in d.images.items()}), \
                 patch.object(m,'validate_images',side_effect=ValueError('Wrong owner')):
                with self.assertRaises(ValueError):m.collect(d,'a'*64,[],out)
            self.assertFalse(json.loads((out/'manifest.json').read_text())['complete'])


if __name__=='__main__':unittest.main()
