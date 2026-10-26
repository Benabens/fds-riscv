# fds-riscv

**RV32I assembly programs, and the assembler and simulator that run them** — written in pure Python and RISC-V assembly, with every program checked against an independent reference.

A personal project built around the RISC-V part of **CS-173 — Fundamentals of Digital Systems** (EPFL, second year). It is not a graded project: it takes the course's computer-architecture chapter (the RV32I instruction set, memory access, branches, the calling convention) and goes well beyond the exercise sessions. It is the companion of [fds-digital-logic](https://github.com/Benabens/fds-digital-logic), which covers the digital-logic part of the same course in Verilog.

> `RV32I base only — no M extension` · Python 3.10+ standard library only · assembler + simulator + encoder · systematic tests

> **Status:** the toolchain is complete; the programs below are being added to this repository progressively, each one together with its tests.

---

## Try it

```bash
python3 -m unittest discover -s tests -t .           # the whole test suite

python3 -m rvsim run programs/functions/hanoi.s       # run a program's demo
python3 -m rvsim run programs/arith/divide.s --regs   # ...and dump the registers
python3 -m rvsim encode "beq t0, t1, 42"              # 0x02628563
python3 -m rvsim decode 0x00C9A383                    # lw t2, 12(s3)
```

No installation and no dependency: everything runs on the Python standard library.

## The toolchain — `rvsim/`

| Module | What it does |
|---|---|
| `assembler.py` | Two-pass assembler: labels, `.text` / `.data`, `.word` `.half` `.byte` `.ascii` `.asciz` `.space` `.align`, and the usual pseudo-instructions (`li` with correct `lui`/`addi` splitting, `la`, `call`, `ret`, `mv`, `not`, `neg`, `beqz`, `bgt`…). Errors report the source line. |
| `encoding.py` | Encodes every RV32I instruction to its 32-bit word and decodes it back, across the six formats (R, I, S, B, U, J), including the scrambled immediate fields of branches and jumps. |
| `cpu.py` | Simulator executing the actual machine words fetched from little-endian, byte-addressable memory: 32 registers with `x0` hard-wired to zero, misaligned-access detection, a few `ecall` services (print, exit). |

The encoder is tested against encodings taken straight from the RISC-V specification, and the ALU against reference arithmetic with explicit 32-bit masking — including the classic traps: `sra` vs `srl` on negative numbers, `slt` vs `sltu`, shift amounts that only use the low five bits.

## The programs — `programs/`

Every program is a set of routines that follow the standard calling convention (arguments in `a0`–`a7`, result in `a0`/`a1`, callee-saved registers preserved), plus a short `_start` demo.

**`bits/`** — bit manipulation: swapping two registers with three XORs, population count, bit reversal, parity, power-of-two test, count leading zeros, rotations, byte swap (endianness).

**`arith/`** — arithmetic with nothing but the base instruction set: absolute value without a branch or a subtraction, signed-overflow detection from the operand and result signs, 64-bit addition with the carry rebuilt by `sltu`, shift-and-add multiplication, restoring division (with the divide-by-zero result the RISC-V spec mandates), Euclid's GCD, integer square root, Q16.16 fixed-point multiplication.

**`memory/`** — loads, stores and addressing: array sums (one stopping at the first signed overflow), counting sign mismatches between two arrays, element-wise absolute difference without any `sub`/`neg`, min/max, matrix maximum with its row and column, matrix transpose, `memcpy` (word-at-a-time when aligned, byte-wise otherwise), `strlen`, in-place string reversal, upper-casing.

**`control/`** — loops and branches: bubble sort with early exit, binary search returning the first occurrence, character counting.

**`functions/`** — the stack and recursion: recursive factorial, iterative and recursive Fibonacci (cross-checked), Towers of Hanoi writing its move list to memory, digit sum with a hand-written divide-by-10.

## How the programs are tested

`tests/programs/harness.py` calls a routine *directly* from Python: it assembles the file, points the program counter at the routine's label, loads the arguments, and stops when the routine returns. A single program can therefore be checked against hundreds of inputs rather than the one hard-coded in its demo.

- **Edge cases first**: 0, 1, −1, `INT_MIN`, `INT_MAX`, `0xFFFFFFFF`, empty arrays and strings, single elements, duplicates, already-sorted and reverse-sorted inputs, 1×N and N×1 matrices.
- **Then randomised inputs** with a fixed seed.
- **Independent references only**: the expected value always comes from plain Python (`bin(x).count("1")`, `//` and `%`, `math.isqrt`, `math.gcd`, `sorted`, `bisect`, `fractions.Fraction`…), never from the assembly under test.
- **The calling convention is enforced**: the harness fills `s0`–`s11` with marker values and fails the test if a routine returns without restoring them. Output buffers are followed by a guard word to catch out-of-bounds writes.
- **Mutation testing**: faults were deliberately injected into copies of the programs (a wrong mask, `srai` instead of `srli`, a missing restore, an off-by-one loop) to confirm that the tests catch them.

## Layout

```
rvsim/              assembler, encoder, simulator, command-line interface
programs/bits/      bit manipulation
programs/arith/     arithmetic without the M extension
programs/memory/    arrays, matrices, strings
programs/control/   sorting and searching
programs/functions/ recursion and the stack
tests/              toolchain tests and one test file per program
```

## Context

CS-173 is taught by Prof. Mirjana Stojilović at EPFL. Its final chapter introduces computer architecture through the RV32I instruction set: integer computation, memory access and branches, assembler directives, and single-cycle and multicycle processor implementations. The exercise sessions used a browser-based simulator; this repository rebuilds the toolchain from scratch and applies it to a broader set of problems.
