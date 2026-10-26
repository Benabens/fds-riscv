"""A single-cycle RV32I simulator.

The machine state is exactly what the ISA defines: 32 general-purpose
registers of 32 bits (``x0`` reads as zero and ignores writes), a program
counter, and a flat byte-addressable memory.  RISC-V is little-endian: the
least significant byte of a word lives at the lowest address.

Each :meth:`CPU.step` performs fetch (read the word at ``pc``), decode (split
it into fields with :func:`rvsim.encoding.decode_fields`, i.e. from the real
machine code rather than from the source text), and execute (update
registers, memory and ``pc``).  Registers hold unsigned 32-bit values; the
signed view is used only by the instructions that need it (``slt``, ``blt``,
``sra``, sign-extending loads ...).

Environment calls (``ecall``) follow the RARS/Venus convention, with the
service number in ``a7``: 1 = print integer in ``a0``, 4 = print the
NUL-terminated string at address ``a0``, 10 = exit, 11 = print character,
93 = exit with code ``a0``.
"""

from __future__ import annotations

from typing import Callable

from .assembler import Program, assemble
from .encoding import (
    ABI_NAMES,
    MASK32,
    Decoded,
    EncodingError,
    decode_fields,
    parse_register,
    sign_extend,
)

STACK_TOP = 0x7FFF_FFF0
DEFAULT_MAX_STEPS = 100_000


class SimulationError(Exception):
    """Runtime fault: misaligned access, illegal instruction, bad jump, ..."""


class StepLimitExceeded(SimulationError):
    """The program did not halt within the allowed number of steps."""


def _signed(value: int) -> int:
    return sign_extend(value, 32)


# Register-register semantics on unsigned 32-bit operands.  Shifts use only
# the low five bits of the shift amount, as the ISA specifies.
_ALU: dict[str, Callable[[int, int], int]] = {
    "add": lambda a, b: (a + b) & MASK32,
    "sub": lambda a, b: (a - b) & MASK32,
    "sll": lambda a, b: (a << (b & 31)) & MASK32,
    "slt": lambda a, b: int(_signed(a) < _signed(b)),
    "sltu": lambda a, b: int(a < b),
    "xor": lambda a, b: a ^ b,
    "srl": lambda a, b: a >> (b & 31),
    "sra": lambda a, b: (_signed(a) >> (b & 31)) & MASK32,
    "or": lambda a, b: a | b,
    "and": lambda a, b: a & b,
}

# Immediate forms reuse the register semantics on the sign-extended immediate.
# This is why "sltiu rd, rs, -1" compares against 0xFFFFFFFF.
_IMM_ALU: dict[str, str] = {
    "addi": "add", "slti": "slt", "sltiu": "sltu", "xori": "xor", "ori": "or",
    "andi": "and", "slli": "sll", "srli": "srl", "srai": "sra",
}

_BRANCH: dict[str, Callable[[int, int], bool]] = {
    "beq": lambda a, b: a == b,
    "bne": lambda a, b: a != b,
    "blt": lambda a, b: _signed(a) < _signed(b),
    "bge": lambda a, b: _signed(a) >= _signed(b),
    "bltu": lambda a, b: a < b,
    "bgeu": lambda a, b: a >= b,
}

# mnemonic -> (access size in bytes, sign-extend?)
_LOADS: dict[str, tuple[int, bool]] = {
    "lb": (1, True), "lh": (2, True), "lw": (4, False), "lbu": (1, False), "lhu": (2, False),
}
_STORES: dict[str, int] = {"sb": 1, "sh": 2, "sw": 4}


