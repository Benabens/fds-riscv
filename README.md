# rv32i-cpu

**A RISC-V processor in Verilog, in two versions (single-cycle and multicycle), that runs real assembly programs.** It is checked instruction by instruction against an assembler and simulator I wrote from scratch.

A personal project built on the computer-architecture chapter of **CS-173: Fundamentals of Digital Systems** (EPFL, second year). The course draws the single-cycle and multicycle RV32I datapaths on paper; this repository builds both, writes the programs they run, and proves they agree with a reference model. It follows [fds-digital-logic](https://github.com/Benabens/fds-digital-logic), which covers the digital-logic half of the same course.

> `RV32I base ISA` · Verilog-2005 (Icarus Verilog) · Python 3.10+ standard library only · zero lint warnings · 1.1 M instructions co-simulated per run

---

## It runs this

The Mandelbrot set, computed by the Verilog CPU in Q22.10 fixed point. RV32I has no multiply instruction, so the program multiplies with a table of quarter squares that it builds itself at start-up.

```
             ...........:::::::::::::::::::::::::----------::::::.............
            .........:::::::::::::::::::::::------==+*+===-----::::::.........
           .......::::::::::::::::::::::--------=====*#*+**=------::::::......
          .....::::::::::::::::::::::----------====+++*##*+===------::::::....
         ....::::::::::::::::::::::----------=====+*##@@%***====------::::::..
        ...::::::::::::::::::::::---------======++*#@@@@@@%*+======----:::::::
       ...::::::::::::::::::::---------===+++++++**#@@@@@@#*+++=======--::::::
      ..::::::::::::::::::::-------=====+*@@#**@@#@@@@@@@@@%@#@*+++*@+=--:::::
      .::::::::::::::::::----===========+*@@@@@@@@@@@@@@@@@@@@@@#@@@@#==--::::
     .::::::::::::::::---=============++**#@@@@@@@@@@@@@@@@@@@@@@@@@%+===--:::
     ::::::::::::----=++===========+++*#@@@@@@@@@@@@@@@@@@@@@@@@@@@%#++==---::
    .::::::-------=====#*++++%#++++++*@%@@@@@@@@@@@@@@@@@@@@@@@@@@@@%@#*=---::
    ::::--------======++*%@%##@%##****@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#*+=----:
    :---------======+++**#@@@@@@@@@@##@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@%==----:
   :--------======+%***#%@@@@@@@@@@@@%@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@%+==----:
   --==========+++**%%@%@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@%+===----:
   @@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#*++===----:
   --==========+++**%%@%@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@%+===----:
   :--------======+%***#%@@@@@@@@@@@@%@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@%+==----:
    :---------======+++**#@@@@@@@@@@##@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@%==----:
    ::::--------======++*%@%##@%##****@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#*+=----:
    .::::::-------=====#*++++%#++++++*@%@@@@@@@@@@@@@@@@@@@@@@@@@@@@%@#*=---::
     ::::::::::::----=++===========+++*#@@@@@@@@@@@@@@@@@@@@@@@@@@@%#++==---::
     .::::::::::::::::---=============++**#@@@@@@@@@@@@@@@@@@@@@@@@@%+===--:::
      .::::::::::::::::::----===========+*@@@@@@@@@@@@@@@@@@@@@@#@@@@#==--::::
      ..::::::::::::::::::::-------=====+*@@#**@@#@@@@@@@@@%@#@*+++*@+=--:::::
       ...::::::::::::::::::::---------===+++++++**#@@@@@@#*+++=======--::::::
        ...::::::::::::::::::::::---------======++*#@@@@@@%*+======----:::::::
         ....::::::::::::::::::::::----------=====+*##@@%***====------::::::..
          .....::::::::::::::::::::::----------====+++*##*+===------::::::....
           .......::::::::::::::::::::::--------=====*#*+**=------::::::......
            .........:::::::::::::::::::::::------==+*+===-----::::::.........
             ...........:::::::::::::::::::::::::----------::::::.............
```

952,702 instructions: 952,702 cycles on the single-cycle core and 3,786,515 on the multicycle core (CPI 3.97). The output is byte-identical to the reference simulator's, and so is every register write along the way.

Two more demos ship with it. `primes.s` is a sieve of Eratosthenes up to 2000, with right-aligned printing and no division. `automata.s` runs elementary cellular automata (rules 90 and 30), with 32 cells updated per instruction using bitwise logic:

```
Rule 90: new cell = left XOR right  (Pascal's triangle mod 2)
                               #
                              # #
                             #   #
                            # # # #
                           #       #
                          # #     # #
                         #   #   #   #
                        # # # # # # # #
                       #               #
                      # #             # #
                     #   #           #   #
                    # # # #         # # # #
                   #       #       #       #
                  # #     # #     # #     # #
                 #   #   #   #   #   #   #   #
                # # # # # # # # # # # # # # # #
```

## Try it

```bash
python3 -m unittest discover -s tests -t .                 # everything: toolchain, programs, both cores

python3 sim/cosim.py programs/demo/mandelbrot.s --core multi   # run a program on the Verilog CPU and compare
python3 sim/run_all.py                                     # full co-simulation campaign + CPI table

python3 -m rvsim run programs/demo/primes.s                # the reference simulator
python3 -m rvsim encode "beq t0, t1, 42"                   # 0x02628563
```

Requires Python 3.10+ and [Icarus Verilog](https://steveicarus.github.io/iverilog/) (`brew install icarus-verilog` / `apt install iverilog`). Nothing else.

## The processor — `rtl/`

Both cores share the same building blocks and the same external interface. They differ only in how an instruction travels through the datapath.

| Module | Role |
|---|---|
| `alu.v` | add/sub, the shifts (logical and arithmetic, shift amount taken from the low 5 bits), slt/sltu, and/or/xor |
| `regfile.v` | 32 × 32-bit registers, two read ports, one write port, `x0` hard-wired to zero |
| `imm_gen.v` | rebuilds the immediate of the I, S, B, U and J formats, including the scrambled branch/jump bit fields |
| `control.v` | main decoder: opcode/funct3/funct7 → datapath control signals, illegal-instruction detection |
| `branch_unit.v` | the six branch conditions |
| `load_store_unit.v` | byte/half/word access with sign or zero extension, byte enables, misalignment detection |
| `memory.v` | instruction ROM (16 KiB at `0x0`), data RAM (64 KiB at `0x10000000`), stack (64 KiB below `0x80000000`) |
| `cpu_single_cycle.v` | one instruction per clock cycle (CPI = 1) |
| `multicycle_control.v` + `cpu_multi_cycle.v` | finite-state machine, one datapath step per cycle, shared ALU |

### Multicycle core

Each instruction takes only the steps it needs: FETCH → DECODE → EXECUTE → MEMORY → WRITEBACK. The ALU is reused across steps (it computes `pc + imm` during DECODE and the operation or address during EXECUTE). The datapath registers IR, A, B, ALUOut and MDR hold values between cycles.

| Instruction class | States | Cycles |
|---|---|---|
| `fence`, `ecall`, `ebreak` | F D | 2 |
| branches, `jal` | F D E / F D W | 3 |
| ALU ops, `lui`, `auipc`, `jalr`, stores | F D E W / F D E M | 4 |
| loads | F D E M W | 5 |

The state diagram is drawn in the header of `multicycle_control.v`. Measured CPI on real programs:

| Program | Instructions | Single-cycle cycles | Multicycle cycles | CPI |
|---|---:|---:|---:|---:|
| `arith/isqrt` | 333 | 333 | 1,186 | 3.56 |
| `arith/gcd` | 863 | 863 | 3,133 | 3.63 |
| `control/bubble_sort` | 338 | 338 | 1,264 | 3.74 |
| `functions/hanoi` | 359 | 359 | 1,413 | 3.94 |
| `functions/fibonacci` | 22,788 | 22,788 | 92,102 | 4.04 |
| `demo/mandelbrot` | 952,702 | 952,702 | 3,786,515 | 3.97 |

Recursive code is the most expensive (CPI ≈ 4), because saving and restoring registers on the stack is all 5-cycle loads and 4-cycle stores. Pure arithmetic loops, where branches only cost 3 cycles, come out cheapest. The multicycle core still has a shorter critical path per cycle, which is the whole trade-off the course discusses.

## How it is verified — `sim/`

**Co-simulation, one instruction at a time.** The testbench (`tb_cpu.v`) loads a program image and logs a *commit trace*: for every retired instruction, its pc, the machine word, the register written and its value, and any memory store. `cosim.py` runs the same program in the reference simulator and compares the two traces line by line. The first divergence is reported with the disassembled instruction, which makes a wrong mux select or a mis-sign-extended load obvious within seconds.

`run_all.py` runs this comparison on three sources of programs, for both cores:

- **all the programs in `programs/`** (below), 37 of them;
- **26 directed tests** for corner cases: writes to `x0`, sign and zero extension of every load, far jumps and branches, `lui`/`auipc`/`jalr`, and every trap. The traps covered are misaligned loads, stores and jumps, jumps out of the program, bad `ecall`s, and 12 illegal encodings (`csrrw`, an M-extension `mul`, `fence.i`, a wrong `funct7`, the all-zero word…);
- **250 random programs** from fixed seeds. They start from register values biased towards 0, −1, `INT_MIN` and `INT_MAX`, and run every ALU operation, loads and stores of every width and alignment, and forward branches and jumps, including `jalr` with an odd target.

Each campaign compares 1,134,579 retired instructions per core and finds zero mismatches.

**Mutation testing.** `mutation_check.py` injects 23 known bugs into copies of the RTL and checks that the campaign catches every one. Examples: `sltu` computed as signed, `sra` as a logical shift, `lb` zero-extending, `jalr` not clearing bit 0, `x0` writable, a load skipping writeback, the pc updated in the wrong FSM state, a store issued twice, operands latched one state too early. 23 of 23 are caught.

## The toolchain — `rvsim/`

Written from scratch, it is the reference model and the build system for the CPU.

| Module | What it does |
|---|---|
| `assembler.py` | two-pass assembler: labels, `.text`/`.data`, `.word .half .byte .ascii .asciz .space .align`, and the usual pseudo-instructions (`li` with correct `lui`/`addi` splitting, `la`, `call`, `ret`, `mv`, `beqz`, `bgt`…) |
| `encoding.py` | encodes and decodes every RV32I instruction, checked against encodings from the RISC-V specification |
| `cpu.py` | instruction-set simulator on little-endian, byte-addressable memory, with misaligned-access detection and `ecall` services (print, exit) |
| `trace.py`, `hexfile.py` | commit-trace output and `$readmemh` memory images for the testbench |

## The programs — `programs/`

These programs are both what the CPU runs and the CPU's test suite. Each one is a set of routines following the standard calling convention, and each has its own test file that calls those routines directly with hundreds of inputs. Expected values always come from plain Python (`math.isqrt`, `math.gcd`, `sorted`, `bisect`…), never from the assembly itself, and the test harness fails any routine that does not restore `s0`–`s11`.

- **`bits/`**: XOR swap, population count, bit reversal, parity, power-of-two test, count leading zeros, rotations, byte swap.
- **`arith/`**: branch-free absolute value, signed-overflow detection, 64-bit addition with the carry rebuilt by `sltu`, shift-and-add multiplication, restoring division, GCD, integer square root, Q16.16 multiplication.
- **`memory/`**: array sums and min/max, matrix maximum and transpose, `memcpy` (word-wise when aligned), `strlen`, string reversal, upper-casing.
- **`control/`**: bubble sort with early exit, binary search for the first occurrence, character counting.
- **`functions/`**: recursive factorial, Fibonacci (iterative and recursive), Towers of Hanoi, digit sum with a hand-written divide-by-10.
- **`demo/`**: Mandelbrot, prime sieve, cellular automata.

## Layout

```
rtl/        the processor: shared blocks, single-cycle core, multicycle core + FSM
sim/        testbench, co-simulation, directed and random tests, mutation testing
rvsim/      assembler, encoder, reference simulator, command-line interface
programs/   assembly programs: the CPU's workload and test suite
tests/      unit tests for everything above
```

## Context

CS-173 is taught by Prof. Mirjana Stojilović at EPFL. Its final chapter introduces computer architecture through RV32I: the instruction set, assembly programming, and the single-cycle and multicycle implementations of the processor. This project takes that chapter from the whiteboard to working, verified hardware.
