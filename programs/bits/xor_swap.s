# =============================================================================
#  xor_swap.s  -  exchange two registers with three XORs, no temporary
# =============================================================================
#
#  WHAT IT COMPUTES
#    Given A in a0 and B in a1, returns B in a0 and A in a1, without using a
#    third register as scratch space.
#
#  ALGORITHM
#    XOR (exclusive or) has four properties that make the trick work:
#        x ^ 0 = x          (0 is the neutral element)
#        x ^ x = 0          (every value is its own inverse)
#        associative and commutative
#    Writing ^ for XOR:
#        a0 <- A ^ B
#        a1 <- (A ^ B) ^ B  = A ^ (B ^ B) = A ^ 0 = A
#        a0 <- (A ^ B) ^ A  = B ^ (A ^ A) = B ^ 0 = B
#    Each step works bit by bit and independently on all 32 positions, so the
#    swap is correct for every bit pattern (signed, unsigned, pointers...).
#
#    Pitfall: if both operands are the SAME storage location (for example a
#    memory version of this routine called with two equal pointers), the first
#    step computes A ^ A = 0 and the value is destroyed. With two distinct
#    registers, as here, that cannot happen.
#    On a real pipeline the three XORs depend on each other, so a plain swap
#    through a temporary register (mv t0,a0 / mv a0,a1 / mv a1,t0) is never
#    slower; the trick matters when no free register exists.
#
#  REGISTER CONVENTION
#    input : a0 = A, a1 = B
#    output: a0 = B, a1 = A
#    uses  : nothing else (no temporaries, no stack)
#
#  COURSE LINK
#    Boolean algebra: XOR is addition modulo 2 (in each bit position), the
#    operation computed by the sum output of a half adder. The swap is a
#    direct application of the algebraic identities of XOR.
# =============================================================================

        .text
        .globl  xor_swap

xor_swap:
        xor     a0, a0, a1          # a0 = A ^ B
        xor     a1, a0, a1          # a1 = (A ^ B) ^ B = A
        xor     a0, a0, a1          # a0 = (A ^ B) ^ A = B
        ret

# -----------------------------------------------------------------------------
#  Demo: swap 7 and -42, print the two registers ("-42 7"), exit.
# -----------------------------------------------------------------------------
        .globl  _start
_start:
        li      a0, 7
        li      a1, -42
        call    xor_swap
        mv      s0, a0              # keep the results visible with --regs
        mv      s1, a1

        mv      a0, s0              # print a0 (expected -42)
        li      a7, 1
        ecall
        li      a0, ' '
        li      a7, 11
        ecall
        mv      a0, s1              # print a1 (expected 7)
        li      a7, 1
        ecall
        li      a0, '\n'
        li      a7, 11
        ecall

        li      a7, 10              # exit
        ecall