class Memory:
    """Sparse little-endian byte memory covering the whole 32-bit space.

    Storage is allocated lazily in 4 KiB pages; bytes never written read as 0.
    """

    PAGE_BITS = 12

    def __init__(self) -> None:
        self._pages: dict[int, bytearray] = {}

    def read_byte(self, address: int) -> int:
        page = self._pages.get(address >> self.PAGE_BITS)
        return page[address & 0xFFF] if page is not None else 0

    def write_byte(self, address: int, value: int) -> None:
        index = address >> self.PAGE_BITS
        page = self._pages.get(index)
        if page is None:
            page = self._pages[index] = bytearray(1 << self.PAGE_BITS)
        page[address & 0xFFF] = value & 0xFF

    def read(self, address: int, size: int) -> int:
        """Read ``size`` bytes as an unsigned little-endian integer."""
        return sum(self.read_byte((address + i) & MASK32) << (8 * i) for i in range(size))

    def write(self, address: int, size: int, value: int) -> None:
        """Write the low ``size`` bytes of ``value``, least significant byte first."""
        for i in range(size):
            self.write_byte((address + i) & MASK32, value >> (8 * i))

    def write_bytes(self, address: int, data: bytes) -> None:
        for i, byte in enumerate(data):
            self.write_byte((address + i) & MASK32, byte)

    def read_cstring(self, address: int, limit: int = 1 << 16) -> bytes:
        out = bytearray()
        while len(out) < limit:
            byte = self.read_byte((address + len(out)) & MASK32)
            if byte == 0:
                break
            out.append(byte)
        return bytes(out)


