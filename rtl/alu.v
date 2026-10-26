// =============================================================================
// alu.v -- 32-bit arithmetic and logic unit of the RV32I processor
// =============================================================================
//
// Purely combinational.  One unit serves every instruction that computes a
// value: register and immediate arithmetic, address computation for loads,
// stores and jalr (always ADD), lui (0 + immediate) and auipc (pc + immediate).
//
// Points worth noting:
//   * SLT compares the operands as signed two's-complement numbers, SLTU as
//     unsigned ones; both return 0 or 1.
//   * Shifts use only b[4:0]: the RISC-V specification ignores the upper bits
//     of the shift amount for 32-bit registers.
//   * SRA must replicate the sign bit, so its operand is declared signed and
//     the arithmetic shift operator ">>>" is used.
// =============================================================================
`timescale 1ns / 1ps
`include "rv32i_defs.vh"

module alu (
    input  wire [3:0]  alu_op,   // operation, see `ALU_* in rv32i_defs.vh
    input  wire [31:0] a,        // operand A
    input  wire [31:0] b,        // operand B
    output reg  [31:0] result
);

    wire [4:0] shamt = b[4:0];

    // Signed views of the operands for SLT and SRA.
    wire signed [31:0] a_signed = a;
    wire signed [31:0] b_signed = b;

    always @(*) begin
        case (alu_op)
            `ALU_ADD:  result = a + b;
            `ALU_SUB:  result = a - b;
            `ALU_SLL:  result = a << shamt;
            `ALU_SLT:  result = {31'd0, a_signed < b_signed};
            `ALU_SLTU: result = {31'd0, a < b};
            `ALU_XOR:  result = a ^ b;
            `ALU_SRL:  result = a >> shamt;
            `ALU_SRA:  result = a_signed >>> shamt;
            `ALU_OR:   result = a | b;
            `ALU_AND:  result = a & b;
            default:   result = 32'd0;
        endcase
    end

endmodule
