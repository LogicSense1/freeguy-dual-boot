# SPDX-License-Identifier: Apache-2.0
"""Read Linux 5.4 EROFS metadata; compressed contents use fsck/dump.erofs.

Layout source: Linux v5.4 fs/erofs/erofs_fs.h (format declarations only). Reject new
incompatible features rather than interpret them as an older format.
"""
import mmap
import stat
import struct

def need(condition, message):
    if not condition:
        raise ValueError(message)

class ErofsMetadata:

    def __init__(self, path):
        self.file = open(path, 'rb')
        self.data = None
        try:
            self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
            s = 1024
            need(len(self.data) >= s + 84, 'Truncated EROFS header')
            need(self.u32(s) == 0xe0f5e1e2, 'Unsupported or malformed EROFS metadata')
            self.block = 1 << self.data[s + 12]
            need(self.block == 4096, 'Unsupported or malformed EROFS metadata')
            self.root = self.u16(s + 14)
            self.time = self.u64(s + 24)
            self.meta = self.u32(s + 40) * self.block
            self.shared = self.u32(s + 44) * self.block
            self.features = self.u32(s + 80)
            need(self.features & ~1 == 0, ('Unsupported features', hex(self.features)))
        except Exception:
            self.close()
            raise

    def close(self):
        if self.data is not None:
            self.data.close()
        self.file.close()

    def u16(self, at):
        return struct.unpack_from('<H', self.data, at)[0]

    def u32(self, at):
        return struct.unpack_from('<I', self.data, at)[0]

    def u64(self, at):
        return struct.unpack_from('<Q', self.data, at)[0]

    def xattr_entry(self, at):
        names = {1: 'user.', 2: 'system.posix_acl_access', 3: 'system.posix_acl_default', 4: 'trusted.', 5: 'lustre.', 6: 'security.'}
        (length, index, vlen) = struct.unpack_from('<BBH', self.data, at)
        need(index in names, 'Unsupported or malformed EROFS metadata')
        name = names[index] + self.data[at + 4:at + 4 + length].decode()
        value = self.data[at + 4 + length:at + 4 + length + vlen]
        return (name, value, 4 + length + vlen + 3 & ~3)

    def inode(self, nid):
        at = self.meta + nid * 32
        (form, icount, mode) = struct.unpack_from('<HHH', self.data, at)
        extended = form & 1
        isize = 64 if extended else 32
        xsize = 12 + (icount - 1) * 4 if icount else 0
        d = {'nid': nid, 'at': at, 'mode': mode, 'layout': form >> 1 & 7, 'size': self.u64(at + 8) if extended else self.u32(at + 8), 'uid': self.u32(at + 24) if extended else self.u16(at + 24), 'gid': self.u32(at + 28) if extended else self.u16(at + 26), 'mtime': self.u64(at + 32) if extended else self.time, 'raw': self.u32(at + 16) * self.block, 'inline': at + isize + xsize, 'xattrs': {}}
        if icount:
            start = at + isize
            count = self.data[start + 4]
            need(count <= 128, 'Unsupported or malformed EROFS metadata')
            for i in range(count):
                (key, value, _) = self.xattr_entry(self.shared + self.u32(start + 12 + i * 4) * 4)
                d['xattrs'][key] = value.hex()
            (cur, end) = (start + 12 + count * 4, start + xsize)
            while cur < end:
                (key, value, length) = self.xattr_entry(cur)
                d['xattrs'][key] = value.hex()
                cur += length
            need(cur == end, 'Unsupported or malformed EROFS metadata')
        return d

    def flat_data(self, n):
        need(n['layout'] in (0, 2), n)
        tail = n['size'] % self.block if n['layout'] == 2 else 0
        count = n['size'] - tail
        data = self.data[n['raw']:n['raw'] + count]
        if tail:
            data += self.data[n['inline']:n['inline'] + tail]
        need(len(data) == n['size'], 'Unsupported or malformed EROFS metadata')
        return data

    def inventory(self):
        result = {}

        def visit(path, nid, ancestors=()):
            need(path not in result and len(result) < 50000, "Repeated or oversized filesystem tree")
            need(nid not in ancestors and len(ancestors) < 128, "Directory cycle or excessive depth")
            n = self.inode(nid)
            result[path] = n
            if stat.S_ISLNK(n['mode']):
                n['link'] = self.flat_data(n).decode()
            if not stat.S_ISDIR(n['mode']):
                return
            data = self.flat_data(n)
            for off in range(0, len(data), self.block):
                block = data[off:off + self.block]
                first = struct.unpack_from('<H', block, 8)[0]
                need(first % 12 == 0, 'Unsupported or malformed EROFS metadata')
                entries = [struct.unpack_from('<QHBB', block, i) for i in range(0, first, 12)]
                for (i, (child, start, typ, reserved)) in enumerate(entries):
                    end = entries[i + 1][1] if i + 1 < len(entries) else len(block)
                    name = block[start:end].split(b'\x00')[0].decode()
                    if name in ('.', '..'):
                        continue
                    need(name and '/' not in name and (reserved == 0), 'Unsupported or malformed EROFS metadata')
                    visit(path.rstrip('/') + '/' + name, child, ancestors + (nid,))
        visit('/', self.root)
        return result