class CPU:
    """RV32I hart loaded with an assembled :class:`Program`.

    Typical use::

        cpu = CPU(assemble(source)).run(max_steps=10_000)
        cpu.reg("a0"), cpu.sreg("a0"), cpu.load_word(cpu.symbol("result"))
    """

    def __init__(self, program: Program) -> None:
        self.program = program
        self.memory = Memory()
        self.memory.write_bytes(program.text_base, program.text)
        self.memory.write_bytes(program.data_base, program.data)
        self.regs: list[int] = [0] * 32
        self.regs[2] = STACK_TOP  # sp
        self.pc: int = program.entry
        self.steps: int = 0
        self.halted: bool = False
        self.exit_code: int | None = None
        self._output: list[str] = []
        self._text_end = program.text_base + len(program.text)
        self._decoded: dict[int, Decoded] = {}  # machine word -> decoded fields

    # ---- inspection API --------------------------------------------------

    @property
    def output(self) -> str:
        """Everything printed through ecall services 1, 4 and 11."""
        return "".join(self._output)

    def reg(self, register: str | int) -> int:
        """Register value as an unsigned 32-bit integer (name or number)."""
        return self.regs[self._index(register)]

    def sreg(self, register: str | int) -> int:
        """Register value interpreted as a signed 32-bit integer."""
        return _signed(self.reg(register))

    def set_reg(self, register: str | int, value: int) -> None:
        index = self._index(register)
        if index:
            self.regs[index] = value & MASK32

    def symbol(self, name: str) -> int:
        """Address of a label (text or data)."""
        try:
            return self.program.symbols[name]
        except KeyError:
            raise KeyError(f"no label named '{name}'") from None

    def load_word(self, address: int) -> int:
        return self._load(address, 4)

    def load_half(self, address: int) -> int:
        return self._load(address, 2)

    def load_byte(self, address: int) -> int:
        return self._load(address, 1)

    def store_word(self, address: int, value: int) -> None:
        self._store(address, 4, value)

    def store_half(self, address: int, value: int) -> None:
        self._store(address, 2, value)

    def store_byte(self, address: int, value: int) -> None:
        self._store(address, 1, value)

    def dump_registers(self) -> str:
        """Human-readable table of all 32 registers and the pc."""
        rows = []
        for i in range(32):
            value = self.regs[i]
            rows.append(f"x{i:<2} {ABI_NAMES[i]:>4} = 0x{value:08x}  {_signed(value):>11}")
        rows.append(f"pc       = 0x{self.pc:08x}")
        return "\n".join(rows)

    # ---- execution -------------------------------------------------------

    def run(self, max_steps: int = DEFAULT_MAX_STEPS) -> CPU:
        """Run until exit ecall, ebreak, or falling off the end of .text.

        Raises :class:`StepLimitExceeded` if ``max_steps`` instructions have
        executed and the program still has not halted.
        """
        while not self.halted:
            if self.pc == self._text_end:
                self.halted = True
                break
            if self.steps >= max_steps:
                raise StepLimitExceeded(
                    f"{self._where(self.pc)}program did not halt within {max_steps} steps"
                )
            self.step()
        return self

    def step(self) -> bool:
        """Execute one instruction; return ``False`` once the program has halted."""
        if self.halted:
            return False
        pc = self.pc
        if pc == self._text_end:
            self.halted = True
            return False
        d = self._fetch(pc)
        x = self.regs
        m = d.mnemonic
        next_pc = (pc + 4) & MASK32

        if m in _ALU:
            self._write(d.rd, _ALU[m](x[d.rs1], x[d.rs2]))
        elif m in _IMM_ALU:
            self._write(d.rd, _ALU[_IMM_ALU[m]](x[d.rs1], d.imm & MASK32))
        elif m in _LOADS:
            size, signed = _LOADS[m]
            value = self._load((x[d.rs1] + d.imm) & MASK32, size, pc)
            if signed:
                value = sign_extend(value, 8 * size) & MASK32
            self._write(d.rd, value)
        elif m in _STORES:
            self._store((x[d.rs1] + d.imm) & MASK32, _STORES[m], x[d.rs2], pc)
        elif m in _BRANCH:
            if _BRANCH[m](x[d.rs1], x[d.rs2]):
                next_pc = (pc + d.imm) & MASK32
        elif m == "jal":
            self._write(d.rd, pc + 4)
            next_pc = (pc + d.imm) & MASK32
        elif m == "jalr":
            target = (x[d.rs1] + d.imm) & MASK32 & ~1  # read rs1 before writing rd
            self._write(d.rd, pc + 4)
            next_pc = target
        elif m == "lui":
            self._write(d.rd, d.imm << 12)
        elif m == "auipc":
            self._write(d.rd, pc + (d.imm << 12))
        elif m == "ecall":
            self._ecall(pc)
        elif m == "ebreak":
            self.halted = True
        # fence: no observable effect on a single in-order hart

        if next_pc % 4:
            raise SimulationError(f"{self._where(pc)}jump target 0x{next_pc:08x} is not 4-byte aligned")
        self.pc = next_pc
        self.steps += 1
        return not self.halted

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _index(register: str | int) -> int:
        if isinstance(register, int):
            if not 0 <= register < 32:
                raise ValueError(f"register number {register} out of range")
            return register
        return parse_register(register)

    def _write(self, rd: int, value: int) -> None:
        if rd:  # x0 is hard-wired to zero
            self.regs[rd] = value & MASK32

    def _where(self, pc: int) -> str:
        line = self.program.line_at(pc)
        return f"line {line} (pc=0x{pc:08x}): " if line is not None else f"pc=0x{pc:08x}: "

    def _fetch(self, pc: int) -> Decoded:
        if pc % 4:
            raise SimulationError(f"{self._where(pc)}misaligned pc")
        if not self.program.text_base <= pc < self._text_end:
            raise SimulationError(f"{self._where(pc)}pc is outside the text section")
        word = self.memory.read(pc, 4)
        decoded = self._decoded.get(word)
        if decoded is None:
            try:
                decoded = self._decoded[word] = decode_fields(word)
            except EncodingError as exc:
                raise SimulationError(f"{self._where(pc)}{exc}") from None
        return decoded

    def _check_alignment(self, address: int, size: int, pc: int | None) -> None:
        if address % size:
            where = self._where(pc) if pc is not None else ""
            kind = {2: "halfword", 4: "word"}[size]
            raise SimulationError(f"{where}misaligned {kind} access at 0x{address:08x}")

    def _load(self, address: int, size: int, pc: int | None = None) -> int:
        address &= MASK32
        self._check_alignment(address, size, pc)
        return self.memory.read(address, size)

    def _store(self, address: int, size: int, value: int, pc: int | None = None) -> None:
        address &= MASK32
        self._check_alignment(address, size, pc)
        self.memory.write(address, size, value)

    def _ecall(self, pc: int) -> None:
        service, a0 = self.regs[17], self.regs[10]
        if service == 1:
            self._output.append(str(_signed(a0)))
        elif service == 4:
            self._output.append(self.memory.read_cstring(a0).decode("utf-8", errors="replace"))
        elif service == 11:
            self._output.append(chr(a0 & 0xFF))
        elif service == 10:
            self.halted, self.exit_code = True, 0
        elif service == 93:
            self.halted, self.exit_code = True, _signed(a0)
        else:
            raise SimulationError(f"{self._where(pc)}unsupported ecall service a7={service}")


def run_source(source: str, max_steps: int = DEFAULT_MAX_STEPS) -> CPU:
    """Assemble ``source``, run it to completion and return the final CPU state."""
    return CPU(assemble(source)).run(max_steps)
