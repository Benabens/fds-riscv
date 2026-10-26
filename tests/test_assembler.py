"""Assembler tests: pseudo-instruction expansion, labels, directives, errors.

Expected machine words are hand-computed from the spec field layouts; the
expected expansion of each pseudo-instruction follows the table in the
RISC-V assembly programmer's manual.
"""

import textwrap
import unittest

from rvsim.assembler import DATA_BASE, TEXT_BASE, AssemblerError, assemble
from rvsim.encoding import encode


def words(source: str) -> list[int]:
    return assemble(textwrap.dedent(source)).words


class LoadImmediateTests(unittest.TestCase):
    def test_small_values_use_single_addi(self):
        self.assertEqual(words("li t0, 0"), [0x00000293])       # addi t0, zero, 0
        self.assertEqual(words("li t0, -1"), [0xFFF00293])      # addi t0, zero, -1
        self.assertEqual(words("li t0, 2047"), [0x7FF00293])
        self.assertEqual(words("li t0, -2048"), [0x80000293])
        # 0xFFFFF800 is -2048 as a 32-bit pattern: still one addi.
        self.assertEqual(words("li t0, 0xFFFFF800"), [0x80000293])

    def test_rounding_case_0x800(self):
        # 0x800 = 0x1000 + (-2048): lui t0, 1 ; addi t0, t0, -2048
        self.assertEqual(words("li t0, 0x800"), [0x000012B7, 0x80028293])

    def test_rounding_case_int_max(self):
        # 0x7FFFFFFF = 0x80000000 + (-1): lui t0, 0x80000 ; addi t0, t0, -1
        self.assertEqual(words("li t0, 0x7FFFFFFF"), [0x800002B7, 0xFFF28293])

    def test_lower_bits_zero_needs_only_lui(self):
        self.assertEqual(words("li t0, 0x80000000"), [0x800002B7])
        self.assertEqual(words("li a0, 0x12345000"), [0x12345537])

    def test_no_rounding_needed(self):
        # 0x12345678: lower 12 bits 0x678 < 0x800, so lui 0x12345 ; addi 0x678
        self.assertEqual(words("li a0, 0x12345678"), [0x12345537, 0x67850513])

    def test_out_of_range(self):
        with self.assertRaisesRegex(AssemblerError, "line 1: li immediate"):
            assemble("li t0, 0x100000000")
        with self.assertRaisesRegex(AssemblerError, "numeric immediate"):
            assemble("li t0, somewhere")


class PseudoInstructionTests(unittest.TestCase):
    def expands_to(self, pseudo: str, *base: str) -> None:
        self.assertEqual(words(pseudo), [encode(b) for b in base], pseudo)

    def test_register_pseudos(self):
        self.expands_to("nop", "addi zero, zero, 0")
        self.expands_to("mv a0, a1", "addi a0, a1, 0")
        self.expands_to("not a0, a1", "xori a0, a1, -1")
        self.expands_to("neg a0, a1", "sub a0, zero, a1")
        self.expands_to("seqz a0, a1", "sltiu a0, a1, 1")
        self.expands_to("snez a0, a1", "sltu a0, zero, a1")
        self.expands_to("ret", "jalr zero, 0(ra)")
        self.expands_to("jr t0", "jalr zero, 0(t0)")

    def test_branch_pseudos_swap_operands(self):
        self.expands_to("beqz a0, 8", "beq a0, zero, 8")
        self.expands_to("bnez a0, -4", "bne a0, zero, -4")
        self.expands_to("bgt a0, a1, 12", "blt a1, a0, 12")
        self.expands_to("ble a0, a1, 12", "bge a1, a0, 12")
        self.expands_to("bgtu a0, a1, 12", "bltu a1, a0, 12")
        self.expands_to("bleu a0, a1, 12", "bgeu a1, a0, 12")
        self.expands_to("blez a0, 8", "bge zero, a0, 8")
        self.expands_to("bgtz a0, 8", "blt zero, a0, 8")

    def test_j_and_known_words(self):
        self.assertEqual(words("j 0"), [0x0000006F])
        self.assertEqual(words("ret"), [0x00008067])
        self.assertEqual(words("nop"), [0x00000013])

    def test_la_is_pc_relative(self):
        # 'msg' is at 0x10000000; la at pc 4 needs offset 0x0FFFFFFC
        # = (0x10000 << 12) + (-4): auipc a0, 0x10000 ; addi a0, a0, -4
        program = assemble(textwrap.dedent("""
            .data
            msg: .word 7
            .text
            nop
            la a0, msg
        """))
        self.assertEqual(program.words[1:], [0x10000517, 0xFFC50513])

    def test_call_expands_to_auipc_jalr(self):
        program = assemble(textwrap.dedent("""
            call f
            nop
            f: ret
        """))
        # call takes 8 bytes, so f is at 12: auipc ra, 0 ; jalr ra, 12(ra)
        self.assertEqual(program.symbols["f"], 12)
        self.assertEqual(program.words[:2], [0x00000097, 0x00C080E7])


