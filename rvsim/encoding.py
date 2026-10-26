"""Bit-level encoding and decoding of single RV32I instructions.

Every RV32I instruction is one 32-bit word.  Its low seven bits, the
*opcode*, select one of six field layouts called formats:

    R   funct7 | rs2 | rs1 | funct3 |    rd    | opcode   register-register ALU
    I     imm[11:0]  | rs1 | funct3 |    rd    | opcode   ALU-immediate, loads, jalr
    S   imm[11:5] | rs2 | rs1 | f3  | imm[4:0] | opcode   stores
    B   imm[12|10:5] | rs2 | rs1 | f3 | imm[4:1|11] | op   conditional branches
    U         imm[31:12]            |    rd    | opcode   lui, auipc
    J   imm[20|10:1|11|19:12]       |    rd    | opcode   jal

The register fields rs1, rs2 and rd always occupy the same bit positions, so
hardware can start reading the register file before it has decoded the
instruction.  The immediate bits are scattered instead, but the sign bit is
always bit 31, which lets sign extension begin early.  Branch and jump
offsets are byte offsets that are always even (instructions are at least
2-byte aligned in the full ISA), so bit 0 is implied and not stored, which
doubles the reachable range.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from typing import Callable

MASK32 = 0xFFFF_FFFF

# ABI register names, indexed by register number.
ABI_NAMES: tuple[str, ...] = (
    "zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2",
    "s0", "s1", "a0", "a1", "a2", "a3", "a4", "a5",
    "a6", "a7", "s2", "s3", "s4", "s5", "s6", "s7",
    "s8", "s9", "s10", "s11", "t3", "t4", "t5", "t6",
)

REGISTER_NUMBERS: dict[str, int] = {
    **{f"x{i}": i for i in range(32)},
    **{name: i for i, name in enumerate(ABI_NAMES)},
    "fp": 8,  # the frame pointer is another name for s0
}


class EncodingError(ValueError):
    """Malformed operand, unknown mnemonic, or a value that does not fit its field."""


@dataclass(frozen=True)
class OpSpec:
    """Static description of one base instruction.

    ``syntax`` names the assembly operand shape, e.g. ``"load"`` for
    ``lw rd, imm(rs1)`` or ``"branch"`` for ``beq rs1, rs2, offset``.
    """

    fmt: str
    opcode: int
    funct3: int = 0
    funct7: int = 0
    syntax: str = "rrr"


def _build_table() -> dict[str, OpSpec]:
    table: dict[str, OpSpec] = {
        "lui": OpSpec("U", 0b0110111, syntax="upper"),
        "auipc": OpSpec("U", 0b0010111, syntax="upper"),
        "jal": OpSpec("J", 0b1101111, syntax="jal"),
        "jalr": OpSpec("I", 0b1100111, 0b000, syntax="jalr"),
        "ecall": OpSpec("I", 0b1110011, syntax="none"),
        "ebreak": OpSpec("I", 0b1110011, syntax="none"),
        "fence": OpSpec("I", 0b0001111, syntax="none"),
    }
    for name, f3 in (("beq", 0), ("bne", 1), ("blt", 4), ("bge", 5), ("bltu", 6), ("bgeu", 7)):
        table[name] = OpSpec("B", 0b1100011, f3, syntax="branch")
    for name, f3 in (("lb", 0), ("lh", 1), ("lw", 2), ("lbu", 4), ("lhu", 5)):
        table[name] = OpSpec("I", 0b0000011, f3, syntax="load")
    for name, f3 in (("sb", 0), ("sh", 1), ("sw", 2)):
        table[name] = OpSpec("S", 0b0100011, f3, syntax="store")
    for name, f3 in (("addi", 0), ("slti", 2), ("sltiu", 3), ("xori", 4), ("ori", 6), ("andi", 7)):
        table[name] = OpSpec("I", 0b0010011, f3, syntax="rri")
    # Shift-immediates reuse the I format: the low 5 immediate bits hold the
    # shift amount and the upper 7 bits act as funct7 (0x20 selects arithmetic).
    for name, f3, f7 in (("slli", 1, 0), ("srli", 5, 0), ("srai", 5, 0b0100000)):
        table[name] = OpSpec("I", 0b0010011, f3, f7, syntax="shift")
    for name, f3, f7 in (
        ("add", 0, 0), ("sub", 0, 0b0100000), ("sll", 1, 0), ("slt", 2, 0),
        ("sltu", 3, 0), ("xor", 4, 0), ("srl", 5, 0), ("sra", 5, 0b0100000),
        ("or", 6, 0), ("and", 7, 0),
    ):
        table[name] = OpSpec("R", 0b0110011, f3, f7, syntax="rrr")
    return table


OPCODES: dict[str, OpSpec] = _build_table()

# Instructions without operands have a single fixed encoding.
# fence is emitted as "fence iorw, iorw", the conservative full barrier.
_FIXED_WORDS: dict[str, int] = {"ecall": 0x0000_0073, "ebreak": 0x0010_0073, "fence": 0x0FF0_000F}


# --------------------------------------------------------------------------
# Small numeric helpers
# --------------------------------------------------------------------------

def sign_extend(value: int, bits: int) -> int:
    """Interpret the low ``bits`` bits of ``value`` as a two's-complement number."""
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def to_signed32(value: int) -> int:
    return sign_extend(value, 32)


