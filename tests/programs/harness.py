"""
Test harness for the assembly programs in ``programs/``.

Every program exposes one or more *routines* that follow the standard RISC-V
calling convention: arguments in a0-a7, result in a0 (and a1 when 64-bit),
return with ``ret``. This module calls such a routine directly from Python:

    result = call("programs/bits/popcount.s", "popcount", a0=0xF0F0)

so a single program can be checked against hundreds of inputs, instead of only
the demo values hard-coded in its ``_start`` section.

How a call works
----------------
1. The source file is assembled.
2. The program counter is set to the routine's label.
3. ``ra`` is set to the address just past the end of the code: when the routine
   executes ``ret``, it lands exactly there and the simulator stops cleanly.
4. ``sp`` is reset to the top of the stack, arguments are written to a0-a7,
   and any input buffers are copied into a scratch area of data memory.
5. Callee-saved registers (s0-s11, sp) are checked after the call: a routine
   that clobbers them violates the calling convention and the test fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rvsim import CPU, STACK_TOP, assemble

REPO = Path(__file__).resolve().parents[2]

# Scratch memory for buffers passed to routines, well away from the program's
# own .data section (which starts at 0x10000000).
SCRATCH = 0x10100000

CALLEE_SAVED = ["sp"] + [f"s{i}" for i in range(12)]
ARG_REGS = [f"a{i}" for i in range(8)]


def to_u32(value: int) -> int:
    return value & 0xFFFFFFFF


def to_s32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value & 0x80000000 else value


@dataclass
class Buffer:
    """A region of scratch memory handed to a routine (array, string, matrix...)."""

    address: int
    size: int


class Machine:
    """Wraps one assembled program and lets tests call its routines repeatedly."""

    def __init__(self, path: str | Path):
        path = REPO / path
        self.source = path.read_text()
        self.program = assemble(self.source)
        self.cpu = CPU(self.program)
        self._text_end = self.program.text_base + len(self.program.text)
        self._next_scratch = SCRATCH

    # -- memory helpers ---------------------------------------------------
    def words(self, values: list[int]) -> Buffer:
        """Copy 32-bit words (signed or unsigned) into scratch memory."""
        addr = self._alloc(4 * max(len(values), 1), align=4)
        for i, v in enumerate(values):
            self.cpu.store_word(addr + 4 * i, to_u32(v))
        return Buffer(addr, 4 * len(values))

    def bytes_(self, data: bytes) -> Buffer:
        """Copy raw bytes (e.g. a NUL-terminated string) into scratch memory."""
        addr = self._alloc(max(len(data), 1), align=4)
        for i, b in enumerate(data):
            self.cpu.store_byte(addr + i, b)
        return Buffer(addr, len(data))

    def space(self, nbytes: int) -> Buffer:
        """Reserve zeroed scratch memory for a routine's output."""
        addr = self._alloc(max(nbytes, 1), align=4)
        for i in range(nbytes):
            self.cpu.store_byte(addr + i, 0)
        return Buffer(addr, nbytes)

    def read_words(self, buf: Buffer | int, count: int, signed: bool = True) -> list[int]:
        base = buf.address if isinstance(buf, Buffer) else buf
        out = [self.cpu.load_word(base + 4 * i) for i in range(count)]
        return [to_s32(v) for v in out] if signed else out

    def read_bytes(self, buf: Buffer | int, count: int) -> bytes:
        base = buf.address if isinstance(buf, Buffer) else buf
        return bytes(self.cpu.load_byte(base + i) for i in range(count))

    def read_cstring(self, buf: Buffer | int, limit: int = 4096) -> bytes:
        base = buf.address if isinstance(buf, Buffer) else buf
        out = bytearray()
        for i in range(limit):
            b = self.cpu.load_byte(base + i)
            if b == 0:
                break
            out.append(b)
        return bytes(out)

    def _alloc(self, nbytes: int, align: int) -> int:
        addr = (self._next_scratch + align - 1) & ~(align - 1)
        self._next_scratch = addr + nbytes
        return addr

    # -- calling ----------------------------------------------------------
    def call(self, routine: str, *args: int | Buffer, max_steps: int = 2_000_000, **regs: int) -> int:
        """
        Call ``routine`` with positional arguments in a0, a1, ... (a Buffer is
        passed as its address) and optional explicit registers (e.g. a3=5).
        Returns a0 as an unsigned 32-bit value; use ``signed(...)`` / ``reg``.
        """
        cpu = self.cpu
        for i, a in enumerate(args):
            cpu.set_reg(ARG_REGS[i], a.address if isinstance(a, Buffer) else to_u32(a))
        for name, value in regs.items():
            cpu.set_reg(name, to_u32(value))
        cpu.set_reg("sp", STACK_TOP)
        cpu.set_reg("ra", self._text_end)
        # Fill callee-saved registers with recognisable values to detect clobbering.
        saved = {}
        for i in range(12):
            value = 0x5A5A0000 | i
            cpu.set_reg(f"s{i}", value)
            saved[f"s{i}"] = value
        saved["sp"] = STACK_TOP

        cpu.halted = False
        cpu.exit_code = None
        cpu.steps = 0  # the step budget applies to each call, not to the whole test
        cpu.pc = cpu.symbol(routine)
        cpu.run(max_steps=max_steps)

        for name, value in saved.items():
            if cpu.reg(name) != value:
                raise AssertionError(
                    f"{routine} violated the calling convention: {name} = "
                    f"{cpu.reg(name):#010x}, expected {value:#010x}"
                )
        return cpu.reg("a0")

    def reg(self, name: str, signed: bool = False) -> int:
        return self.cpu.sreg(name) if signed else self.cpu.reg(name)


def run_program(path: str | Path, max_steps: int = 2_000_000) -> CPU:
    """Run a program from its ``_start`` label (the demo section) to completion."""
    program = assemble((REPO / path).read_text())
    cpu = CPU(program)
    return cpu.run(max_steps=max_steps)
