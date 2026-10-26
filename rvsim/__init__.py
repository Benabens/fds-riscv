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

__all__ = [
    "AssemblerError",
    "CPU",
    "DATA_BASE",
    "EncodingError",
    "Memory",
    "Program",
    "STACK_TOP",
    "SimulationError",
    "StepLimitExceeded",
    "TEXT_BASE",
    "assemble",
    "decode",
    "decode_fields",
    "encode",
    "encode_fields",
    "run_source",
]
