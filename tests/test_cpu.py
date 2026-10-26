"""Simulator tests: ALU semantics, memory, control flow, ecalls, faults.

ALU results are checked against reference functions written in this file
with plain Python integer arithmetic and explicit 32-bit masking.  They use
a different formulation from the simulator (floor division instead of
shifts, ``% 32`` instead of ``& 31``) so that a shared mistake is unlikely.
"""

import random
import textwrap
import unittest

from rvsim import (
    CPU,
    DATA_BASE,
    STACK_TOP,
    SimulationError,
    StepLimitExceeded,
    assemble,
    run_source,
)

M = 0xFFFF_FFFF


def s32(x: int) -> int:
    x &= M
    return x - (1 << 32) if x >= 1 << 31 else x


def run(source: str, max_steps: int = 100_000) -> CPU:
    return run_source(textwrap.dedent(source), max_steps=max_steps)


REFERENCE = {
    "add": lambda a, b: (a + b) % (1 << 32),
    "sub": lambda a, b: (a - b) % (1 << 32),
    "sll": lambda a, b: (a * 2 ** (b % 32)) % (1 << 32),
    "slt": lambda a, b: 1 if s32(a) < s32(b) else 0,
    "sltu": lambda a, b: 1 if a < b else 0,
    "xor": lambda a, b: a ^ b,
    "or": lambda a, b: a | b,
    "and": lambda a, b: a & b,
    "srl": lambda a, b: a // 2 ** (b % 32),
    "sra": lambda a, b: (s32(a) // 2 ** (b % 32)) % (1 << 32),  # floor = arithmetic shift
}
IMMEDIATE_OF = {
    "addi": "add", "slti": "slt", "sltiu": "sltu", "xori": "xor", "ori": "or",
    "andi": "and", "slli": "sll", "srli": "srl", "srai": "sra",
}
EDGES = [0, 1, 2, 31, 32, 33, 0x7FFFFFFF, 0x80000000, 0x80000001, 0xFFFFFFFE, M, 0x12345678, 0xDEADBEEF]


def run_cases(op: str, cases: list[tuple[int, int]], immediate: bool) -> list[int]:
    """Run ``op`` on every (a, b) pair in one program; return the results."""
    lines = [".data", f"out: .space {4 * len(cases)}", ".text", "la s0, out"]
    for a, b in cases:
        lines.append(f"li t0, {a}")
        if immediate:
            lines.append(f"{op} t2, t0, {b}")
        else:
            lines.append(f"li t1, {b}")
            lines.append(f"{op} t2, t0, t1")
        lines += ["sw t2, 0(s0)", "addi s0, s0, 4"]
    cpu = run_source("\n".join(lines), max_steps=10 * len(lines))
    base = cpu.symbol("out")
    return [cpu.load_word(base + 4 * i) for i in range(len(cases))]


class AluTests(unittest.TestCase):
    def test_register_register_ops_random(self):
        rng = random.Random(173)
        for op, ref in REFERENCE.items():
            cases = [(a, b) for a in EDGES for b in EDGES]
            cases += [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(150)]
            for (a, b), got in zip(cases, run_cases(op, cases, immediate=False)):
                with self.subTest(op=op, a=hex(a), b=hex(b)):
                    self.assertEqual(got, ref(a, b))

    def test_register_immediate_ops_random(self):
        rng = random.Random(2024)
        for op, base in IMMEDIATE_OF.items():
            ref = REFERENCE[base]
            if op in ("slli", "srli", "srai"):
                imms = list(range(32))
            else:
                imms = [-2048, -1, 0, 1, 2047] + [rng.randint(-2048, 2047) for _ in range(40)]
            cases = [(a, imm) for a in EDGES for imm in imms[:8]]
            cases += [(rng.getrandbits(32), rng.choice(imms)) for _ in range(150)]
            for (a, imm), got in zip(cases, run_cases(op, cases, immediate=True)):
                with self.subTest(op=op, a=hex(a), imm=imm):
                    # The immediate is sign-extended to 32 bits before use.
                    self.assertEqual(got, ref(a, imm % (1 << 32)))

    def test_hand_checked_shifts_and_compares(self):
        cpu = run("""
            li   t0, 0x80000000
            srai a0, t0, 4          # 0xF8000000: sign bit copied in
            srli a1, t0, 4          # 0x08000000: zeros shifted in
            li   t1, 33
            li   t2, 1
            sll  a2, t2, t1         # only low 5 bits of 33 count -> 1 << 1
            sra  a3, t0, t1         # 0x80000000 >> 1 = 0xC0000000
            li   t3, -1
            slt  a4, t3, t2         # -1 < 1 signed   -> 1
            sltu a5, t3, t2         # 0xFFFFFFFF < 1  -> 0
            sltiu a6, t2, -1        # 1 < 0xFFFFFFFF  -> 1
            slti a7, t3, 0          # -1 < 0          -> 1
        """)
        self.assertEqual(cpu.reg("a0"), 0xF8000000)
        self.assertEqual(cpu.reg("a1"), 0x08000000)
        self.assertEqual(cpu.reg("a2"), 2)
        self.assertEqual(cpu.reg("a3"), 0xC0000000)
        self.assertEqual([cpu.reg(r) for r in ("a4", "a5", "a6", "a7")], [1, 0, 1, 1])

    def test_overflow_wraps(self):
        cpu = run("""
            li  t0, 0x7FFFFFFF
            addi t1, t0, 1
            li  t2, 0
            addi t3, t2, -1
            sub t4, t2, t0
        """)
        self.assertEqual(cpu.reg("t1"), 0x80000000)
        self.assertEqual(cpu.sreg("t1"), -(2 ** 31))
        self.assertEqual(cpu.reg("t3"), 0xFFFFFFFF)
        self.assertEqual(cpu.sreg("t4"), -0x7FFFFFFF)


class LiTests(unittest.TestCase):
    def test_edge_immediates(self):
        values = [0, -1, 0x7FFFFFFF, 0x80000000, 0xFFFFF800, 0x00000800,
                  0xDDDDDDDD, 13623570, -2048, 2047, 2048, -2049, 0xFFFFF7FF, 0x12345FFF]
        source = "\n".join(f"li x{5 + i}, {v}" for i, v in enumerate(values))
        cpu = run_source(source)
        for i, v in enumerate(values):
            with self.subTest(value=v):
                self.assertEqual(cpu.reg(5 + i), v & M)

    def test_pseudo_semantics(self):
        cpu = run("""
            li   t0, 5
            mv   t1, t0
            not  t2, t0
            neg  t3, t0
            seqz t4, zero
            snez t5, t0
            seqz t6, t0
        """)
        self.assertEqual(cpu.reg("t1"), 5)
        self.assertEqual(cpu.reg("t2"), 0xFFFFFFFA)
        self.assertEqual(cpu.sreg("t3"), -5)
        self.assertEqual([cpu.reg("t4"), cpu.reg("t5"), cpu.reg("t6")], [1, 1, 0])


class MemoryTests(unittest.TestCase):
    SOURCE = """
        .data
        w:   .word 0x8001F0FF       # bytes FF F0 01 80
        buf: .space 8
        .text
        la  s0, w
        lb  t0, 0(s0)               # 0xFF -> sign-extended
        lbu t1, 0(s0)               # 0xFF -> zero-extended
        lh  t2, 2(s0)               # 0x8001 -> sign-extended
        lhu t3, 2(s0)               # 0x8001 -> zero-extended
        lw  t4, 0(s0)
        lb  t5, 1(s0)               # 0xF0
        lb  t6, 2(s0)               # 0x01 (positive)
        lh  a3, 0(s0)               # 0xF0FF
        la  s1, buf
        li  a0, 0x11223344
        sw  a0, 0(s1)
        sb  a0, 4(s1)               # stores 0x44 only
        sh  a0, 6(s1)               # stores 0x3344 only
        lbu a1, 0(s1)
        lbu a2, 3(s1)
    """

    def setUp(self):
        self.cpu = run(self.SOURCE)

    def test_sign_vs_zero_extension(self):
        c = self.cpu
        self.assertEqual(c.reg("t0"), 0xFFFFFFFF)
        self.assertEqual(c.sreg("t0"), -1)
        self.assertEqual(c.reg("t1"), 0xFF)
        self.assertEqual(c.reg("t2"), 0xFFFF8001)
        self.assertEqual(c.reg("t3"), 0x8001)
        self.assertEqual(c.reg("t4"), 0x8001F0FF)
        self.assertEqual(c.reg("t5"), 0xFFFFFFF0)
        self.assertEqual(c.reg("t6"), 0x01)
        self.assertEqual(c.reg("a3"), 0xFFFFF0FF)

    def test_little_endian_stores(self):
        c = self.cpu
        buf = c.symbol("buf")
        self.assertEqual(buf, DATA_BASE + 4)
        self.assertEqual([c.load_byte(buf + i) for i in range(8)],
                         [0x44, 0x33, 0x22, 0x11, 0x44, 0x00, 0x44, 0x33])
        self.assertEqual(c.reg("a1"), 0x44)
        self.assertEqual(c.reg("a2"), 0x11)
        self.assertEqual(c.load_word(buf), 0x11223344)
        self.assertEqual(c.load_half(buf + 6), 0x3344)

    def test_data_directives_placement(self):
        cpu = run("""
            .data
            bytes: .byte 1, 2, 3, 0xFF
            words: .word 10, -20, 0xCAFEBABE
            halfs: .half 0x1234, -1
        """)
        b, w, h = cpu.symbol("bytes"), cpu.symbol("words"), cpu.symbol("halfs")
        self.assertEqual(b, DATA_BASE)
        self.assertEqual(cpu.load_word(b), 0xFF030201)
        self.assertEqual(w, DATA_BASE + 4)
        self.assertEqual([cpu.load_word(w + 4 * i) for i in range(3)], [10, (-20) & M, 0xCAFEBABE])
        self.assertEqual(h, DATA_BASE + 16)
        self.assertEqual(cpu.load_half(h), 0x1234)
        self.assertEqual(cpu.load_half(h + 2), 0xFFFF)
        self.assertEqual(cpu.load_byte(h), 0x34)

    def test_misaligned_word_load_reports_line(self):
        source = textwrap.dedent("""
            .data
            w: .word 0
            .text
            la t0, w
            lw t1, 1(t0)
        """)
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "lw" in text)
        with self.assertRaisesRegex(SimulationError, rf"line {line}\b.*misaligned word access"):
            run_source(source)

    def test_misaligned_half_store_and_api(self):
        with self.assertRaisesRegex(SimulationError, "misaligned halfword"):
            run("la t0, x\nsh t0, 1(t0)\n.data\nx: .word 0")
        cpu = run(".data\nx: .word 0x01020304")
        self.assertEqual(cpu.load_byte(cpu.symbol("x") + 1), 0x03)  # bytes are fine anywhere
        with self.assertRaises(SimulationError):
            cpu.load_word(cpu.symbol("x") + 2)