class LabelTests(unittest.TestCase):
    def test_forward_and_backward_references(self):
        program = assemble(textwrap.dedent("""
            start:  nop                    # 0x00
                    beq zero, zero, start  # 0x04 -> -4
                    j end                  # 0x08 -> +8
                    nop                    # 0x0C
            end:    bnez t0, start         # 0x10 -> -16
        """))
        self.assertEqual(program.symbols["start"], 0)
        self.assertEqual(program.symbols["end"], 0x10)
        self.assertEqual(program.words[1], 0xFE000EE3)  # beq zero, zero, -4
        self.assertEqual(program.words[2], 0x0080006F)  # jal zero, 8
        self.assertEqual(program.words[4], encode("bne t0, zero, -16"))

    def test_numeric_offsets_are_relative(self):
        self.assertEqual(words("nop\nnop\nbeq t0, t1, 42"), [0x13, 0x13, 0x02628563])

    def test_multiple_labels_and_label_only_lines(self):
        program = assemble("a:\nb: c: nop\nd:")
        self.assertEqual([program.symbols[k] for k in "abcd"], [0, 0, 0, 4])

    def test_start_label_sets_entry(self):
        self.assertEqual(assemble("nop\n_start: nop").entry, 4)
        self.assertEqual(assemble("nop").entry, TEXT_BASE)

    def test_line_map(self):
        program = assemble("# header\n\nli t0, 0x12345678\nnop")
        self.assertEqual(program.line_at(0), 3)
        self.assertEqual(program.line_at(4), 3)
        self.assertEqual(program.line_at(8), 4)


class DirectiveTests(unittest.TestCase):
    def test_data_layout_little_endian_and_alignment(self):
        program = assemble(textwrap.dedent(r"""
            .data
            a: .byte 1, 2
            b: .word 0x11223344      # aligned up to 4
            c: .half -2
            s: .asciz "hi\n"
            t: .ascii "ok"
        """))
        self.assertEqual(program.symbols["a"], DATA_BASE)
        self.assertEqual(program.symbols["b"], DATA_BASE + 4)
        self.assertEqual(program.symbols["c"], DATA_BASE + 8)
        self.assertEqual(program.symbols["s"], DATA_BASE + 10)
        self.assertEqual(program.symbols["t"], DATA_BASE + 14)
        self.assertEqual(
            program.data,
            bytes([1, 2, 0, 0, 0x44, 0x33, 0x22, 0x11, 0xFE, 0xFF])
            + b"hi\n\0" + b"ok",
        )

    def test_label_on_previous_line_gets_aligned_address(self):
        program = assemble(".data\n.byte 1\nx:\n.word 5")
        self.assertEqual(program.symbols["x"], DATA_BASE + 4)

    def test_space_align_and_word_label(self):
        program = assemble(textwrap.dedent("""
            .data
            buf:  .space 3
                  .align 3
            ptr:  .word buf, 0xFFFFFFFF, -1
            .text
            .globl main
            main: nop
        """))
        self.assertEqual(program.symbols["ptr"], DATA_BASE + 8)
        self.assertEqual(program.data[8:12], DATA_BASE.to_bytes(4, "little"))
        self.assertEqual(program.data[12:20], b"\xff" * 8)
        self.assertEqual(program.symbols["main"], 0)

    def test_hash_inside_string_is_not_a_comment(self):
        program = assemble('.data\ns: .string "a#b" # real comment')
        self.assertEqual(program.data, b"a#b\0")

    def test_sections_can_alternate(self):
        program = assemble(".data\nx: .word 1\n.text\nnop\n.data\ny: .word 2\n.text\nz: nop")
        self.assertEqual(program.symbols["y"], DATA_BASE + 4)
        self.assertEqual(program.symbols["z"], 4)

    def test_value_ranges(self):
        assemble(".data\n.byte -128, 255\n.half -32768, 65535")
        with self.assertRaisesRegex(AssemblerError, "line 2: value 256"):
            assemble(".data\n.byte 256")
        with self.assertRaisesRegex(AssemblerError, "value 65536"):
            assemble(".data\n.half 65536")


class ErrorTests(unittest.TestCase):
    def assertLineError(self, source: str, line: int, fragment: str) -> None:
        with self.assertRaises(AssemblerError) as ctx:
            assemble(source)
        self.assertEqual(ctx.exception.line, line)
        self.assertIn(fragment, str(ctx.exception))
        self.assertTrue(str(ctx.exception).startswith(f"line {line}:"))

    def test_unknown_instruction(self):
        self.assertLineError("nop\nnop\nfoo t0, t1", 3, "unknown instruction 'foo'")

    def test_bad_register(self):
        self.assertLineError("nop\nadd t0, t9, t1", 2, "invalid register 't9'")

    def test_out_of_range_immediate(self):
        self.assertLineError("addi t0, t0, 4096", 1, "out of range")
        self.assertLineError("nop\nslli t0, t0, 40", 2, "shift amount")

    def test_branch_target_out_of_range(self):
        source = "target: nop\n" + "nop\n" * 1100 + "beq t0, t1, target"
        self.assertLineError(source, 1102, "branch offset")

    def test_undefined_and_duplicate_labels(self):
        self.assertLineError("j nowhere", 1, "undefined label 'nowhere'")
        self.assertLineError("x: nop\nx: nop", 2, "duplicate label 'x'")

    def test_instruction_in_data_section(self):
        self.assertLineError(".data\nadd t0, t0, t0", 2, ".text")

    def test_unknown_directive(self):
        self.assertLineError(".data\n.quad 1", 2, "unknown directive '.quad'")

    def test_wrong_arity(self):
        self.assertLineError("mv a0", 1, "expects 2")


if __name__ == "__main__":
    unittest.main()
