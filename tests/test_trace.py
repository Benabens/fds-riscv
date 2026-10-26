"""Tests for the hardware-facing additions to rvsim: commit trace and hex images."""

import io
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from rvsim import (
    CPU,
    DATA_BASE,
    STACK_TOP,
    SimulationError,
    assemble,
    hex_words,
    read_meta,
    trace_program,
    trace_source,
    write_build,
)
from rvsim.__main__ import main as cli

REPO = Path(__file__).resolve().parents[1]


def trace(src: str, max_steps: int = 10_000):
    return trace_source(textwrap.dedent(src), max_steps)


class TraceFormatTest(unittest.TestCase):
    def test_exact_lines(self):
        t = trace("""
            li   a0, 5
            addi a1, a0, 10
            add  x0, a0, a1
        """)
        self.assertEqual(t.lines(), [
            "00000000 00500513 x10=00000005 -",
            "00000004 00a50593 x11=0000000f -",
            "00000008 00b50033 - -",          # write to x0 is not a write
            "HALT end-of-text",
        ])

    def test_store_values_are_truncated_to_the_access_size(self):
        t = trace("""
            li  t0, 0x10000000
            li  t1, -2
            sb  t1, 1(t0)
            sh  t1, 2(t0)
            sw  t1, 4(t0)
        """)
        stores = [c.format().split()[3] for c in t.commits[-3:]]
        self.assertEqual(stores, ["M[10000001]=fe/1", "M[10000002]=fffe/2", "M[10000004]=fffffffe/4"])

    def test_store_reports_old_register_values(self):
        # sw t0, 0(t0): address and data both come from t0 before the store.
        t = trace("""
            li  t0, 0x10000010
            sw  t0, 0(t0)
        """)
        self.assertEqual(t.commits[-1].format().split()[3], "M[10000010]=10000010/4")

    def test_jumps_record_the_link_value(self):
        t = trace("""
            jal  ra, next
        next:
            jalr x0, 8(ra)
            nop
        """)
        self.assertEqual(t.commits[0].format(), "00000000 004000ef x1=00000004 -")
        self.assertEqual(t.commits[1].rd, None)

    def test_loads_record_the_extended_value(self):
        t = trace("""
            .data
        v:  .byte 0x80
            .text
            la  t0, v
            lb  t1, 0(t0)
            lbu t2, 0(t0)
        """)
        self.assertEqual(t.commits[-2].rd_value, 0xFFFFFF80)
        self.assertEqual(t.commits[-1].rd_value, 0x80)


class TraceEndTest(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(trace("li a7, 10\necall").end, "HALT exit 0")
        self.assertEqual(trace("li a0, -3\nli a7, 93\necall").end, "HALT exit -3")

    def test_exit_ecall_is_retired(self):
        t = trace("li a7, 10\necall")
        self.assertEqual(t.commits[-1].instr, 0x73)

    def test_ebreak(self):
        t = trace("nop\nebreak\nnop")
        self.assertEqual((len(t.commits), t.end), (2, "HALT ebreak"))

    def test_timeout(self):
        t = trace("spin: j spin", max_steps=50)
        self.assertEqual((len(t.commits), t.end), (50, "TIMEOUT"))

    def test_trap_kinds_and_faulting_instruction_not_retired(self):
        # source -> (final line, instructions retired before the fault)
        cases = {
            "li t0, 0x10000002\nlw t1, 0(t0)": ("TRAP misaligned-access", 2),
            "li t0, 0x10000001\nsh t1, 0(t0)": ("TRAP misaligned-access", 2),
            "beq x0, x0, 6": ("TRAP misaligned-jump", 0),
            ".word 0xffffffff": ("TRAP illegal-instruction", 0),
            "li a7, 77\necall": ("TRAP ecall-service", 1),
            "li t0, 0x2000\njr t0": ("TRAP fetch", 2),
        }
        for src, (end, retired) in cases.items():
            with self.subTest(src=src):
                t = trace(src)
                self.assertEqual((t.end, len(t.commits)), (end, retired))

    def test_output_is_captured(self):
        t = trace("li a0, 42\nli a7, 1\necall\nli a0, 33\nli a7, 11\necall")
        self.assertEqual(t.output, "42!")


class TraceAgainstSimulatorTest(unittest.TestCase):
    """Replaying the trace's writes must reproduce the simulator's final state."""

    def test_every_program(self):
        for path in sorted((REPO / "programs").glob("*/*.s")):
            with self.subTest(program=path.name):
                program = assemble(path.read_text(encoding="utf-8"))
                t = trace_program(program)
                cpu = CPU(program).run(1_000_000)
                self.assertTrue(t.end.startswith("HALT"))
                self.assertEqual(len(t.commits), cpu.steps)
                self.assertEqual(t.output, cpu.output)
                regs = [0] * 32
                regs[2] = STACK_TOP
                memory = {}
                for c in t.commits:
                    if c.rd is not None:
                        regs[c.rd] = c.rd_value
                    if c.mem_addr is not None:
                        for i in range(c.mem_size):
                            memory[c.mem_addr + i] = (c.mem_value >> (8 * i)) & 0xFF
                self.assertEqual(regs, cpu.regs)
                for address, byte in memory.items():
                    self.assertEqual(cpu.memory.read_byte(address), byte)


class HexImageTest(unittest.TestCase):
    def test_little_endian_words_and_padding(self):
        self.assertEqual(hex_words(b"\x01\x02\x03\x04\x05"), ["04030201", "00000005"])
        self.assertEqual(hex_words(b""), ["00000000"])

    def test_text_hex_matches_machine_words(self):
        program = assemble("_start: li a0, 1\nadd a1, a0, a0\n.data\nx: .byte 7, 8")
        with tempfile.TemporaryDirectory() as tmp:
            meta = write_build(program, tmp)
            text = Path(tmp, "text.hex").read_text().split()
            data = Path(tmp, "data.hex").read_text().split()
            self.assertEqual([int(w, 16) for w in text], program.words)
            self.assertEqual(data, ["00000807"])
            self.assertEqual(read_meta(Path(tmp, "meta.txt")), meta)
        self.assertEqual(meta["entry"], 0)
        self.assertEqual(meta["text_end"], 8)
        self.assertEqual(meta["data_base"], DATA_BASE)
        self.assertEqual((meta["text_words"], meta["data_words"], meta["data_size"]), (2, 1, 2))


class CommandLineTest(unittest.TestCase):
    def test_build_and_trace_commands(self):
        src = REPO / "programs" / "memory" / "memcpy.s"
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli(["build", str(src), "-o", tmp]), 0)
            for name in ("text.hex", "data.hex", "meta.txt"):
                self.assertTrue(Path(tmp, name).is_file())
            trace_file, output_file = Path(tmp, "t.txt"), Path(tmp, "o.txt")
            with redirect_stderr(io.StringIO()):
                rc = cli(["trace", str(src), "-o", str(trace_file), "--output", str(output_file)])
            self.assertEqual(rc, 0)
            expected = trace_program(assemble(src.read_text()))
            self.assertEqual(trace_file.read_text(), expected.text())
            self.assertEqual(output_file.read_text(), expected.output)


class SimulationErrorKindTest(unittest.TestCase):
    def test_default_kind_keeps_old_constructor_working(self):
        self.assertEqual(SimulationError("boom").kind, "error")
        self.assertEqual(str(SimulationError("boom")), "boom")


if __name__ == "__main__":
    unittest.main()
