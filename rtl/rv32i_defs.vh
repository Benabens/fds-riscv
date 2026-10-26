// =============================================================================
// rv32i_defs.vh -- constants shared by the RV32I single-cycle processor
// =============================================================================
//
// Included by every RTL module that decodes instructions or drives the
// datapath multiplexers, so that the encodings live in a single place.
// Compile with "-I rtl" (or the directory that holds this file).
// =============================================================================

`ifndef RV32I_DEFS_VH
`define RV32I_DEFS_VH

// ---- Major opcodes: instruction bits [6:0] ---------------------------------
`define OPC_LUI      7'b0110111
`define OPC_AUIPC    7'b0010111
`define OPC_JAL      7'b1101111
`define OPC_JALR     7'b1100111
`define OPC_BRANCH   7'b1100011
`define OPC_LOAD     7'b0000011
`define OPC_STORE    7'b0100011
`define OPC_OP_IMM   7'b0010011
`define OPC_OP       7'b0110011
`define OPC_MISC_MEM 7'b0001111   // FENCE
`define OPC_SYSTEM   7'b1110011   // ECALL, EBREAK

// The two SYSTEM instructions of the base ISA have a single fixed encoding.
`define INSTR_ECALL  32'h0000_0073
`define INSTR_EBREAK 32'h0010_0073

// ---- ALU operations ---------------------------------------------------------
// The 4-bit code is {alt, funct3}: funct3 of the OP / OP-IMM instruction
// selects the operation family and "alt" (instruction bit 30) distinguishes
// SUB from ADD and SRA from SRL.  The ALU control therefore only has to
// pass these bits through for register and immediate arithmetic, and to
// force ADD for address computations (loads, stores, jalr, lui, auipc).
`define ALU_ADD  4'b0_000
`define ALU_SUB  4'b1_000
`define ALU_SLL  4'b0_001
`define ALU_SLT  4'b0_010
`define ALU_SLTU 4'b0_011
`define ALU_XOR  4'b0_100
`define ALU_SRL  4'b0_101
`define ALU_SRA  4'b1_101
`define ALU_OR   4'b0_110
`define ALU_AND  4'b0_111

// ---- Immediate formats (imm_gen select) -----------------------------------
`define IMM_I 3'd0
`define IMM_S 3'd1
`define IMM_B 3'd2
`define IMM_U 3'd3
`define IMM_J 3'd4

// ---- ALU operand A source ----------------------------------------------------
`define SRCA_RS1  2'd0   // register rs1 (arithmetic, address computation)
`define SRCA_PC   2'd1   // program counter (auipc)
`define SRCA_ZERO 2'd2   // constant 0 (lui: result = 0 + immediate)

// ---- ALU operand B source ----------------------------------------------------
`define SRCB_RS2 1'b0
`define SRCB_IMM 1'b1

// ---- Register write-back source ----------------------------------------------
`define WB_ALU 2'd0   // ALU result
`define WB_MEM 2'd1   // load data, already aligned and extended
`define WB_PC4 2'd2   // return address pc + 4 (jal, jalr)

// ---- Memory access size, as encoded by funct3[1:0] of loads and stores ------
`define SIZE_BYTE 2'd0
`define SIZE_HALF 2'd1
`define SIZE_WORD 2'd2

// ---- Trap causes reported to the testbench ---------------------------------
`define TRAP_NONE      3'd0
`define TRAP_ILLEGAL   3'd1   // not a valid RV32I encoding
`define TRAP_MISALIGN  3'd2   // halfword / word access at a misaligned address
`define TRAP_JUMP      3'd3   // taken branch or jump to an address that is not 4-byte aligned
`define TRAP_BUS       3'd4   // data access outside every memory region (or a store to ROM)

`endif
