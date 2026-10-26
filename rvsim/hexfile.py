"""Memory images for hardware simulation.

``write_build`` turns an assembled :class:`~rvsim.assembler.Program` into the
files a Verilog testbench loads with ``$readmemh``:

* ``text.hex`` -- the instruction ROM, one 32-bit word per line, word 0 at
  address ``TEXT_BASE``;
* ``data.hex`` -- the initial contents of the data RAM, word 0 at
  ``DATA_BASE``;
* ``meta.txt`` -- ``key value`` lines: entry pc, section sizes in bytes,
  number of words in each hex file, and the address at which the program
  halts by running off the end of ``.text``.

Each word is assembled from four consecutive bytes in little-endian order
(the byte at the lowest address becomes bits 7:0), which is exactly how the
RAM stores it.  Sections are padded with zero bytes to a whole number of
words, and an empty section still produces one zero word so that every file
is a valid, non-empty ``$readmemh`` image.
"""

from __future__ import annotations

from pathlib import Path

from .assembler import Program


def hex_words(image: bytes) -> list[str]:
    """Split ``image`` into little-endian 32-bit words, formatted as 8 hex digits."""
    padded = image + bytes(-len(image) % 4)
    if not padded:
        padded = bytes(4)
    return [f"{int.from_bytes(padded[i:i + 4], 'little'):08x}" for i in range(0, len(padded), 4)]


def build_meta(program: Program) -> dict[str, int]:
    return {
        "entry": program.entry,
        "text_base": program.text_base,
        "text_size": len(program.text),
        "text_words": len(hex_words(program.text)),
        "text_end": program.text_base + len(program.text),
        "data_base": program.data_base,
        "data_size": len(program.data),
        "data_words": len(hex_words(program.data)),
    }


def write_build(program: Program, out_dir: str | Path) -> dict[str, int]:
    """Write ``text.hex``, ``data.hex`` and ``meta.txt`` into ``out_dir``; return the metadata."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "text.hex").write_text("\n".join(hex_words(program.text)) + "\n", encoding="ascii")
    (out / "data.hex").write_text("\n".join(hex_words(program.data)) + "\n", encoding="ascii")
    meta = build_meta(program)
    (out / "meta.txt").write_text(
        "".join(f"{key} 0x{value:08x}\n" for key, value in meta.items()), encoding="ascii"
    )
    return meta


def read_meta(path: str | Path) -> dict[str, int]:
    """Parse a ``meta.txt`` written by :func:`write_build`."""
    meta = {}
    for line in Path(path).read_text(encoding="ascii").splitlines():
        if line.strip():
            key, value = line.split()
            meta[key] = int(value, 0)
    return meta
