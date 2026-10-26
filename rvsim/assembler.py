"""Two-pass assembler for RV32I assembly source.

Pass 1 *lays out* the program: each statement is given a section and an
address, and every label is bound to the address of the statement that
follows it.  Nothing can be encoded yet because an instruction may refer to
a label defined further down (a forward reference).

Pass 2 walks the laid-out statements again with a complete symbol table,
expands pseudo-instructions into base instructions and encodes them.

This only works if the *size* of every statement is known in pass 1 without
knowing any label value.  That holds here: pseudo-instructions that take a
label (``la``, ``call``) always expand to a fixed ``auipc`` pair, and ``li``
only takes numeric literals, so its expansion length is decided directly.

Memory map: ``.text`` starts at 0x00000000 and ``.data`` at 0x10000000.
Execution starts at the label ``_start`` if it exists, else at the first
instruction.  ``.word`` and ``.half`` are aligned to their natural size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .encoding import (
    MASK32,
    EncodingError,
    OPCODES,
    encode_fields,
    is_integer_literal,
    operands_to_fields,
    parse_immediate,
    parse_register,
    sign_extend,
    to_signed32,
    unescape,
)

TEXT_BASE = 0x0000_0000
DATA_BASE = 0x1000_0000

_BASES = {"text": TEXT_BASE, "data": DATA_BASE}
_LABEL_RE = re.compile(r"\s*([A-Za-z_.$][\w.$]*)\s*:")
_SYMBOL_RE = re.compile(r"^[A-Za-z_.$][\w.$]*$")

Expansion = list[tuple[str, dict[str, int]]]


class AssemblerError(Exception):
    """Syntax or semantic error in the source, tagged with its line number."""

    def __init__(self, message: str, line: int | None = None) -> None:
        self.message = message
        self.line = line
        super().__init__(f"line {line}: {message}" if line is not None else message)


@dataclass
class Program:
    """The output of :func:`assemble`: section images plus metadata."""

    text: bytes
    data: bytes
    symbols: dict[str, int]
    line_map: dict[int, int] = field(default_factory=dict)  # text address -> source line
    entry: int = TEXT_BASE
    text_base: int = TEXT_BASE
    data_base: int = DATA_BASE

    def line_at(self, address: int) -> int | None:
        """Source line of the instruction stored at ``address`` (if any)."""
        return self.line_map.get(address)

    @property
    def words(self) -> list[int]:
        """The text section as a list of 32-bit machine words."""
        return [int.from_bytes(self.text[i:i + 4], "little") for i in range(0, len(self.text), 4)]


@dataclass
class _Statement:
    line: int
    labels: list[str]
    name: str  # lower-case mnemonic or directive; "" for a label-only line
    operands: list[str]


@dataclass
class _Placed:
    stmt: _Statement
    section: str
    address: int
    size: int


# --------------------------------------------------------------------------
# Lexing
# --------------------------------------------------------------------------

def _scan(line: str, stop: str) -> list[int]:
    """Indices of ``stop`` characters that are outside string/char literals."""
    hits: list[int] = []
    quote: str | None = None
    escaped = False
    for i, ch in enumerate(line):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in stop:
            hits.append(i)
    return hits


def _strip_comment(line: str) -> str:
    hits = _scan(line, "#")
    return line[:hits[0]] if hits else line


def _split_operands(text: str) -> list[str]:
    if not text.strip():
        return []
    cuts = _scan(text, ",")
    pieces, start = [], 0
    for cut in cuts + [len(text)]:
        pieces.append(text[start:cut].strip())
        start = cut + 1
    if any(not p for p in pieces):
        raise EncodingError("empty operand")
    return pieces


def _parse(source: str) -> list[_Statement]:
    statements = []
    for number, raw in enumerate(source.splitlines(), start=1):
        try:
            rest = _strip_comment(raw)
            labels = []
            while (m := _LABEL_RE.match(rest)):
                labels.append(m.group(1))
                rest = rest[m.end():]
            rest = rest.strip()
            name, operands = "", []
            if rest:
                parts = rest.split(None, 1)
                name = parts[0].lower()
                operands = _split_operands(parts[1]) if len(parts) > 1 else []
        except EncodingError as exc:
            raise AssemblerError(str(exc), number) from None
        if labels or name:
            statements.append(_Statement(number, labels, name, operands))
    return statements


# --------------------------------------------------------------------------
# Helpers for immediates
# --------------------------------------------------------------------------

def split_hi_lo(value: int) -> tuple[int, int]:
    """Split a 32-bit value into a 20-bit upper part and signed 12-bit lower part.

    The pair satisfies ``(hi << 12) + lo == value (mod 2**32)``.  Because
    ``addi``/``jalr`` sign-extend their 12-bit immediate, a lower part with bit
    11 set is really negative; adding 0x800 before taking the upper bits
    pre-compensates for that borrow.
    """
    value &= MASK32
    return ((value + 0x800) >> 12) & 0xFFFFF, sign_extend(value, 12)


def load_immediate(rd: int, value: int) -> Expansion:
    """Expansion of ``li rd, value``: one ``addi`` if it fits 12 bits, else ``lui`` (+ ``addi``)."""
    if not -(1 << 31) <= value <= MASK32:
        raise EncodingError(f"li immediate {value} does not fit in 32 bits")
    signed = to_signed32(value)
    if -2048 <= signed <= 2047:
        return [("addi", {"rd": rd, "rs1": 0, "imm": signed})]
    hi, lo = split_hi_lo(value)
    seq: Expansion = [("lui", {"rd": rd, "imm": hi})]
    if lo:
        seq.append(("addi", {"rd": rd, "rs1": rd, "imm": lo}))
    return seq


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


# --------------------------------------------------------------------------
# The assembler
# --------------------------------------------------------------------------

class _Assembler:
    def __init__(self, source: str) -> None:
        self.statements = _parse(source)
        self.symbols: dict[str, int] = {}

    # ---- symbol handling -------------------------------------------------

    def _define(self, label: str, address: int, line: int) -> None:
        if label in self.symbols:
            raise AssemblerError(f"duplicate label '{label}'", line)
        self.symbols[label] = address

    def _address_of(self, token: str, resolve: bool) -> int:
        """Absolute address denoted by a label or a numeric literal."""
        if is_integer_literal(token):
            return parse_immediate(token)
        if not _SYMBOL_RE.match(token):
            raise EncodingError(f"invalid symbol '{token}'")
        if not resolve:
            return 0
        if token not in self.symbols:
            raise EncodingError(f"undefined label '{token}'")
        return self.symbols[token]

    def _offset_to(self, token: str, pc: int, resolve: bool) -> int:
        """PC-relative offset for a branch/jump operand.

        A numeric operand *is* the offset (``beq t0, t1, 42`` jumps 42 bytes
        forward); a label is converted to ``label - pc``.
        """
        if is_integer_literal(token):
            return parse_immediate(token)
        address = self._address_of(token, resolve)
        return address - pc if resolve else 0

    # ---- pass 1 ----------------------------------------------------------

    def _layout(self) -> tuple[list[_Placed], dict[str, int]]:
        loc = {"text": 0, "data": 0}
        section = "text"
        pending: list[tuple[str, int]] = []
        placed: list[_Placed] = []

        def bind(address: int) -> None:
            for label, line in pending:
                self._define(label, address, line)
            pending.clear()

        for stmt in self.statements:
            pending.extend((label, stmt.line) for label in stmt.labels)
            name = stmt.name
            if not name:
                continue
            try:
                if name in (".text", ".data", ".section"):
                    bind(_BASES[section] + loc[section])
                    section = self._section_name(stmt)
                    continue
                if name in (".globl", ".global"):
                    continue
                loc[section] = _align_up(loc[section], self._alignment(stmt))
                address = _BASES[section] + loc[section]
                bind(address)
                if name in (".align", ".balign"):
                    continue
                if not name.startswith(".") and section != "text":
                    raise EncodingError("instructions are only allowed in the .text section")
                size = len(self._emit(stmt, address, resolve=False))
            except EncodingError as exc:
                raise AssemblerError(str(exc), stmt.line) from None
            placed.append(_Placed(stmt, section, address, size))
            loc[section] += size
        bind(_BASES[section] + loc[section])
        return placed, loc

    @staticmethod
    def _section_name(stmt: _Statement) -> str:
        if stmt.name != ".section":
            return stmt.name[1:]
        if len(stmt.operands) != 1:
            raise EncodingError(".section expects one operand")
        target = stmt.operands[0].lower()
        if target == ".text":
            return "text"
        if target in (".data", ".rodata", ".bss"):
            return "data"
        raise EncodingError(f"unsupported section '{stmt.operands[0]}'")

    @staticmethod
    def _alignment(stmt: _Statement) -> int:
        name = stmt.name
        if not name.startswith("."):
            return 4
        if name == ".word":
            return 4
        if name == ".half":
            return 2
        if name in (".align", ".balign"):
            if len(stmt.operands) != 1:
                raise EncodingError(f"{name} expects one operand")
            n = parse_immediate(stmt.operands[0])
            if name == ".align":  # .align n means 2**n bytes on RISC-V
                _check(0 <= n <= 16, f".align exponent {n} out of range [0, 16]")
                return 1 << n
            _check(n > 0 and n & (n - 1) == 0, f".balign {n} is not a power of two")
            return n
        return 1

    # ---- shared by both passes -------------------------------------------

    def _emit(self, stmt: _Statement, address: int, resolve: bool) -> bytes:
        if stmt.name.startswith("."):
            return self._directive_bytes(stmt, resolve)
        words = [encode_fields(m, **f) for m, f in self._expand(stmt, address, resolve)]
        return b"".join(w.to_bytes(4, "little") for w in words)

    def _expand(self, stmt: _Statement, pc: int, resolve: bool) -> Expansion:
        name, ops = stmt.name, stmt.operands
        if name in OPCODES:
            return [(name, operands_to_fields(name, ops, lambda t: self._offset_to(t, pc, resolve)))]
        expansion = self._pseudo(name, ops, pc, resolve)
        if expansion is None:
            raise EncodingError(f"unknown instruction '{name}'")
        return expansion

    def _pseudo(self, name: str, ops: list[str], pc: int, resolve: bool) -> Expansion | None:
        reg = parse_register

        def arity(n: int) -> None:
            if len(ops) != n:
                raise EncodingError(f"'{name}' expects {n} operand(s), got {len(ops)}")

        def target(token: str) -> int:
            return self._offset_to(token, pc, resolve)

        match name:
            case "nop":
                arity(0)
                return [("addi", {})]
            case "li":
                arity(2)
                if not is_integer_literal(ops[1]):
                    raise EncodingError("li needs a numeric immediate (use la for addresses)")
                return load_immediate(reg(ops[0]), parse_immediate(ops[1]))
            case "la":
                arity(2)
                rd = reg(ops[0])
                address = self._address_of(ops[1], resolve)
                hi, lo = split_hi_lo(address - pc if resolve else 0)
                return [("auipc", {"rd": rd, "imm": hi}), ("addi", {"rd": rd, "rs1": rd, "imm": lo})]
            case "call":
                arity(1)
                hi, lo = split_hi_lo(target(ops[0]))
                return [("auipc", {"rd": 1, "imm": hi}), ("jalr", {"rd": 1, "rs1": 1, "imm": lo})]
            case "mv":
                arity(2)
                return [("addi", {"rd": reg(ops[0]), "rs1": reg(ops[1]), "imm": 0})]
            case "not":
                arity(2)
                return [("xori", {"rd": reg(ops[0]), "rs1": reg(ops[1]), "imm": -1})]
            case "neg":
                arity(2)
                return [("sub", {"rd": reg(ops[0]), "rs1": 0, "rs2": reg(ops[1])})]
            case "seqz":
                arity(2)
                return [("sltiu", {"rd": reg(ops[0]), "rs1": reg(ops[1]), "imm": 1})]
            case "snez":
                arity(2)
                return [("sltu", {"rd": reg(ops[0]), "rs1": 0, "rs2": reg(ops[1])})]
            case "sltz":
                arity(2)
                return [("slt", {"rd": reg(ops[0]), "rs1": reg(ops[1]), "rs2": 0})]
            case "sgtz":
                arity(2)
                return [("slt", {"rd": reg(ops[0]), "rs1": 0, "rs2": reg(ops[1])})]
            case "j":
                arity(1)
                return [("jal", {"rd": 0, "imm": target(ops[0])})]
            case "jr":
                arity(1)
                return [("jalr", {"rd": 0, "rs1": reg(ops[0]), "imm": 0})]
            case "ret":
                arity(0)
                return [("jalr", {"rd": 0, "rs1": 1, "imm": 0})]
            case "beqz" | "bnez" | "bltz" | "bgez" | "blez" | "bgtz":
                arity(2)
                rs, off = reg(ops[0]), target(ops[1])
                base, rs1, rs2 = {
                    "beqz": ("beq", rs, 0), "bnez": ("bne", rs, 0),
                    "bltz": ("blt", rs, 0), "bgez": ("bge", rs, 0),
                    "blez": ("bge", 0, rs), "bgtz": ("blt", 0, rs),
                }[name]
                return [(base, {"rs1": rs1, "rs2": rs2, "imm": off})]
            case "bgt" | "ble" | "bgtu" | "bleu":
                arity(3)
                # a > b  is  b < a ;  a <= b  is  b >= a  (operands swapped)
                base = {"bgt": "blt", "ble": "bge", "bgtu": "bltu", "bleu": "bgeu"}[name]
                return [(base, {"rs1": reg(ops[1]), "rs2": reg(ops[0]), "imm": target(ops[2])})]
        return None

    def _directive_bytes(self, stmt: _Statement, resolve: bool) -> bytes:
        name, ops = stmt.name, stmt.operands
        if name in (".word", ".half", ".byte"):
            _check(bool(ops), f"{name} needs at least one value")
            size = {".word": 4, ".half": 2, ".byte": 1}[name]
            return b"".join(self._data_value(op, size, resolve) for op in ops)
        if name in (".ascii", ".asciz", ".string"):
            _check(bool(ops), f"{name} needs a string")
            terminator = b"" if name == ".ascii" else b"\0"
            return b"".join(_parse_string(op) + terminator for op in ops)
        if name in (".space", ".zero"):
            _check(len(ops) == 1, f"{name} expects one operand")
            count = parse_immediate(ops[0])
            _check(count >= 0, f"{name} size must be non-negative")
            return bytes(count)
        raise EncodingError(f"unknown directive '{name}'")

    def _data_value(self, token: str, size: int, resolve: bool) -> bytes:
        if size == 4:
            value = self._address_of(token, resolve)
        else:
            value = parse_immediate(token)
        bits = 8 * size
        lo, hi = -(1 << (bits - 1)), (1 << bits) - 1
        _check(lo <= value <= hi, f"value {value} does not fit in {size} byte(s)")
        return (value & ((1 << bits) - 1)).to_bytes(size, "little")

    # ---- pass 2 ----------------------------------------------------------

    def run(self) -> Program:
        placed, sizes = self._layout()
        images = {"text": bytearray(sizes["text"]), "data": bytearray(sizes["data"])}
        line_map: dict[int, int] = {}
        for item in placed:
            try:
                chunk = self._emit(item.stmt, item.address, resolve=True)
            except EncodingError as exc:
                raise AssemblerError(str(exc), item.stmt.line) from None
            assert len(chunk) == item.size, "statement size changed between passes"
            offset = item.address - _BASES[item.section]
            images[item.section][offset:offset + len(chunk)] = chunk
            if not item.stmt.name.startswith("."):
                for i in range(0, len(chunk), 4):
                    line_map[item.address + i] = item.stmt.line
        return Program(
            text=bytes(images["text"]),
            data=bytes(images["data"]),
            symbols=dict(self.symbols),
            line_map=line_map,
            entry=self.symbols.get("_start", TEXT_BASE),
        )


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise EncodingError(message)


def _parse_string(token: str) -> bytes:
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        raise EncodingError(f"expected a double-quoted string, got {token}")
    return unescape(token[1:-1])


def assemble(source: str) -> Program:
    """Assemble RV32I source text into a :class:`Program`.

    Raises :class:`AssemblerError` (with ``.line``) on any error.
    """
    return _Assembler(source).run()
