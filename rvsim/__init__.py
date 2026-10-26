"""rvsim: a small RV32I assembler and simulator.

Quick start::

    from rvsim import run_source

    cpu = run_source('''
        li   a0, 6
        li   a1, 7
        add  a2, a0, a1
    ''')
    assert cpu.reg("a2") == 13
"""

from .assembler import DATA_BASE, TEXT_BASE, AssemblerError, Program, assemble
from .cpu import CPU, STACK_TOP, Memory, SimulationError, StepLimitExceeded, run_source
from .encoding import EncodingError, decode, decode_fields, encode, encode_fields
from .hexfile import hex_words, read_meta, write_build
from .trace import Commit, Trace, trace_program, trace_source

__all__ = [
    "AssemblerError",
    "CPU",
    "Commit",
    "DATA_BASE",
    "EncodingError",
    "Memory",
    "Program",
    "STACK_TOP",
    "SimulationError",
    "StepLimitExceeded",
    "TEXT_BASE",
    "Trace",
    "assemble",
    "decode",
    "decode_fields",
    "encode",
    "encode_fields",
    "hex_words",
    "read_meta",
    "run_source",
    "trace_program",
    "trace_source",
    "write_build",
]
