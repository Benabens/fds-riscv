"""Tests for programs/bits/xor_swap.s (swap two registers with three XORs)."""

import random
import unittest

from tests.programs.harness import Machine, run_program, to_s32, to_u32

PATH = "programs/bits/xor_swap.s"
MASK = 0xFFFFFFFF


def edge_values():
    values = {0, 1, 2, 3, MASK, MASK - 1, 0x7FFFFFFF, 0x80000000, 0x80000001,
              0x55555555, 0xAAAAAAAA, 0x33333333, 0xCCCCCCCC, 0x0F0F0F0F,
              0xF0F0F0F0, 0x00FF00FF, 0xFF00FF00, 0x0000FFFF, 0xFFFF0000,
              0x12345678, 0xDEADBEEF}
    for k in range(32):
        values |= {1 << k, (1 << k) - 1, MASK ^ (1 << k)}
    return sorted(values)


class XorSwapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = Machine(PATH)

    def check(self, a, b):
        self.m.cpu.steps = 0  # the simulator's step budget is cumulative
        self.m.call("xor_swap", a, b)
        self.assertEqual((self.m.reg("a0"), self.m.reg("a1")), (to_u32(b), to_u32(a)),
                         f"xor_swap({a:#x}, {b:#x})")

    def test_edge_pairs(self):
        small = [0, 1, MASK, 0x7FFFFFFF, 0x80000000, 0x55555555, 0xAAAAAAAA, 0x00010000]
        for a in small:
            for b in small:
                self.check(a, b)

    def test_equal_values(self):
        # Equal VALUES in two distinct registers must survive (only aliasing
        # the same storage location would zero them).
        for x in edge_values():
            self.check(x, x)

    def test_edge_against_random(self):
        rng = random.Random(173)
        for x in edge_values():
            self.check(x, rng.getrandbits(32))

    def test_random_pairs(self):
        rng = random.Random(1173)
        for _ in range(300):
            self.check(rng.getrandbits(32), rng.getrandbits(32))

    def test_signed_view(self):
        self.m.call("xor_swap", 7, -42)
        self.assertEqual((to_s32(self.m.reg("a0")), to_s32(self.m.reg("a1"))), (-42, 7))

    def test_demo_runs(self):
        cpu = run_program(PATH)
        self.assertEqual(cpu.exit_code, 0)
        self.assertEqual(cpu.output.split(), ["-42", "7"])


if __name__ == "__main__":
    unittest.main()