class ControlFlowTests(unittest.TestCase):
    def test_sum_1_to_100(self):
        cpu = run("""
                li   t0, 1          # i
                li   t1, 100        # n
                li   a0, 0          # sum
            loop:
                add  a0, a0, t0
                addi t0, t0, 1
                ble  t0, t1, loop
        """)
        self.assertEqual(cpu.reg("a0"), 5050)
        self.assertEqual(cpu.steps, 3 + 3 * 100)

    def test_recursive_factorial_uses_stack(self):
        # RV32I has no multiply instruction, so mul is a shift-and-add loop.
        cpu = run("""
            main:
                li   a0, 10
                call fact
                mv   s1, a0
                li   a7, 10
                ecall

            # fact(n) = n <= 1 ? 1 : n * fact(n - 1)
            fact:
                addi sp, sp, -8
                sw   ra, 4(sp)
                sw   a0, 0(sp)
                li   t0, 1
                ble  a0, t0, base
                addi a0, a0, -1
                call fact           # a0 = fact(n - 1)
                lw   a1, 0(sp)      # a1 = n
                call mul
                j    done
            base:
                li   a0, 1
            done:
                lw   ra, 4(sp)
                addi sp, sp, 8
                ret

            # mul(a0, a1) -> a0, clobbers t1..t3
            mul:
                li   t1, 0
            mul_loop:
                beqz a1, mul_end
                andi t2, a1, 1
                beqz t2, mul_skip
                add  t1, t1, a0
            mul_skip:
                slli a0, a0, 1
                srli a1, a1, 1
                j    mul_loop
            mul_end:
                mv   a0, t1
                ret
        """)
        self.assertEqual(cpu.reg("s1"), 3628800)
        self.assertEqual(cpu.reg("sp"), STACK_TOP)
        self.assertEqual(cpu.exit_code, 0)
        # The outermost frame saved n = 10 at sp - 8 and the return address at sp - 4.
        self.assertEqual(cpu.load_word(STACK_TOP - 8), 10)
        # 'call fact' is auipc (0x04) + jalr (0x08), so the return address is 0x0C.
        self.assertEqual(cpu.load_word(STACK_TOP - 4), 0x0C)
        # The innermost call (n = 1) is ten frames deep.
        self.assertEqual(cpu.load_word(STACK_TOP - 8 * 10), 1)

    def test_numeric_branch_offsets(self):
        cpu = run("""
            li   t0, 1            # 0x00
            li   t1, 1            # 0x04
            beq  t0, t1, 8        # 0x08 -> 0x10
            li   a0, 99           # 0x0C (skipped)
            li   a1, 7            # 0x10
            li   t2, 5            # 0x14
            li   t3, 0            # 0x18
            addi t3, t3, 3        # 0x1C
            addi t2, t2, -1       # 0x20
            bnez t2, -8           # 0x24 -> 0x1C
        """)
        self.assertEqual(cpu.reg("a0"), 0)
        self.assertEqual(cpu.reg("a1"), 7)
        self.assertEqual(cpu.reg("t3"), 15)

    def test_every_branch_condition(self):
        cases = [  # (mnemonic, a, b, taken)
            ("beq", 5, 5, True), ("beq", 5, 6, False),
            ("bne", 5, 6, True), ("bne", 5, 5, False),
            ("blt", -1, 0, True), ("blt", 0, -1, False),
            ("bge", 0, -1, True), ("bge", -1, 0, False), ("bge", 3, 3, True),
            ("bltu", 0, -1, True), ("bltu", -1, 0, False),
            ("bgeu", -1, 0, True), ("bgeu", 0, -1, False), ("bgeu", 3, 3, True),
        ]
        for op, a, b, taken in cases:
            with self.subTest(op=op, a=a, b=b):
                cpu = run(f"li t0, {a}\nli t1, {b}\nli a0, 0\n{op} t0, t1, 8\nli a0, 1\nnop")
                self.assertEqual(cpu.reg("a0"), 0 if taken else 1)

    def test_jal_jalr_auipc_link_values(self):
        cpu = run("""
            nop                 # 0x00
            jal  ra, 8          # 0x04: ra = 0x08, jump to 0x0C
            li   a0, 1          # 0x08 (skipped)
            auipc t0, 1         # 0x0C: t0 = 0x0C + 0x1000
            la   t1, target     # 0x10
            jalr s0, 0(t1)      # 0x18: s0 = 0x1C
            li   a1, 1          # 0x1C (skipped)
        target:
            la   t1, end        # 0x20
            jalr t1, 0(t1)      # 0x28: rd == rs1, the jump must use the old t1
            li   a2, 1          # 0x2C (skipped)
        end:
            nop                 # 0x30
        """)
        self.assertEqual(cpu.reg("ra"), 0x08)
        self.assertEqual(cpu.reg("a0"), 0)
        self.assertEqual(cpu.reg("t0"), 0x100C)
        self.assertEqual(cpu.reg("s0"), 0x1C)
        self.assertEqual(cpu.reg("a1"), 0)
        self.assertEqual(cpu.reg("t1"), 0x2C)
        self.assertEqual(cpu.reg("a2"), 0)

    def test_x0_stays_zero(self):
        cpu = run("""
            addi x0, x0, 5
            li   zero, 0x12345678
            lui  x0, 0xFFFFF
            jal  zero, 4
            .data
            v: .word 42
            .text
            la   t0, v
            lw   zero, 0(t0)
            add  t1, zero, zero
            addi t2, zero, 0
        """)
        self.assertEqual(cpu.reg("zero"), 0)
        self.assertEqual(cpu.reg("x0"), 0)
        self.assertEqual(cpu.reg("t1"), 0)
        self.assertEqual(cpu.reg("t2"), 0)