def _check_range(value: int, lo: int, hi: int, what: str) -> None:
    if not lo <= value <= hi:
        raise EncodingError(f"{what} {value} out of range [{lo}, {hi}]")


# --------------------------------------------------------------------------
# Operand parsing (shared with the assembler)
# --------------------------------------------------------------------------

_INT_RE = re.compile(r"^[+-]?(0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|[0-9]+)$")
_MEM_RE = re.compile(r"^(.*?)\(\s*([A-Za-z0-9]+)\s*\)$")
_ESCAPES = {"n": 10, "t": 9, "r": 13, "0": 0, "\\": 92, '"': 34, "'": 39}


def unescape(body: str) -> bytes:
    """Decode the body of a string or character literal (C-style escapes)."""
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out += ch.encode("utf-8")
            i += 1
            continue
        if i + 1 >= len(body):
            raise EncodingError("dangling backslash in literal")
        nxt = body[i + 1]
        if nxt == "x":
            digits = body[i + 2:i + 4]
            if len(digits) != 2 or any(c not in string.hexdigits for c in digits):
                raise EncodingError("\\x escape needs two hex digits")
            out.append(int(digits, 16))
            i += 4
        elif nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            i += 2
        else:
            raise EncodingError(f"unknown escape sequence '\\{nxt}'")
    return bytes(out)


def is_integer_literal(token: str) -> bool:
    tok = token.strip()
    return bool(_INT_RE.match(tok)) or (len(tok) >= 3 and tok[0] == tok[-1] == "'")


def parse_immediate(token: str) -> int:
    """Parse a decimal, hex (0x), binary (0b), octal (0o) or 'c' literal."""
    tok = token.strip()
    if len(tok) >= 3 and tok[0] == tok[-1] == "'":
        data = unescape(tok[1:-1])
        if len(data) != 1:
            raise EncodingError(f"character literal {tok} must denote one byte")
        return data[0]
    if not _INT_RE.match(tok):
        raise EncodingError(f"invalid immediate '{tok}'")
    negative = tok.startswith("-")
    body = tok.lstrip("+-")
    value = int(body, 0) if body[:2].lower() in ("0x", "0b", "0o") else int(body, 10)
    return -value if negative else value


def parse_register(token: str) -> int:
    """Map ``x0``..``x31`` or an ABI name (``zero``, ``sp``, ``t0``, ``fp`` ...) to its number."""
    name = token.strip().lower()
    try:
        return REGISTER_NUMBERS[name]
    except KeyError:
        raise EncodingError(f"invalid register '{token.strip()}'") from None


def parse_memory_operand(token: str) -> tuple[int, int]:
    """Parse ``imm(reg)`` (or ``(reg)``) into ``(imm, register_number)``."""
    match = _MEM_RE.match(token.strip())
    if not match:
        raise EncodingError(f"expected memory operand 'offset(register)', got '{token.strip()}'")
    offset = match.group(1).strip()
    return (parse_immediate(offset) if offset else 0), parse_register(match.group(2))


