"""Tests for programs/memory/memcpy.s (aligned fast path and byte path)."""

import random
import unittest

from tests.programs.harness import Machine, run_program, to_s32, to_u32

PATH = "programs/memory/memcpy.s"
GUARD = b"\xA5\x5A\xC3\x3C"
SENTINEL = 0xEE


class MemcpyTest(unittest.TestCase):
    def setUp(self):
        self.m = Machine(PATH)

    def check(self, data, src_off=0, dst_off=0):
        """Copy `data` from src+src_off to dst+dst_off; check bytes around dst."""
        n = len(data)
        src = self.m.bytes_(bytes(src_off) + data + GUARD)
        dst = self.m.bytes_(bytes([SENTINEL]) * (dst_off + n) + GUARD)
        d = dst.address + dst_off
        ret = self.m.call("memcpy", d, src.address + src_off, n)
        self.assertEqual(ret, d, "memcpy must return dst")
        self.assertEqual(self.m.read_bytes(d, n), data, (n, src_off, dst_off))
        # nothing before dst and nothing after dst + n is modified
        self.assertEqual(self.m.read_bytes(dst, dst_off), bytes([SENTINEL]) * dst_off)
        self.assertEqual(self.m.read_bytes(d + n, 4), GUARD)
        # the source is only read
        self.assertEqual(self.m.read_bytes(src.address + src_off, n), data)

    def test_zero_length(self):
        self.check(b"")
        self.check(b"", 1, 3)

    def test_single_byte(self):
        self.check(b"\x7f")
        self.check(b"\xff", 3, 1)

    def test_aligned_sizes(self):
        for n in range(0, 18):
            self.check(bytes(range(1, n + 1)))

    def test_misaligned(self):
        data = bytes(range(100, 123))
        for so in range(4):
            for do in range(4):
                self.check(data, so, do)

    def test_string_with_nul(self):
        self.check(b"Hello, RV32I!\x00")

    def test_randomized(self):
        rng = random.Random(4096)
        for _ in range(50):
            n = rng.randint(0, 64)
            data = bytes(rng.randrange(256) for _ in range(n))
            self.check(data, rng.randrange(4), rng.randrange(4))

    def test_demo_program_terminates(self):
        cpu = run_program(PATH)
        self.assertTrue(cpu.halted)
        self.assertEqual(cpu.output, "Hello, RV32I!")


if __name__ == "__main__":
    unittest.main()
