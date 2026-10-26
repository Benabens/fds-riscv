"""Encoding/decoding tests against hand-computed machine words.

Every expected word below was worked out on paper from the field layouts in
the RISC-V Unprivileged ISA specification (chapter "RV32I Base Integer
Instruction Set"), never by calling the encoder.  The comment next to each
vector shows the field breakdown used.
"""

import random
import unittest

from rvsim.encoding import (
    OPCODES,
    EncodingError,
    decode,
    decode_fields,
    encode,
    encode_fields,
    parse_immediate,
    parse_register,
)

# Vectors quoted directly from the course/spec examples.
SPEC_VECTORS = [
    ("beq t0, t1, 42", 0x02628563),
    ("bltu s1, s2, -4", 0xFF24EEE3),
    ("lw t2, 12(s3)", 0x00C9A383),
    ("sb t0, 4(s6)", 0x005B0223),
    ("addi x0, x0, 0", 0x00000013),
    ("add t2, t0, t1", 0x006283B3),
    ("jal ra, 0", 0x000000EF),
]

# (canonical text, word) computed by hand; canonical = ABI names, decimal
# offsets, U-type immediates in lowercase hex.
HAND_VECTORS = [
    # R: funct7 | rs2 | rs1 | funct3 | rd | 0110011
    ("sub s0, s1, s2", 0x41248433),     # 0100000 10010 01001 000 01000
    ("sra a2, a3, a4", 0x40E6D633),     # 0100000 01110 01101 101 01100
    ("sltu a0, zero, a1", 0x00B03533),  # 0000000 01011 00000 011 01010
    ("add t2, t0, t1", 0x006283B3),
    # I (ALU): imm[11:0] | rs1 | funct3 | rd | 0010011
    ("addi sp, sp, -16", 0xFF010113),   # imm 0xFF0
    ("addi a0, zero, -2048", 0x80000513),  # imm 0x800 (most negative)
    ("addi a0, zero, 2047", 0x7FF00513),   # imm 0x7FF (most positive)
    ("xori a0, a1, -1", 0xFFF5C513),
    ("sltiu a0, a0, 1", 0x00153513),
    ("andi t0, t0, 255", 0x0FF2F293),
    # I (shifts): funct7 | shamt | rs1 | funct3 | rd | 0010011
    ("slli a0, a0, 2", 0x00251513),
    ("srli a1, a1, 31", 0x01F5D593),
    ("srai t0, t1, 31", 0x41F35293),
    # I (loads): imm | rs1 | funct3 | rd | 0000011
    ("lb t0, 0(a0)", 0x00050283),
    ("lh s1, 6(sp)", 0x00611483),
    ("lw t2, 12(s3)", 0x00C9A383),
    ("lw ra, 12(sp)", 0x00C12083),
    ("lbu a0, -1(a1)", 0xFFF5C503),
    ("lhu a0, 2(a1)", 0x0025D503),
    # I (jalr)
    ("jalr ra, 0(t0)", 0x000280E7),
    ("jalr zero, 0(ra)", 0x00008067),   # ret
    # S: imm[11:5] | rs2 | rs1 | funct3 | imm[4:0] | 0100011
    ("sb t0, 4(s6)", 0x005B0223),
    ("sh t1, 2047(t2)", 0x7E639FA3),
    ("sw ra, 12(sp)", 0x00112623),
    ("sw a5, -4(s0)", 0xFEF42E23),
    ("sw zero, -2048(a0)", 0x80052023),
    # B: imm[12|10:5] | rs2 | rs1 | funct3 | imm[4:1|11] | 1100011
    ("beq t0, t1, 42", 0x02628563),
    ("bltu s1, s2, -4", 0xFF24EEE3),
    ("bne a0, zero, -8", 0xFE051CE3),
    ("blt a0, a1, 4094", 0x7EB54FE3),   # largest forward offset
    ("beq zero, zero, -4096", 0x80000063),  # largest backward offset
    ("bge a0, a1, 2048", 0x00B550E3),   # only imm[11] set -> word bit 7
    ("bgeu t0, t1, 16", 0x0062F863),
    # U: imm[31:12] | rd | opcode
    ("lui a0, 0x12345", 0x12345537),
    ("auipc t0, 0xfffff", 0xFFFFF297),
    ("auipc ra, 0x0", 0x00000097),
    # J: imm[20|10:1|11|19:12] | rd | 1101111
    ("jal ra, 0", 0x000000EF),
    ("jal zero, 0", 0x0000006F),
    ("jal zero, -4", 0xFFDFF06F),
    ("jal ra, 2048", 0x001000EF),       # only imm[11] set
    ("jal zero, 1048574", 0x7FFFF06F),  # largest forward offset
    ("jal zero, -1048576", 0x8000006F),  # largest backward offset
    # System
    ("ecall", 0x00000073),
    ("ebreak", 0x00100073),
]