def operands_to_fields(
    mnemonic: str,
    operands: list[str],
    target: Callable[[str], int] = parse_immediate,
) -> dict[str, int]:
    """Turn the textual operands of a base instruction into encoder fields.

    ``target`` converts a branch/jump operand into a PC-relative byte offset.
    By default only numeric offsets are accepted; the assembler passes a
    resolver that also understands labels.
    """
    spec = OPCODES.get(mnemonic)
    if spec is None:
        raise EncodingError(f"unknown instruction '{mnemonic}'")
    ops = operands

    def arity(*allowed: int) -> None:
        if len(ops) not in allowed:
            want = " or ".join(str(n) for n in allowed)
            raise EncodingError(f"'{mnemonic}' expects {want} operand(s), got {len(ops)}")

    reg = parse_register
    match spec.syntax:
        case "rrr":
            arity(3)
            return {"rd": reg(ops[0]), "rs1": reg(ops[1]), "rs2": reg(ops[2])}
        case "rri" | "shift":
            arity(3)
            return {"rd": reg(ops[0]), "rs1": reg(ops[1]), "imm": parse_immediate(ops[2])}
        case "load":
            arity(2)
            imm, rs1 = parse_memory_operand(ops[1])
            return {"rd": reg(ops[0]), "rs1": rs1, "imm": imm}
        case "store":
            arity(2)
            imm, rs1 = parse_memory_operand(ops[1])
            return {"rs2": reg(ops[0]), "rs1": rs1, "imm": imm}
        case "branch":
            arity(3)
            return {"rs1": reg(ops[0]), "rs2": reg(ops[1]), "imm": target(ops[2])}
        case "upper":
            arity(2)
            return {"rd": reg(ops[0]), "imm": parse_immediate(ops[1])}
        case "jal":
            arity(1, 2)
            if len(ops) == 1:  # "jal label" links through ra
                return {"rd": 1, "imm": target(ops[0])}
            return {"rd": reg(ops[0]), "imm": target(ops[1])}
        case "jalr":
            arity(1, 2, 3)
            if len(ops) == 1:  # "jalr rs" links through ra
                return {"rd": 1, "rs1": reg(ops[0]), "imm": 0}
            if len(ops) == 2:
                imm, rs1 = parse_memory_operand(ops[1])
                return {"rd": reg(ops[0]), "rs1": rs1, "imm": imm}
            return {"rd": reg(ops[0]), "rs1": reg(ops[1]), "imm": parse_immediate(ops[2])}
        case _:  # "none"
            arity(0)
            return {}


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------

def encode_fields(mnemonic: str, rd: int = 0, rs1: int = 0, rs2: int = 0, imm: int = 0) -> int:
    """Pack already-numeric fields into a 32-bit machine word.

    Raises :class:`EncodingError` when a value does not fit its field, e.g. an
    I-type immediate outside [-2048, 2047] or an odd branch offset.
    """
    spec = OPCODES.get(mnemonic)
    if spec is None:
        raise EncodingError(f"unknown instruction '{mnemonic}'")
    if mnemonic in _FIXED_WORDS:
        return _FIXED_WORDS[mnemonic]
    for value in (rd, rs1, rs2):
        _check_range(value, 0, 31, "register number")
    op, f3, f7 = spec.opcode, spec.funct3, spec.funct7

    if spec.fmt == "R":
        return f7 << 25 | rs2 << 20 | rs1 << 15 | f3 << 12 | rd << 7 | op

    if spec.fmt == "I":
        if spec.syntax == "shift":
            _check_range(imm, 0, 31, "shift amount")
            field = f7 << 5 | imm
        else:
            _check_range(imm, -2048, 2047, "immediate")
            field = imm & 0xFFF
        return field << 20 | rs1 << 15 | f3 << 12 | rd << 7 | op

    if spec.fmt == "S":
        _check_range(imm, -2048, 2047, "store offset")
        u = imm & 0xFFF
        return (u >> 5) << 25 | rs2 << 20 | rs1 << 15 | f3 << 12 | (u & 0x1F) << 7 | op

    if spec.fmt == "B":
        _check_range(imm, -4096, 4094, "branch offset")
        if imm % 2:
            raise EncodingError(f"branch offset {imm} must be even")
        u = imm & 0x1FFF
        return (
            (u >> 12 & 1) << 31 | (u >> 5 & 0x3F) << 25 | rs2 << 20 | rs1 << 15
            | f3 << 12 | (u >> 1 & 0xF) << 8 | (u >> 11 & 1) << 7 | op
        )

    if spec.fmt == "U":
        _check_range(imm, -(1 << 19), (1 << 20) - 1, "upper immediate")
        return (imm & 0xFFFFF) << 12 | rd << 7 | op

    # J format
    _check_range(imm, -(1 << 20), (1 << 20) - 2, "jump offset")
    if imm % 2:
        raise EncodingError(f"jump offset {imm} must be even")
    u = imm & 0x1FFFFF
    return (
        (u >> 20 & 1) << 31 | (u >> 1 & 0x3FF) << 21 | (u >> 11 & 1) << 20
        | (u >> 12 & 0xFF) << 12 | rd << 7 | op
    )


