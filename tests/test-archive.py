#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise malformed archives without creating files or invoking a compiler."""
import importlib.util
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('loader_build',
    Path(__file__).resolve().parents[1] / 'tools/build-loader.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def archive(rows):
    out = bytearray()
    for i, (name, mode, data, links) in enumerate([*rows, ('TRAILER!!!', 0, b'', 1)], 1):
        raw = name.encode() + b'\0'
        fields = [i, mode, 0, 0, links, 0, len(data), 0, 0, 0, 0, len(raw), 0]
        out.extend(b'070701' + ''.join('%08x' % f for f in fields).encode())
        out.extend(raw)
        out.extend(b'\0' * (-len(out) % 4))
        out.extend(data)
        out.extend(b'\0' * (-len(out) % 4))
    return bytes(out)


class ArchiveTests(unittest.TestCase):
    def test_valid_metadata_and_data(self):
        raw = archive([('system', 0o40755, b'', 2),
                       ('system/init', 0o100755, b'ELF-placeholder', 1),
                       ('init', 0o120777, b'/system/init', 1)])
        blob, rows, index = build.table(build.entries(raw))
        self.assertEqual(len(rows), 3)
        self.assertEqual(index[0]['name'], 'system')
        self.assertIn(b'ELF-placeholder', blob)

    def test_refuses_paths_that_escape_or_hide_structure(self):
        for name in ['/outside', '../outside', 'a/../outside', 'a//b', '.',
                     'a/./b', 'a\x00b', 'a\nb', 'a b']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                build.entries(archive([(name, 0o100644, b'x', 1)]))

    def test_refuses_duplicate_names_and_special_nodes(self):
        for rows in [[('same', 0o100644, b'a', 1), ('same', 0o100644, b'b', 1)],
                     [('block', 0o60600, b'', 1)], [('hardlink', 0o100644, b'a', 2)],
                     [('directory', 0o40755, b'not-empty', 2)],
                     [('link', 0o120777, b'bad\0target', 1)]]:
            with self.assertRaises(ValueError):
                build.entries(archive(rows))

    def test_never_traverses_a_symlink_parent(self):
        for rows in [[('link', 0o120777, b'/outside', 1), ('link/file', 0o100644, b'x', 1)],
                     [('missing/file', 0o100644, b'x', 1)]]:
            with self.assertRaises(ValueError):
                build.table(build.entries(archive(rows)))

    def test_truncation_and_nonzero_trailing_bytes(self):
        raw = archive([('file', 0o100644, b'content', 1)])
        for length in range(len(raw)):
            with self.assertRaises(ValueError):
                build.entries(raw[:length])
        with self.assertRaises(ValueError):
            build.entries(raw + b'nonzero')


if __name__ == '__main__':
    unittest.main()