class SpecVectorTests(unittest.TestCase):
    def test_spec_vectors(self):
        for text, word in SPEC_VECTORS:
            with self.subTest(text=text):
                self.assertEqual(encode(text), word, f"{text}: got 0x{encode(text):08X}")

    def test_hand_vectors_encode(self):
        for text, word in HAND_VECTORS:
            with self.subTest(text=text):
                self.assertEqual(encode(text), word, f"{text}: got 0x{encode(text):08X}")

    def test_hand_vectors_decode(self):
        for text, word in HAND_VECTORS:
            with self.subTest(word=hex(word)):
                self.assertEqual(decode(word), text)

    def test_equivalent_spellings(self):
        # Numeric register names, fp alias, case, hex offsets and comments.
        self.assertEqual(encode("addi x2, x2, -16"), 0xFF010113)
        self.assertEqual(encode("ADDI SP, SP, -0x10"), 0xFF010113)
        self.assertEqual(encode("sw a5, -4(fp)"), 0xFEF42E23)
        self.assertEqual(encode("lw t2, 0xc(s3)  # comment"), 0x00C9A383)
        self.assertEqual(encode("jalr ra, t0, 0"), 0x000280E7)
        self.assertEqual(encode("jalr t0"), 0x000280E7)
        self.assertEqual(encode("jal 2048"), 0x001000EF)
        self.assertEqual(encode("lw ra, (sp)"), 0x00012083)

    def test_fence_is_accepted(self):
        self.assertEqual(encode("fence"), 0x0FF0000F)
        self.assertEqual(decode(0x0FF0000F), "fence")


class RoundTripTests(unittest.TestCase):
    def test_canonical_text_round_trips(self):
        for text, _ in HAND_VECTORS:
            with self.subTest(text=text):
                self.assertEqual(decode(encode(text)), text)

    def test_random_fields_round_trip(self):
        """Encode random legal fields for every mnemonic, decode, compare."""
        rng = random.Random(173)
        for mnemonic, spec in OPCODES.items():
            if spec.syntax == "none":
                continue
            for _ in range(200):
                fields = {"rd": rng.randrange(32), "rs1": rng.randrange(32), "rs2": rng.randrange(32)}
                fmt = spec.fmt
                if spec.syntax == "shift":
                    fields["imm"] = rng.randrange(32)
                elif fmt in ("I", "S"):
                    fields["imm"] = rng.randint(-2048, 2047)
                elif fmt == "B":
                    fields["imm"] = 2 * rng.randint(-2048, 2047)
                elif fmt == "U":
                    fields["imm"] = rng.randrange(1 << 20)
                elif fmt == "J":
                    fields["imm"] = 2 * rng.randint(-(1 << 19), (1 << 19) - 1)
                else:
                    fields["imm"] = 0
                # Drop fields the format does not store.
                if fmt in ("S", "B"):
                    fields["rd"] = 0
                if fmt in ("I", "U", "J"):
                    fields["rs2"] = 0
                if fmt in ("U", "J"):
                    fields["rs1"] = 0
                word = encode_fields(mnemonic, **fields)
                d = decode_fields(word)
                with self.subTest(mnemonic=mnemonic, fields=fields):
                    self.assertEqual(d.mnemonic, mnemonic)
                    self.assertEqual((d.rd, d.rs1, d.rs2, d.imm),
                                     (fields["rd"], fields["rs1"], fields["rs2"], fields["imm"]))
                    self.assertEqual(encode(decode(word)), word)

    def test_illegal_words_rejected(self):
        for word in (0x00000000, 0xFFFFFFFF, 0x02000033, 0x40001013):
            with self.subTest(word=hex(word)):
                with self.assertRaises(EncodingError):
                    decode(word)


