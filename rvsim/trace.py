"""Commit trace: the golden model for hardware co-simulation.

A *commit trace* lists, for every instruction that retires, exactly what it
changed in the architectural state:

    pc  instruction word  register write  memory store

and ends with one line saying why execution stopped.  A processor that
produces the same trace as the simulator, line for line, has executed the
program identically: same control flow, same register values, same memory
writes, in the same order.  Comparing traces (rather than final states) also
pinpoints the very first instruction at which two implementations disagree.

Text format, one record per line (all numbers in lower-case hexadecimal):

    00000010 00a50593 x11=0000000f -
    00000014 00b52023 - M[10000000]=0000000f/4
    00000018 00000073 - -
    HALT exit 0

* field 3 is ``x<rd>=<value>`` when the instruction writes a register other
  than ``x0`` (writes to ``x0`` are discarded and therefore not listed),
  ``-`` otherwise;
* field 4 is ``M[<address>]=<value>/<size>`` for a store, with the value
  truncated to the access size (2, 4 or 8 hex digits), ``-`` otherwise.

The final line is one of ``HALT end-of-text``, ``HALT exit <code>``,
``HALT ebreak``, ``TIMEOUT`` or ``TRAP <kind>`` where ``<kind>`` is the
:attr:`rvsim.cpu.SimulationError.kind` tag of the fault.  A faulting
instruction does not retire and has no record.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .assembler import Program, assemble
from .cpu import CPU, SimulationError
from .encoding import MASK32, OPCODES, EncodingError, decode_fields

DEFAULT_TRACE_STEPS = 1_000_000

_STORE_SIZES = {"sb": 1, "sh": 2, "sw": 4}


@dataclass(frozen=True)
class Commit:
    """Architectural effect of one retired instruction."""

    pc: int
    instr: int
    rd: int | None = None          # destination register, None if nothing is written
    rd_value: int | None = None
    mem_addr: int | None = None    # store address, None if the instruction is not a store
    mem_value: int | None = None   # stored value, truncated to mem_size bytes
    mem_size: int | None = None    # 1, 2 or 4

    def format(self) -> str:
        reg = f"x{self.rd}={self.rd_value:08x}" if self.rd is not None else "-"
        if self.mem_addr is not None:
            mem = f"M[{self.mem_addr:08x}]={self.mem_value:0{2 * self.mem_size}x}/{self.mem_size}"
        else:
            mem = "-"
        return f"{self.pc:08x} {self.instr:08x} {reg} {mem}"


@dataclass
class Trace:
    """Result of :func:`trace_program`."""

    commits: list[Commit] = field(default_factory=list)
    output: str = ""
    end: str = ""

    def lines(self) -> list[str]:
        return [c.format() for c in self.commits] + [self.end]

    def text(self) -> str:
        return "\n".join(self.lines()) + "\n"


def _writes_rd(mnemonic: str) -> bool:
    spec = OPCODES[mnemonic]
    if spec.fmt in ("R", "U", "J"):
        return True
    return spec.fmt == "I" and spec.syntax != "none"  # ALU-immediate, loads, jalr


def trace_program(program: Program, max_steps: int = DEFAULT_TRACE_STEPS) -> Trace:
    """Run ``program`` and record one :class:`Commit` per retired instruction."""
    cpu = CPU(program)
    text_end = program.text_base + len(program.text)
    result = Trace()

    while True:
        if cpu.pc == text_end:
            result.end = "HALT end-of-text"
            break
        if cpu.steps >= max_steps:
            result.end = "TIMEOUT"
            break
        pc = cpu.pc
        word, decoded = 0, None
        if program.text_base <= pc < text_end and pc % 4 == 0:
            word = cpu.memory.read(pc, 4)
            try:
                decoded = decode_fields(word)
            except EncodingError:
                decoded = None
        # A store's address and data are taken from the registers *before* it executes.
        store = None
        if decoded is not None and decoded.mnemonic in _STORE_SIZES:
            size = _STORE_SIZES[decoded.mnemonic]
            address = (cpu.regs[decoded.rs1] + decoded.imm) & MASK32
            store = (address, cpu.regs[decoded.rs2] & ((1 << (8 * size)) - 1), size)
        try:
            cpu.step()
        except SimulationError as exc:  # the faulting instruction does not retire
            result.end = f"TRAP {exc.kind}"
            break
        assert decoded is not None
        rd = decoded.rd if _writes_rd(decoded.mnemonic) and decoded.rd != 0 else None
        commit = Commit(pc, word, rd, cpu.regs[rd] if rd is not None else None)
        if store is not None:
            commit = Commit(pc, word, None, None, *store)
        result.commits.append(commit)
        if cpu.halted:
            result.end = f"HALT exit {cpu.exit_code}" if cpu.exit_code is not None else "HALT ebreak"
            break

    result.output = cpu.output
    return result


def trace_source(source: str, max_steps: int = DEFAULT_TRACE_STEPS) -> Trace:
    """Assemble ``source`` and return its commit trace."""
    return trace_program(assemble(source), max_steps)