class EnvironmentTests(unittest.TestCase):
    def test_print_int_and_string_then_exit(self):
        cpu = run(r"""
            .data
            msg: .asciz "Hello, RISC-V!\n"
            .text
                li   a0, -42
                li   a7, 1
                ecall
                la   a0, msg
                li   a7, 4
                ecall
                li   a0, 'X'
                li   a7, 11
                ecall
                li   a7, 10
                ecall
                li   s0, 1          # never reached
        """)
        self.assertEqual(cpu.output, "-42Hello, RISC-V!\nX")
        self.assertTrue(cpu.halted)
        self.assertEqual(cpu.exit_code, 0)
        self.assertEqual(cpu.reg("s0"), 0)

    def test_exit_with_code(self):
        cpu = run("li a0, 3\nli a7, 93\necall")
        self.assertEqual(cpu.exit_code, 3)

    def test_falls_off_end(self):
        cpu = run("li t0, 3\naddi t0, t0, 1")
        self.assertTrue(cpu.halted)
        self.assertEqual(cpu.steps, 2)
        self.assertIsNone(cpu.exit_code)
        self.assertEqual(cpu.reg("t0"), 4)

    def test_initial_state(self):
        cpu = CPU(assemble("nop"))
        self.assertEqual(cpu.reg("sp"), STACK_TOP)
        self.assertEqual(cpu.pc, 0)
        self.assertTrue(all(cpu.reg(i) == 0 for i in range(32) if i != 2))

    def test_step_limit(self):
        with self.assertRaisesRegex(StepLimitExceeded, "line 1.*50 steps"):
            run("loop: j loop", max_steps=50)

    def test_jump_outside_text(self):
        with self.assertRaisesRegex(SimulationError, "outside the text section"):
            run("li t0, 0x400\njr t0")

    def test_misaligned_jump_target(self):
        with self.assertRaisesRegex(SimulationError, "line 1.*not 4-byte aligned"):
            run("beq zero, zero, 42\nnop")

    def test_unsupported_ecall(self):
        with self.assertRaisesRegex(SimulationError, "a7=99"):
            run("li a7, 99\necall")

    def test_unknown_symbol(self):
        cpu = run("nop")
        with self.assertRaisesRegex(KeyError, "no label"):
            cpu.symbol("missing")


if __name__ == "__main__":
    unittest.main()