class FieldRangeTests(unittest.TestCase):
    def test_i_immediate_range(self):
        encode("addi t0, t0, 2047")
        encode("addi t0, t0, -2048")
        for bad in ("addi t0, t0, 2048", "addi t0, t0, -2049", "lw t0, 4096(sp)"):
            with self.subTest(bad=bad), self.assertRaisesRegex(EncodingError, "out of range"):
                encode(bad)

    def test_store_offset_range(self):
        with self.assertRaisesRegex(EncodingError, "out of range"):
            encode("sw t0, 2048(sp)")

    def test_shift_amount_range(self):
        with self.assertRaisesRegex(EncodingError, "shift amount"):
            encode("slli t0, t0, 32")
        with self.assertRaisesRegex(EncodingError, "shift amount"):
            encode("srai t0, t0, -1")

    def test_branch_offsets(self):
        with self.assertRaisesRegex(EncodingError, "even"):
            encode("beq t0, t1, 3")
        with self.assertRaisesRegex(EncodingError, "out of range"):
            encode("beq t0, t1, 4096")
        with self.assertRaisesRegex(EncodingError, "out of range"):
            encode("beq t0, t1, -4098")

    def test_jump_offsets(self):
        with self.assertRaisesRegex(EncodingError, "even"):
            encode("jal ra, 1")
        with self.assertRaisesRegex(EncodingError, "out of range"):
            encode("jal ra, 1048576")

    def test_upper_immediate_range(self):
        with self.assertRaisesRegex(EncodingError, "out of range"):
            encode("lui t0, 0x100000")

    def test_bad_registers(self):
        for bad in ("x32", "t7", "a8", "s12", "r1", ""):
            with self.subTest(bad=bad), self.assertRaises(EncodingError):
                parse_register(bad)
        with self.assertRaisesRegex(EncodingError, "invalid register 't7'"):
            encode("add t7, t0, t1")

    def test_register_names(self):
        self.assertEqual(parse_register("zero"), 0)
        self.assertEqual(parse_register("fp"), parse_register("s0"))
        self.assertEqual(parse_register("s0"), 8)
        self.assertEqual(parse_register("s2"), 18)
        self.assertEqual(parse_register("t3"), 28)
        self.assertEqual(parse_register("a7"), 17)
        self.assertEqual(parse_register("X31"), 31)

    def test_unknown_mnemonic_and_arity(self):
        with self.assertRaisesRegex(EncodingError, "unknown instruction 'mul'"):
            encode("mul a0, a1, a2")
        with self.assertRaisesRegex(EncodingError, "expects 3"):
            encode("add a0, a1")

    def test_immediate_syntax(self):
        self.assertEqual(parse_immediate("-0x10"), -16)
        self.assertEqual(parse_immediate("0b101"), 5)
        self.assertEqual(parse_immediate("'A'"), 65)
        self.assertEqual(parse_immediate("'\\n'"), 10)
        with self.assertRaises(EncodingError):
            parse_immediate("12abc")


if __name__ == "__main__":
    unittest.main()
