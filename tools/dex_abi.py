# SPDX-License-Identifier: Apache-2.0
"""Read DEX type/method declarations, never method instructions or decompiled bodies.

Format reference: https://source.android.com/docs/core/runtime/dex-format
Only standalone little-endian DEX versions 035 through 040 are accepted.
"""
import hashlib
import struct
import zlib


class Dex:
    def __init__(self, raw):
        self.raw = raw
        if not 112 <= len(raw) <= 64 * 1024 * 1024:
            raise ValueError('DEX size out of bounds')
        if raw[:4] != b'dex\n' or raw[4:7] not in (b'035', b'037', b'038', b'039', b'040') or raw[7] != 0:
            raise ValueError('Unsupported DEX format')
        if self.u32(32) != len(raw) or self.u32(36) != 112 or self.u32(40) != 0x12345678:
            raise ValueError('Invalid DEX header')
        if raw[12:32] != hashlib.sha1(raw[32:]).digest() or self.u32(8) != zlib.adler32(raw[12:]):
            raise ValueError('DEX checksum mismatch')
        self.tables = {}
        for name, header, width in (('strings', 56, 4), ('types', 64, 4), ('protos', 72, 12),
                                     ('fields', 80, 8), ('methods', 88, 8), ('classes', 96, 32)):
            count, offset = self.u32(header), self.u32(header + 4)
            if count > 1000000 or (count and (offset < 112 or offset % 4)):
                raise ValueError('Invalid DEX table')
            self.check(offset, count * width)
            self.tables[name] = count, offset, width

    def check(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.raw):
            raise ValueError('DEX read outside file')

    def u32(self, offset):
        self.check(offset, 4)
        return struct.unpack_from('<I', self.raw, offset)[0]

    def u16(self, offset):
        self.check(offset, 2)
        return struct.unpack_from('<H', self.raw, offset)[0]

    def item(self, name, index):
        count, offset, width = self.tables[name]
        if not 0 <= index < count:
            raise ValueError('DEX index outside table')
        return offset + index * width

    def uleb(self, offset):
        value = 0
        for n in range(5):
            self.check(offset, 1)
            byte = self.raw[offset]
            offset += 1
            if n == 4 and byte & 0xf0:
                raise ValueError('ULEB128 overflow')
            value |= (byte & 127) << (n * 7)
            if not byte & 128:
                return value, offset
        raise ValueError('Unterminated ULEB128')

    def string(self, index):
        offset = self.u32(self.item('strings', index))
        size, offset = self.uleb(offset)
        if size > 65536:
            raise ValueError('Excessive DEX string')
        end = self.raw.find(b'\0', offset, min(len(self.raw), offset + size * 3 + 1))
        if end < 0:
            raise ValueError('Unterminated DEX string')
        # Keep MUTF-8 bytes opaque. Only selected ASCII ABI names are decoded.
        return self.raw[offset:end]

    def type(self, index):
        return self.string(self.u32(self.item('types', index)))

    def method(self, index):
        offset = self.item('methods', index)
        owner = self.type(self.u16(offset))
        proto = self.item('protos', self.u16(offset + 2))
        result = self.type(self.u32(proto + 4))
        parameters = self.u32(proto + 8)
        arguments = []
        if parameters:
            count = self.u32(parameters)
            if count > 255:
                raise ValueError('Excessive parameter count')
            self.check(parameters + 4, count * 2)
            arguments = [self.type(self.u16(parameters + 4 + i * 2)) for i in range(count)]
        return owner, self.string(self.u32(offset + 4)), result, tuple(arguments)

    def class_methods(self, descriptor):
        matches = []
        for n in range(self.tables['classes'][0]):
            offset = self.item('classes', n)
            if self.type(self.u32(offset)) == descriptor:
                matches.append(offset)
        if len(matches) != 1:
            raise ValueError('Required class missing or duplicated')
        offset = matches[0]
        flags = self.u32(offset + 4)
        if not flags & 1 or flags & 0x200 or self.type(self.u32(offset + 8)) != b'Ljava/lang/Object;':
            raise ValueError('Unexpected required class shape')
        position = self.u32(offset + 24)
        if not position:
            raise ValueError('Required class has no declarations')
        counts = []
        for _ in range(4):
            count, position = self.uleb(position)
            if count > 10000:
                raise ValueError('Excessive class members')
            counts.append(count)
        for count in counts[:2]:
            index = 0
            for n in range(count):
                delta, position = self.uleb(position)
                if n and not delta:
                    raise ValueError('Duplicate encoded field')
                index += delta
                self.item('fields', index)
                _, position = self.uleb(position)
        result = {}
        for count in counts[2:]:
            index = 0
            for n in range(count):
                delta, position = self.uleb(position)
                if n and not delta:
                    raise ValueError('Duplicate encoded method')
                index += delta
                access, position = self.uleb(position)
                code_offset, position = self.uleb(position)
                if code_offset:
                    self.check(code_offset, 16)  # Never read method instructions.
                owner, name, returns, arguments = self.method(index)
                if owner != descriptor:
                    raise ValueError('Encoded method has wrong owner')
                signature = name, returns, arguments
                if signature in result:
                    raise ValueError('Duplicate method declaration')
                result[signature] = access
        return flags, result
