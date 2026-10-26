# =============================================================================
# memcpy.s -- copy n bytes from one buffer to another
# =============================================================================
#
# What it computes
#   memcpy(a0 = dst, a1 = src, a2 = n)  ->  a0 = dst (like the C library)
#   Copies bytes src[0..n-1] to dst[0..n-1]. n = 0 copies nothing. The two
#   regions must not overlap (that would be memmove). Any alignment works.
#
# Algorithm: word fast path + byte tail
#   RV32I requires (in this simulator, as on many real cores) that `lw`/`sw`
#   addresses be multiples of 4; a misaligned word access traps. `lb`/`sb`
#   work at any address but move only one byte per instruction.
#   So the routine:
#     1. checks whether dst and src are BOTH word aligned, by testing the two
#        low bits of (dst OR src) with `andi ..., 3`;
#     2. if so, copies 4 bytes per iteration with lw/sw while n >= 4;
#     3. finishes the remaining 0..3 bytes (or the whole buffer if the
#        pointers were misaligned) one byte at a time with lbu/sb.
#   Bytes are moved with `lbu` (zero extension) -- with a plain copy either
#   lb or lbu works since sb stores only the low 8 bits.
#
# Register convention (leaf routine)
#   in : a0 = dst, a1 = src, a2 = n (unsigned)
#   out: a0 = dst (unchanged), dst[0..n-1] written, nothing beyond it
#   t0 = write pointer, t1 = scratch / data, t2 = constant 4
#
# Course link
#   Byte vs word loads/stores, alignment constraints, little-endian memory
#   (a word copy moves 4 bytes in the same order as 4 byte copies).
# =============================================================================

        .text

# -----------------------------------------------------------------------------
# memcpy(a0 = dst, a1 = src, a2 = n) -> a0 = dst
# -----------------------------------------------------------------------------
memcpy:
        mv      t0, a0              # t0 = write pointer (a0 is kept to return it)
        or      t1, a0, a1
        andi    t1, t1, 3           # low two bits of dst | src
        bnez    t1, mc_bytes        # one of them is misaligned: bytes only
        li      t2, 4
mc_words:
        bltu    a2, t2, mc_bytes    # fewer than 4 bytes left
        lw      t1, 0(a1)
        sw      t1, 0(t0)
        addi    a1, a1, 4
        addi    t0, t0, 4
        addi    a2, a2, -4
        j       mc_words
mc_bytes:
        beqz    a2, mc_done         # handles n = 0 and the empty tail
        lbu     t1, 0(a1)
        sb      t1, 0(t0)
        addi    a1, a1, 1
        addi    t0, t0, 1
        addi    a2, a2, -1
        j       mc_bytes
mc_done:
        ret

# -----------------------------------------------------------------------------
# Demo: copy a 14-byte string (including its NUL) and print the copy.
# -----------------------------------------------------------------------------
        .data
demo_src:
        .asciz  "Hello, RV32I!"
        .align  2
demo_dst:
        .space  16

        .text
_start:
        la      a0, demo_dst
        la      a1, demo_src
        li      a2, 14
        call    memcpy
        li      a7, 4               # print the copy (a0 = dst)
        ecall
        li      a7, 10
        ecall