def split_instruction(text: str) -> tuple[str, list[str]]:
    """Split ``"beq t0, t1, 42"`` into ``("beq", ["t0", "t1", "42"])``."""
    text = text.split("#", 1)[0].strip()
    if not text:
        raise EncodingError("empty instruction")
    parts = text.split(None, 1)
    operands = [op.strip() for op in parts[1].split(",")] if len(parts) > 1 else []
    if any(not op for op in operands):
        raise EncodingError("empty operand")
    return parts[0].lower(), operands


def encode(text: str) -> int:
    """Encode one base instruction written in assembly, e.g. ``encode("lw t2, 12(s3)")``.

    Branch and jump targets must be numeric byte offsets relative to the
    instruction itself (labels and pseudo-instructions need the assembler).
    """
    mnemonic, operands = split_instruction(text)
    return encode_fields(mnemonic, **operands_to_fields(mnemonic, operands))


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Decoded:
    """Fields of a decoded instruction; unused fields are zero."""

    mnemonic: str
    rd: int = 0
    rs1: int = 0
    rs2: int = 0
    imm: int = 0


def _decode_key(spec: OpSpec) -> tuple[int, int | None, int | None]:
    if spec.fmt in ("U", "J"):
        return (spec.opcode, None, None)
    if spec.fmt == "R" or spec.syntax == "shift":
        return (spec.opcode, spec.funct3, spec.funct7)
    return (spec.opcode, spec.funct3, None)


_DECODE_TABLE: dict[tuple[int, int | None, int | None], str] = {
    _decode_key(spec): name for name, spec in OPCODES.items() if name not in _FIXED_WORDS
}
_FIXED_BY_WORD = {word: name for name, word in _FIXED_WORDS.items()}


def decode_fields(word: int) -> Decoded:
    """Split a machine word into mnemonic, registers and sign-extended immediate."""
    word &= MASK32
    opcode = word & 0x7F
    rd = word >> 7 & 0x1F
    f3 = word >> 12 & 0x7
    rs1 = word >> 15 & 0x1F
    rs2 = word >> 20 & 0x1F
    f7 = word >> 25

    if word in _FIXED_BY_WORD:
        return Decoded(_FIXED_BY_WORD[word])
    if opcode == 0b0001111 and f3 == 0:
        return Decoded("fence")

    name = (
        _DECODE_TABLE.get((opcode, f3, f7))
        or _DECODE_TABLE.get((opcode, f3, None))
        or _DECODE_TABLE.get((opcode, None, None))
    )
    if name is None:
        raise EncodingError(f"illegal instruction word 0x{word:08x}")
    spec = OPCODES[name]

    match spec.fmt:
        case "R":
            return Decoded(name, rd, rs1, rs2)
        case "I":
            imm = rs2 if spec.syntax == "shift" else sign_extend(word >> 20, 12)
            return Decoded(name, rd, rs1, 0, imm)
        case "S":
            return Decoded(name, 0, rs1, rs2, sign_extend(f7 << 5 | rd, 12))
        case "B":
            imm = (word >> 31 & 1) << 12 | (word >> 7 & 1) << 11 | (word >> 25 & 0x3F) << 5 | (word >> 8 & 0xF) << 1
            return Decoded(name, 0, rs1, rs2, sign_extend(imm, 13))
        case "U":
            return Decoded(name, rd, 0, 0, word >> 12)
        case _:  # J
            imm = (word >> 31 & 1) << 20 | (word >> 12 & 0xFF) << 12 | (word >> 20 & 1) << 11 | (word >> 21 & 0x3FF) << 1
            return Decoded(name, rd, 0, 0, sign_extend(imm, 21))


def format_instruction(d: Decoded) -> str:
    """Render decoded fields as canonical assembly (ABI names, decimal offsets)."""
    r = ABI_NAMES
    m = d.mnemonic
    match OPCODES[m].syntax:
        case "rrr":
            return f"{m} {r[d.rd]}, {r[d.rs1]}, {r[d.rs2]}"
        case "rri" | "shift":
            return f"{m} {r[d.rd]}, {r[d.rs1]}, {d.imm}"
        case "load" | "jalr":
            return f"{m} {r[d.rd]}, {d.imm}({r[d.rs1]})"
        case "store":
            return f"{m} {r[d.rs2]}, {d.imm}({r[d.rs1]})"
        case "branch":
            return f"{m} {r[d.rs1]}, {r[d.rs2]}, {d.imm}"
        case "upper":
            return f"{m} {r[d.rd]}, 0x{d.imm:x}"
        case "jal":
            return f"{m} {r[d.rd]}, {d.imm}"
        case _:
            return m


def decode(word: int) -> str:
    """Disassemble one machine word, e.g. ``decode(0x02628563) == "beq t0, t1, 42"``."""
    return format_instruction(decode_fields(word))
