"""Command-line interface.

    python3 -m rvsim run prog.s [--regs] [--max-steps N]
    python3 -m rvsim encode "beq t0, t1, 42" ["addi sp, sp, -16" ...]
    python3 -m rvsim decode 0x02628563 [...]
    python3 -m rvsim build prog.s -o out_dir      # text.hex, data.hex, meta.txt
    python3 -m rvsim trace prog.s [-o trace.txt] [--output out.txt] [--max-steps N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .assembler import AssemblerError, assemble
from .cpu import CPU, DEFAULT_MAX_STEPS, SimulationError
from .encoding import EncodingError, decode
from .hexfile import write_build
from .trace import DEFAULT_TRACE_STEPS, trace_program


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        source = Path(args.file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        cpu = CPU(assemble(source)).run(args.max_steps)
    except (AssemblerError, SimulationError) as exc:
        print(f"{args.file}: error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(cpu.output)
    if cpu.output and not cpu.output.endswith("\n"):
        print()
    if args.regs:
        print(cpu.dump_registers())
    status = f"exit code {cpu.exit_code}" if cpu.exit_code is not None else "end of program"
    print(f"[halted after {cpu.steps} steps: {status}]", file=sys.stderr)
    return cpu.exit_code or 0


def _cmd_encode(args: argparse.Namespace) -> int:
    for text in args.instructions:
        try:
            program = assemble(text)
        except AssemblerError as exc:
            print(f"error: {exc.message} in '{text}'", file=sys.stderr)
            return 1
        for word in program.words:
            print(f"0x{word:08X}  {decode(word)}")
    return 0


def _cmd_decode(args: argparse.Namespace) -> int:
    for token in args.words:
        try:
            word = int(token, 16 if not token.lower().startswith(("0x", "0b")) else 0)
            print(f"0x{word:08X}  {decode(word)}")
        except (ValueError, EncodingError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0


def _assemble_file(path: str):
    """Read and assemble a source file; print the error and return None on failure."""
    try:
        return assemble(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
    except AssemblerError as exc:
        print(f"{path}: error: {exc}", file=sys.stderr)
    return None


def _cmd_build(args: argparse.Namespace) -> int:
    program = _assemble_file(args.file)
    if program is None:
        return 1
    meta = write_build(program, args.out)
    print(f"{args.out}: {meta['text_words']} text word(s), {meta['data_words']} data word(s), "
          f"entry 0x{meta['entry']:08x}")
    return 0


def _cmd_trace(args: argparse.Namespace) -> int:
    program = _assemble_file(args.file)
    if program is None:
        return 1
    result = trace_program(program, args.max_steps)
    if args.o:
        Path(args.o).write_text(result.text(), encoding="utf-8")
    else:
        sys.stdout.write(result.text())
    if args.output:
        Path(args.output).write_text(result.output, encoding="utf-8")
    print(f"[{len(result.commits)} instructions retired: {result.end}]", file=sys.stderr)
    return 0 if result.end.startswith("HALT") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m rvsim", description="RV32I assembler and simulator")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="assemble and execute a program")
    run.add_argument("file", help="assembly source file")
    run.add_argument("--regs", action="store_true", help="print all registers at the end")
    run.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, metavar="N",
                     help=f"abort after N instructions (default {DEFAULT_MAX_STEPS})")
    run.set_defaults(func=_cmd_run)

    enc = sub.add_parser("encode", help="print the machine code of instructions")
    enc.add_argument("instructions", nargs="+", help='e.g. "beq t0, t1, 42" (pseudo-instructions allowed)')
    enc.set_defaults(func=_cmd_encode)

    dec = sub.add_parser("decode", help="disassemble machine words")
    dec.add_argument("words", nargs="+", help="hex words, e.g. 0x02628563")
    dec.set_defaults(func=_cmd_decode)

    bld = sub.add_parser("build", help="write $readmemh memory images for the Verilog testbench")
    bld.add_argument("file", help="assembly source file")
    bld.add_argument("-o", "--out", required=True, metavar="DIR", help="output directory")
    bld.set_defaults(func=_cmd_build)

    trc = sub.add_parser("trace", help="print the commit trace (one line per retired instruction)")
    trc.add_argument("file", help="assembly source file")
    trc.add_argument("-o", metavar="FILE", help="write the trace to FILE instead of stdout")
    trc.add_argument("--output", metavar="FILE", help="write the program's printed output to FILE")
    trc.add_argument("--max-steps", type=int, default=DEFAULT_TRACE_STEPS, metavar="N",
                     help=f"stop after N instructions (default {DEFAULT_TRACE_STEPS})")
    trc.set_defaults(func=_cmd_trace)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
