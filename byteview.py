#!/usr/bin/env python3
"""ByteView - Python Bytecode Disassembler and Control Flow Graph Generator."""

import argparse
import ast
import dis
import json
import os
import sys
from types import CodeType, FunctionType

# ========== Color Output ==========

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"

    @staticmethod
    def colorize(text, color, use_color=True):
        if not use_color or not sys.stdout.isatty():
            return text
        return f"{color}{text}{Colors.RESET}"

    @staticmethod
    def instruction_color(opname, use_color=True):
        if opname.startswith("LOAD_"):
            return Colors.GREEN
        elif opname.startswith("STORE_"):
            return Colors.BLUE
        elif opname.startswith("BINARY_") or opname.startswith("INPLACE_") or opname.startswith("UNARY_"):
            return Colors.MAGENTA
        elif opname.startswith("JUMP_") or opname.startswith("FOR_ITER"):
            return Colors.RED
        elif opname.startswith("CALL_"):
            return Colors.YELLOW
        elif opname.startswith("RETURN_"):
            return Colors.CYAN
        elif "POP" in opname or "PUSH" in opname:
            return Colors.DIM
        else:
            return Colors.WHITE


# ========== Bytecode Disassembly ==========

def compile_source(source_path):
    """Compile a .py file and return the top-level code object."""
    with open(source_path, "r", encoding="utf-8") as f:
        source = f.read()
    return compile(source, source_path, "exec")


def get_instructions(code_obj):
    """Get a list of instruction dicts from a code object."""
    instructions = []
    for instr in dis.get_instructions(code_obj):
        line_no = getattr(instr, 'line_number', None)
        if line_no is None:
            starts = instr.starts_line
            if isinstance(starts, int) and not isinstance(starts, bool):
                line_no = starts
        instructions.append({
            "offset": instr.offset,
            "opname": instr.opname,
            "opcode": instr.opcode,
            "arg": instr.arg,
            "argval": instr.argval,
            "argrepr": instr.argrepr,
            "line": line_no,
            "is_jump_target": instr.is_jump_target,
            "starts_line": instr.starts_line,
        })
    return instructions


def get_all_code_objects(code_obj, prefix=""):
    """Recursively collect all code objects (nested functions, methods, etc.)."""
    results = []
    name = prefix or "<module>"
    results.append((name, code_obj))

    for const in code_obj.co_consts:
        if isinstance(const, CodeType):
            inner_name = f"{name}.{const.co_name}"
            results.extend(get_all_code_objects(const, inner_name))

    return results


def find_function(code_obj, func_name):
    """Find a specific function by name (recursively)."""
    all_codes = get_all_code_objects(code_obj)
    for name, co in all_codes:
        if name == func_name or co.co_name == func_name or name.endswith("." + func_name):
            return name, co
    return None, None


def disassemble(code_obj, name="<module>", use_color=True):
    """Disassemble a code object and return formatted output."""
    instructions = get_instructions(code_obj)
    lines = []

    header = f"=== {name} ==="
    lines.append(Colors.colorize(header, Colors.BOLD + Colors.CYAN, use_color))
    lines.append("")

    col_widths = {
        "offset": 8,
        "line": 6,
        "opname": 20,
        "arg": 8,
        "argrepr": 30,
    }

    for instr in instructions:
        parts = []

        offset_str = str(instr["offset"]).rjust(col_widths["offset"])
        if instr["is_jump_target"]:
            offset_str = Colors.colorize(">>", Colors.RED, use_color) + offset_str[2:]
        else:
            offset_str = "  " + offset_str[2:]
        parts.append(offset_str)

        line_str = str(instr["line"] if instr["line"] else "").ljust(col_widths["line"])
        parts.append(Colors.colorize(line_str, Colors.DIM, use_color))

        color = Colors.instruction_color(instr["opname"], use_color)
        opname_str = Colors.colorize(instr["opname"].ljust(col_widths["opname"]), color, use_color)
        parts.append(opname_str)

        if instr["arg"] is not None:
            arg_str = str(instr["arg"]).rjust(col_widths["arg"])
            parts.append(arg_str)
            parts.append(instr["argrepr"])
        else:
            parts.append(" " * col_widths["arg"])
            parts.append("")

        lines.append("  ".join(parts))

    lines.append("")
    return "\n".join(lines)


# ========== Control Flow Graph ==========

class BasicBlock:
    def __init__(self, start_offset):
        self.start_offset = start_offset
        self.end_offset = None
        self.instructions = []
        self.id = None
        self.label = None

    def __repr__(self):
        return f"BB{self.id}({self.start_offset}-{self.end_offset})"


def build_basic_blocks(instructions):
    """Split instructions into basic blocks."""
    block_starts = set()

    if instructions:
        block_starts.add(instructions[0]["offset"])

    jump_op_names = (
        "JUMP_FORWARD", "JUMP_ABSOLUTE", "JUMP_BACKWARD",
        "JUMP_BACKWARD_NO_INTERRUPT",
        "POP_JUMP_IF_TRUE", "POP_JUMP_IF_FALSE",
        "POP_JUMP_FORWARD_IF_TRUE", "POP_JUMP_FORWARD_IF_FALSE",
        "POP_JUMP_BACKWARD_IF_TRUE", "POP_JUMP_BACKWARD_IF_FALSE",
        "POP_JUMP_FORWARD_IF_NONE", "POP_JUMP_BACKWARD_IF_NONE",
        "POP_JUMP_FORWARD_IF_NOT_NONE", "POP_JUMP_BACKWARD_IF_NOT_NONE",
        "JUMP_IF_TRUE_OR_POP", "JUMP_IF_FALSE_OR_POP",
        "JUMP_IF_NOT_EXC_MATCH",
        "FOR_ITER", "SEND",
    )

    terminator_ops = (
        "RETURN_VALUE", "RETURN_CONST", "RETURN_GENERATOR",
        "RAISE_VARARGS", "RERAISE", "RAISE_EXCEPTION",
        "END_FOR", "END_SEND",
    )

    instr_by_offset = {instr["offset"]: instr for instr in instructions}
    offsets = sorted(instr_by_offset.keys())

    for i, instr in enumerate(instructions):
        if instr["is_jump_target"]:
            block_starts.add(instr["offset"])

        if instr["opname"] in jump_op_names:
            target = instr.get("argval")
            if target is not None and isinstance(target, int):
                block_starts.add(target)
            if i + 1 < len(instructions):
                next_offset = instructions[i + 1]["offset"]
                block_starts.add(next_offset)

        elif instr["opname"] in terminator_ops:
            if i + 1 < len(instructions):
                next_offset = instructions[i + 1]["offset"]
                block_starts.add(next_offset)

    blocks = []
    current_block = None

    for instr in instructions:
        offset = instr["offset"]

        if offset in block_starts or current_block is None:
            if current_block is not None and current_block.instructions:
                current_block.end_offset = current_block.instructions[-1]["offset"]
                blocks.append(current_block)
            current_block = BasicBlock(offset)
            current_block.id = len(blocks)
            current_block.label = f"BB{len(blocks)}"

        current_block.instructions.append(instr)

    if current_block is not None and current_block.instructions:
        current_block.end_offset = current_block.instructions[-1]["offset"]
        blocks.append(current_block)

    for i, block in enumerate(blocks):
        block.id = i
        block.label = f"BB{i}"

    return blocks


def build_cfg_edges(blocks):
    """Build directed edges between basic blocks."""
    edges = []

    def get_block_by_offset(offset):
        for b in blocks:
            if b.start_offset <= offset <= b.end_offset:
                return b
        return None

    def next_block(block):
        idx = blocks.index(block)
        if idx + 1 < len(blocks):
            return blocks[idx + 1]
        return None

    unconditional_jumps = (
        "JUMP_FORWARD", "JUMP_ABSOLUTE", "JUMP_BACKWARD",
        "JUMP_BACKWARD_NO_INTERRUPT",
    )

    conditional_jumps = (
        "POP_JUMP_IF_TRUE", "POP_JUMP_IF_FALSE",
        "POP_JUMP_FORWARD_IF_TRUE", "POP_JUMP_FORWARD_IF_FALSE",
        "POP_JUMP_BACKWARD_IF_TRUE", "POP_JUMP_BACKWARD_IF_FALSE",
        "POP_JUMP_FORWARD_IF_NONE", "POP_JUMP_BACKWARD_IF_NONE",
        "POP_JUMP_FORWARD_IF_NOT_NONE", "POP_JUMP_BACKWARD_IF_NOT_NONE",
        "JUMP_IF_TRUE_OR_POP", "JUMP_IF_FALSE_OR_POP",
        "JUMP_IF_NOT_EXC_MATCH",
    )

    terminators = (
        "RETURN_VALUE", "RETURN_CONST", "RETURN_GENERATOR",
        "RAISE_VARARGS", "RERAISE", "RAISE_EXCEPTION",
    )

    for block in blocks:
        last_instr = block.instructions[-1]
        opname = last_instr["opname"]
        target = last_instr.get("argval")
        if target is None:
            target = last_instr.get("arg")
        nxt = next_block(block)

        if opname in unconditional_jumps:
            target_block = get_block_by_offset(target) if isinstance(target, int) else None
            if target_block:
                edges.append((block, target_block, "unconditional"))

        elif opname in conditional_jumps:
            target_block = get_block_by_offset(target) if isinstance(target, int) else None
            if target_block:
                edges.append((block, target_block, "conditional"))
            if nxt:
                edges.append((block, nxt, "fallthrough"))

        elif opname == "FOR_ITER":
            target_block = get_block_by_offset(target) if isinstance(target, int) else None
            if target_block:
                edges.append((block, target_block, "loop_exit"))
            if nxt:
                edges.append((block, nxt, "loop_body"))

        elif opname == "SEND":
            target_block = get_block_by_offset(target) if isinstance(target, int) else None
            if target_block:
                edges.append((block, target_block, "send"))
            if nxt:
                edges.append((block, nxt, "fallthrough"))

        elif opname in terminators:
            pass

        else:
            if nxt:
                edges.append((block, nxt, "fallthrough"))

    return edges


def cfg_to_ascii(blocks, edges, use_color=True):
    """Generate ASCII art representation of the CFG."""
    lines = []

    edge_map = {}
    for src, dst, kind in edges:
        if src.id not in edge_map:
            edge_map[src.id] = []
        edge_map[src.id].append((dst, kind))

    lines.append(Colors.colorize("=== Control Flow Graph ===", Colors.BOLD + Colors.CYAN, use_color))
    lines.append("")

    for block in blocks:
        header = f"[{block.label}] offset {block.start_offset}-{block.end_offset}"
        lines.append(Colors.colorize(header, Colors.BOLD + Colors.YELLOW, use_color))

        for instr in block.instructions:
            line_parts = []
            line_parts.append(f"  {instr['offset']:4d}")
            color = Colors.instruction_color(instr["opname"], use_color)
            line_parts.append(Colors.colorize(instr["opname"], color, use_color))
            if instr["argrepr"]:
                line_parts.append(instr["argrepr"])
            lines.append("  ".join(line_parts))

        if block.id in edge_map:
            outgoing = edge_map[block.id]
            for dst, kind in outgoing:
                arrow = "->"
                kind_label = f"({kind})"
                edge_str = f"  {arrow} {dst.label} {kind_label}"
                if kind == "unconditional":
                    lines.append(Colors.colorize(edge_str, Colors.RED, use_color))
                elif kind == "conditional":
                    lines.append(Colors.colorize(edge_str, Colors.YELLOW, use_color))
                else:
                    lines.append(Colors.colorize(edge_str, Colors.DIM, use_color))

        lines.append("")

    return "\n".join(lines)


def cfg_to_dot(blocks, edges, name="cfg"):
    """Generate Graphviz DOT format CFG."""
    lines = []
    lines.append(f"digraph {name} {{")
    lines.append('  node [shape=box, style="rounded,filled", fillcolor="#f0f0f0"];')
    lines.append('  edge [fontsize=10];')
    lines.append("")

    for block in blocks:
        label_lines = []
        label_lines.append(f"{block.label}\\n")
        for instr in block.instructions:
            arg_repr = instr["argrepr"] if instr["argrepr"] else ""
            label_lines.append(f"{instr['offset']:4d} {instr['opname']} {arg_repr}\\n")
        label = "".join(label_lines)
        label = label.replace("\"", "'")
        lines.append(f'  {block.label} [label="{label}"];')

    lines.append("")

    for src, dst, kind in edges:
        style = "solid"
        color = "black"
        if kind == "unconditional":
            color = "red"
        elif kind == "conditional":
            color = "blue"
            style = "dashed"
        elif kind == "loop_exit":
            color = "green"
        elif kind == "loop_body":
            color = "purple"
        lines.append(f'  {src.label} -> {dst.label} [label="{kind}", color="{color}", style="{style}"];')

    lines.append("}")
    return "\n".join(lines)


# ========== Data Flow Analysis ==========

STACK_EFFECTS = {
    "POP_TOP": -1,
    "ROT_TWO": 0,
    "ROT_THREE": 0,
    "DUP_TOP": 1,
    "DUP_TOP_TWO": 2,
    "ROT_N": 0,
    "COPY": 1,
    "SWAP": 0,
}

LOAD_OPS = {
    "LOAD_FAST", "LOAD_FAST_BORROW", "LOAD_FAST_CHECK", "LOAD_FAST_AND_CLEAR",
    "LOAD_FAST_LOAD_FAST", "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
    "LOAD_GLOBAL", "LOAD_CONST", "LOAD_SMALL_INT", "LOAD_COMMON_CONSTANT",
    "LOAD_NAME", "LOAD_ATTR", "LOAD_SPECIAL", "LOAD_SUPER_ATTR",
    "LOAD_CLOSURE", "LOAD_DEREF", "LOAD_CLASSDEREF", "LOAD_METHOD",
    "LOAD_FROM_DICT_OR_DEREF", "LOAD_FROM_DICT_OR_GLOBALS",
    "LOAD_BUILD_CLASS", "LOAD_LOCALS",
    "PUSH_NULL", "PUSH_EXC_INFO",
}
STORE_OPS = {
    "STORE_FAST", "STORE_FAST_STORE_FAST", "STORE_FAST_LOAD_FAST",
    "STORE_FAST_MAYBE_NULL",
    "STORE_GLOBAL", "STORE_NAME", "STORE_ATTR",
    "STORE_DEREF", "STORE_CLASSDEREF",
    "STORE_SLICE", "STORE_SUBSCR",
}
DELETE_OPS = {
    "DELETE_FAST", "DELETE_GLOBAL", "DELETE_NAME", "DELETE_ATTR", "DELETE_DEREF",
    "DELETE_SLICE", "DELETE_SUBSCR",
}

BINARY_OPS = {"BINARY_OP", "BINARY_SLICE"}
INPLACE_OPS = set()
UNARY_OPS = {"UNARY_POSITIVE", "UNARY_NEGATIVE", "UNARY_NOT", "UNARY_INVERT",
             "UNARY_POSITIVE_FLOAT", "UNARY_NEGATIVE_FLOAT"}

CALL_OPS = {"CALL", "CALL_FUNCTION", "CALL_METHOD", "CALL_FUNCTION_KW", "CALL_FUNCTION_EX",
            "CALL_KW", "CALL_INTRINSIC_1", "CALL_INTRINSIC_2"}

COMPARE_OP = {"COMPARE_OP"}

RETURN_OPS = {"RETURN_VALUE", "RETURN_CONST", "RETURN_GENERATOR"}


def _count_fast_vars(opname, arg):
    """Count how many fast locals an instruction references."""
    if opname in ("LOAD_FAST_LOAD_FAST", "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
                  "STORE_FAST_STORE_FAST", "STORE_FAST_LOAD_FAST"):
        return 2
    elif opname in ("LOAD_FAST", "LOAD_FAST_BORROW", "LOAD_FAST_CHECK",
                    "LOAD_FAST_AND_CLEAR", "STORE_FAST", "STORE_FAST_MAYBE_NULL",
                    "DELETE_FAST"):
        return 1
    return 0


def compute_stack_effect(opname, arg):
    """Compute the stack effect of an instruction (+n pushes, -n pops)."""
    n_fast = _count_fast_vars(opname, arg)

    if opname.startswith("LOAD_FAST") and "STORE" not in opname:
        return n_fast
    elif opname.startswith("STORE_FAST") and opname != "STORE_FAST_LOAD_FAST":
        return -n_fast
    elif opname == "STORE_FAST_LOAD_FAST":
        return 0
    elif opname in LOAD_OPS:
        return 1
    elif opname in STORE_OPS:
        return -1
    elif opname in DELETE_OPS:
        return 0
    elif opname in BINARY_OPS:
        return -1
    elif opname in UNARY_OPS:
        return 0
    elif opname in RETURN_OPS:
        return -1
    elif opname in ("POP_TOP", "POP_BLOCK", "POP_EXCEPT"):
        return -1
    elif opname in ("DUP_TOP", "DUP_TOP_TWO"):
        return 2 if opname == "DUP_TOP_TWO" else 1
    elif opname in ("ROT_TWO", "ROT_THREE", "ROT_N", "SWAP"):
        return 0
    elif opname in CALL_OPS:
        if opname == "CALL_INTRINSIC_1":
            return 0
        elif opname == "CALL_INTRINSIC_2":
            return -1
        elif arg is not None:
            return -arg
        return -1
    elif opname == "CALL_KW":
        if arg is not None:
            return -(arg + 1)
        return -2
    elif opname in COMPARE_OP:
        return -1
    elif opname in ("BUILD_TUPLE", "BUILD_LIST", "BUILD_SET"):
        if arg is not None:
            return -(arg - 1)
        return 0
    elif opname == "BUILD_MAP":
        if arg is not None:
            return -(2 * arg) + 1
        return 1
    elif opname == "BUILD_STRING":
        if arg is not None:
            return -(arg - 1)
        return 0
    elif opname in ("UNPACK_SEQUENCE", "UNPACK_EX"):
        if arg is not None:
            return arg - 1
        return 0
    elif opname == "FOR_ITER":
        return 1
    elif opname in ("GET_ITER", "GET_YIELD_FROM_ITER", "GET_AWAITABLE"):
        return 0
    elif opname in ("YIELD_VALUE", "YIELD_FROM", "YIELD_CONST"):
        return 0
    elif opname == "SEND":
        return 0
    elif opname == "RAISE_VARARGS":
        if arg is not None:
            return -arg
        return 0
    elif opname in ("POP_JUMP_IF_TRUE", "POP_JUMP_IF_FALSE",
                    "POP_JUMP_FORWARD_IF_TRUE", "POP_JUMP_FORWARD_IF_FALSE",
                    "POP_JUMP_BACKWARD_IF_TRUE", "POP_JUMP_BACKWARD_IF_FALSE",
                    "POP_JUMP_FORWARD_IF_NONE", "POP_JUMP_BACKWARD_IF_NONE",
                    "POP_JUMP_FORWARD_IF_NOT_NONE", "POP_JUMP_BACKWARD_IF_NOT_NONE"):
        return -1
    elif opname in ("JUMP_IF_TRUE_OR_POP", "JUMP_IF_FALSE_OR_POP"):
        return 0
    elif opname == "JUMP_IF_NOT_EXC_MATCH":
        return -2
    elif opname == "IMPORT_NAME":
        return -1
    elif opname == "IMPORT_FROM":
        return 1
    elif opname == "MAKE_FUNCTION":
        return 0
    elif opname in ("SETUP_WITH", "BEFORE_WITH"):
        return 0
    elif opname == "WITH_EXCEPT_START":
        return 0
    elif opname == "PUSH_NULL":
        return 1
    elif opname == "PUSH_EXC_INFO":
        return 0
    elif opname == "CHECK_EXC_MATCH":
        return -1
    elif opname in ("CHECK_EG_MATCH", "COPY"):
        return 0
    elif opname == "RESUME":
        return 0
    elif opname in ("NOT_TAKEN", "END_FOR", "END_SEND", "POP_ITER"):
        return 0
    elif opname == "RETURN_IF_EXCEPTION":
        return 0
    elif opname == "RERAISE":
        return 0
    elif opname == "RAISE_EXCEPTION":
        return 0
    else:
        return 0


def _get_fast_var_names(instr, varnames):
    """Get variable names from a fast local instruction."""
    opname = instr["opname"]
    argval = instr.get("argval")

    if isinstance(argval, tuple):
        return list(argval)
    elif isinstance(argval, str):
        return [argval]
    elif isinstance(instr.get("arg"), int):
        arg = instr["arg"]
        if opname in ("LOAD_FAST_LOAD_FAST", "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
                      "STORE_FAST_STORE_FAST", "STORE_FAST_LOAD_FAST"):
            high = arg >> 8
            low = arg & 0xFF
            names = []
            if low < len(varnames):
                names.append(varnames[low])
            if high < len(varnames):
                names.append(varnames[high])
            return names
        elif arg < len(varnames):
            return [varnames[arg]]
    return []


def _is_load_fast(opname):
    """Check if an instruction loads from fast locals."""
    return opname in (
        "LOAD_FAST", "LOAD_FAST_BORROW", "LOAD_FAST_CHECK", "LOAD_FAST_AND_CLEAR",
        "LOAD_FAST_LOAD_FAST", "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
        "STORE_FAST_LOAD_FAST",
    )


def _is_store_fast(opname):
    """Check if an instruction stores to fast locals."""
    return opname in (
        "STORE_FAST", "STORE_FAST_STORE_FAST", "STORE_FAST_MAYBE_NULL",
        "STORE_FAST_LOAD_FAST",
    )


def analyze_dataflow(blocks, edges, code_obj):
    """Analyze data flow: stack changes, def-use chains, unused variables."""
    results = {
        "block_stack_effects": {},
        "def_use_chains": {},
        "unused_vars": [],
        "maybe_uninitialized": [],
        "local_vars": list(code_obj.co_varnames) if hasattr(code_obj, 'co_varnames') else [],
    }

    for block in blocks:
        total_effect = 0
        for instr in block.instructions:
            effect = compute_stack_effect(instr["opname"], instr["arg"])
            total_effect += effect
        results["block_stack_effects"][block.id] = total_effect

    var_defs = {}
    var_uses = {}
    varnames = list(code_obj.co_varnames) if hasattr(code_obj, 'co_varnames') else []

    for block in blocks:
        for instr in block.instructions:
            opname = instr["opname"]
            var_names = _get_fast_var_names(instr, varnames)

            if _is_store_fast(opname):
                for varname in var_names:
                    if varname not in var_defs:
                        var_defs[varname] = []
                    var_defs[varname].append({
                        "offset": instr["offset"],
                        "line": instr["line"],
                        "block": block.id,
                    })

            if _is_load_fast(opname):
                for varname in var_names:
                    if varname not in var_uses:
                        var_uses[varname] = []
                    var_uses[varname].append({
                        "offset": instr["offset"],
                        "line": instr["line"],
                        "block": block.id,
                    })

    results["var_defs"] = var_defs
    results["var_uses"] = var_uses

    for varname in var_defs:
        if varname not in var_uses:
            for def_info in var_defs[varname]:
                results["unused_vars"].append({
                    "variable": varname,
                    "offset": def_info["offset"],
                    "line": def_info["line"],
                    "type": "unused_assignment",
                })

    pred_map = {b.id: [] for b in blocks}
    for src, dst, kind in edges:
        pred_map[dst.id].append(src.id)

    if blocks:
        entry_block = blocks[0]
        initialized = {v: set() for v in varnames}

        n_args = 0
        if hasattr(code_obj, 'co_argcount'):
            n_args += code_obj.co_argcount
        if hasattr(code_obj, 'co_kwonlyargcount'):
            n_args += code_obj.co_kwonlyargcount

        for i in range(min(n_args, len(varnames))):
            initialized[varnames[i]].add(entry_block.id)

        for block in blocks:
            for instr in block.instructions:
                if _is_store_fast(instr["opname"]):
                    var_names = _get_fast_var_names(instr, varnames)
                    for varname in var_names:
                        initialized[varname].add(block.id)

        for block in blocks:
            for instr in block.instructions:
                if _is_load_fast(instr["opname"]):
                    var_names = _get_fast_var_names(instr, varnames)
                    for varname in var_names:
                        if block.id not in initialized.get(varname, set()):
                            preds = pred_map[block.id]
                            all_preds_init = all(
                                any(b in initialized.get(varname, set())
                                    for b in _reach_blocks(p, pred_map, initialized, varname))
                                for p in preds
                            ) if preds else (block.id == entry_block.id)
                            if not all_preds_init:
                                results["maybe_uninitialized"].append({
                                    "variable": varname,
                                    "offset": instr["offset"],
                                    "line": instr["line"],
                                    "block": block.id,
                                })

    return results


def _reach_blocks(start_id, pred_map, initialized, varname, visited=None):
    """Helper to find which blocks have variable initialized when reaching start_id."""
    if visited is None:
        visited = set()
    if start_id in visited:
        return set()
    visited.add(start_id)

    if start_id in initialized.get(varname, set()):
        return {start_id}

    result = set()
    for pred in pred_map[start_id]:
        result |= _reach_blocks(pred, pred_map, initialized, varname, visited)
    return result


def format_dataflow_results(results, blocks, use_color=True):
    """Format data flow analysis results for display."""
    lines = []
    lines.append(Colors.colorize("=== Data Flow Analysis ===", Colors.BOLD + Colors.CYAN, use_color))
    lines.append("")

    lines.append(Colors.colorize("Stack Effects per Block:", Colors.BOLD, use_color))
    for block_id, effect in sorted(results["block_stack_effects"].items()):
        sign = "+" if effect > 0 else ""
        block_label = f"BB{block_id}"
        effect_str = f"{sign}{effect}"
        if effect > 0:
            effect_colored = Colors.colorize(effect_str, Colors.GREEN, use_color)
        elif effect < 0:
            effect_colored = Colors.colorize(effect_str, Colors.RED, use_color)
        else:
            effect_colored = effect_str
        lines.append(f"  {block_label}: net stack {effect_colored}")
    lines.append("")

    lines.append(Colors.colorize("Def-Use Chains:", Colors.BOLD, use_color))
    all_vars = set(results.get("var_defs", {}).keys()) | set(results.get("var_uses", {}).keys())
    for var in sorted(all_vars):
        defs = results.get("var_defs", {}).get(var, [])
        uses = results.get("var_uses", {}).get(var, [])
        lines.append(f"  {Colors.colorize(var, Colors.YELLOW, use_color)}:")
        if defs:
            def_offsets = ", ".join(f"@{d['offset']}" for d in defs)
            lines.append(f"    defs: {def_offsets}")
        if uses:
            use_offsets = ", ".join(f"@{u['offset']}" for u in uses)
            lines.append(f"    uses: {use_offsets}")
        if not defs and not uses:
            lines.append("    (none)")
    lines.append("")

    if results["unused_vars"]:
        lines.append(Colors.colorize("Unused Variable Assignments:", Colors.BOLD + Colors.YELLOW, use_color))
        for uv in results["unused_vars"]:
            lines.append(f"  {Colors.colorize(uv['variable'], Colors.YELLOW, use_color)} "
                         f"at offset {uv['offset']} (line {uv['line']})")
        lines.append("")

    if results["maybe_uninitialized"]:
        lines.append(Colors.colorize("Potentially Uninitialized Variables:", Colors.BOLD + Colors.RED, use_color))
        for mi in results["maybe_uninitialized"]:
            lines.append(f"  {Colors.colorize(mi['variable'], Colors.RED, use_color)} "
                         f"at offset {mi['offset']} (line {mi['line']}) in BB{mi['block']}")
        lines.append("")

    return "\n".join(lines)


# ========== Optimization Suggestions ==========

def analyze_optimizations(blocks, edges, instructions, code_obj, dataflow_results=None):
    """Detect optimization opportunities, integrating dataflow analysis results."""
    suggestions = []

    reachable = set()
    if blocks:
        stack = [blocks[0]]
        while stack:
            b = stack.pop()
            if b.id in reachable:
                continue
            reachable.add(b.id)
            for src, dst, kind in edges:
                if src.id == b.id and dst.id not in reachable:
                    stack.append(dst)

    for block in blocks:
        if block.id not in reachable:
            first_instr = block.instructions[0] if block.instructions else None
            suggestions.append({
                "type": "dead_code",
                "severity": "warning",
                "block": block.id,
                "start_offset": block.start_offset,
                "end_offset": block.end_offset,
                "line": first_instr["line"] if first_instr else None,
                "description": f"Dead code: basic block BB{block.id} is unreachable",
                "suggestion": "Consider removing unreachable code or fixing control flow",
                "removable": True,
            })

    if dataflow_results:
        for uv in dataflow_results.get("unused_vars", []):
            suggestions.append({
                "type": "unused_assignment",
                "severity": "warning",
                "variable": uv["variable"],
                "offset": uv["offset"],
                "line": uv["line"],
                "start_offset": uv["offset"],
                "end_offset": uv["offset"],
                "description": f"Unused assignment to variable '{uv['variable']}' at offset {uv['offset']}",
                "suggestion": f"Remove the unused assignment to '{uv['variable']}'",
                "removable": True,
            })

        for mi in dataflow_results.get("maybe_uninitialized", []):
            suggestions.append({
                "type": "maybe_uninitialized",
                "severity": "error",
                "variable": mi["variable"],
                "offset": mi["offset"],
                "line": mi["line"],
                "block": mi["block"],
                "start_offset": mi["offset"],
                "end_offset": mi["offset"],
                "description": f"Variable '{mi['variable']}' may be uninitialized at offset {mi['offset']}",
                "suggestion": f"Ensure '{mi['variable']}' is initialized before use, or add a default value",
                "removable": False,
            })

    for i in range(len(instructions) - 1):
        curr = instructions[i]
        nxt = instructions[i + 1]
        if curr["opname"] == "LOAD_FAST" and nxt["opname"] == "POP_TOP":
            suggestions.append({
                "type": "redundant_operation",
                "severity": "info",
                "offset": curr["offset"],
                "line": curr["line"],
                "start_offset": curr["offset"],
                "end_offset": nxt["offset"],
                "description": f"Redundant LOAD_FAST + POP_TOP at offset {curr['offset']}",
                "suggestion": "The loaded value is immediately discarded; consider removing",
                "removable": True,
            })
        if curr["opname"] == "LOAD_CONST" and nxt["opname"] == "POP_TOP":
            suggestions.append({
                "type": "redundant_operation",
                "severity": "info",
                "offset": curr["offset"],
                "line": curr["line"],
                "start_offset": curr["offset"],
                "end_offset": nxt["offset"],
                "description": f"Redundant LOAD_CONST + POP_TOP at offset {curr['offset']}",
                "suggestion": "The constant is immediately discarded; consider removing",
                "removable": True,
            })

    for block in blocks:
        for i, instr in enumerate(block.instructions):
            if instr["opname"] == "LOAD_CONST" and instr["arg"] is not None:
                const_val1 = instr["argval"]
                if i + 1 < len(block.instructions):
                    next_instr = block.instructions[i + 1]
                    if next_instr["opname"] == "LOAD_CONST":
                        const_val2 = next_instr["argval"]
                        if i + 2 < len(block.instructions):
                            third = block.instructions[i + 2]
                            if third["opname"] in BINARY_OPS:
                                folded_value = None
                                try:
                                    if third["opname"] == "BINARY_OP":
                                        op_type = third["argval"]
                                        if isinstance(op_type, str):
                                            if op_type == "+":
                                                folded_value = const_val1 + const_val2
                                            elif op_type == "-":
                                                folded_value = const_val1 - const_val2
                                            elif op_type == "*":
                                                folded_value = const_val1 * const_val2
                                            elif op_type == "/":
                                                folded_value = const_val1 / const_val2
                                            elif op_type == "**":
                                                folded_value = const_val1 ** const_val2
                                            elif op_type == "//":
                                                folded_value = const_val1 // const_val2
                                            elif op_type == "%":
                                                folded_value = const_val1 % const_val2
                                except Exception:
                                    pass
                                suggestions.append({
                                    "type": "constant_folding",
                                    "severity": "info",
                                    "offset": instr["offset"],
                                    "line": instr["line"],
                                    "start_offset": instr["offset"],
                                    "end_offset": third["offset"],
                                    "description": f"Constant expression at offset {instr['offset']} can be folded",
                                    "suggestion": "Two constants followed by binary operation can be pre-computed",
                                    "removable": True,
                                    "folded_value": folded_value,
                                    "const1": const_val1,
                                    "const2": const_val2,
                                })

    for block in blocks:
        for instr in block.instructions:
            if instr["opname"] == "JUMP_FORWARD" and instr["arg"] is not None:
                target = instr["arg"]
                next_offset = instr["offset"] + 2 + (2 if sys.version_info >= (3, 10) else 0)
                if target <= next_offset:
                    suggestions.append({
                        "type": "trivial_jump",
                        "severity": "info",
                        "offset": instr["offset"],
                        "line": instr["line"],
                        "start_offset": instr["offset"],
                        "end_offset": instr["offset"],
                        "description": f"Trivial jump at offset {instr['offset']} jumps to next instruction",
                        "suggestion": "Consider removing the redundant jump",
                        "removable": True,
                    })

    suggestions.sort(key=lambda s: s.get("start_offset", s.get("offset", 0)))
    return suggestions


def format_optimizations(suggestions, use_color=True):
    """Format optimization suggestions for display."""
    lines = []
    lines.append(Colors.colorize("=== Optimization Suggestions ===", Colors.BOLD + Colors.CYAN, use_color))
    lines.append("")

    if not suggestions:
        lines.append(Colors.colorize("No optimization suggestions found.", Colors.GREEN, use_color))
        return "\n".join(lines)

    severity_colors = {
        "error": Colors.RED,
        "warning": Colors.YELLOW,
        "info": Colors.CYAN,
    }

    for i, s in enumerate(suggestions, 1):
        sev_color = severity_colors.get(s["severity"], Colors.WHITE)
        header = f"[{i}] {Colors.colorize(s['severity'].upper(), sev_color + Colors.BOLD, use_color)}: {s['type']}"
        lines.append(header)
        lines.append(f"    Location: offset {s.get('start_offset', s.get('offset', '?'))}"
                     f" (line {s.get('line', '?')})")
        lines.append(f"    Description: {s['description']}")
        lines.append(f"    Suggestion: {s['suggestion']}")
        lines.append("")

    return "\n".join(lines)


# ========== Source Code Rewriting ==========

class OptimizationRewriter(ast.NodeTransformer):
    """AST transformer that applies optimizations based on analysis results."""

    def __init__(self, unused_vars=None, constant_folds=None, dead_code_lines=None, source_lines=None):
        self.unused_vars = unused_vars or []
        self.constant_folds = constant_folds or []
        self.dead_code_lines = dead_code_lines or set()
        self.source_lines = source_lines or []
        self.removed_lines = set()
        self.modified_assignments = {}

    def visit_Assign(self, node):
        self.generic_visit(node)
        if isinstance(node.targets[0], ast.Name):
            var_name = node.targets[0].id
            for uv in self.unused_vars:
                if uv["variable"] == var_name and uv["line"] == node.lineno:
                    self.removed_lines.add(node.lineno)
                    return None
        return node

    def visit_Expr(self, node):
        self.generic_visit(node)
        if node.lineno in self.dead_code_lines:
            self.removed_lines.add(node.lineno)
            return None
        if isinstance(node.value, ast.Constant):
            for uv in self.unused_vars:
                if uv["line"] == node.lineno and uv["type"] == "unused_assignment":
                    pass
        return node

    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant):
            left_val = node.left.value
            right_val = node.right.value
            try:
                if isinstance(node.op, ast.Add):
                    result = left_val + right_val
                elif isinstance(node.op, ast.Sub):
                    result = left_val - right_val
                elif isinstance(node.op, ast.Mult):
                    result = left_val * right_val
                elif isinstance(node.op, ast.Div):
                    result = left_val / right_val
                elif isinstance(node.op, ast.FloorDiv):
                    result = left_val // right_val
                elif isinstance(node.op, ast.Mod):
                    result = left_val % right_val
                elif isinstance(node.op, ast.Pow):
                    result = left_val ** right_val
                else:
                    return node
                return ast.Constant(value=result, lineno=node.lineno, col_offset=node.col_offset)
            except Exception:
                pass
        return node

    def visit_If(self, node):
        self.generic_visit(node)
        if isinstance(node.test, ast.Constant):
            if node.test.value is True:
                return node.body
            elif node.test.value is False:
                return node.orelse if node.orelse else None
        return node

    def visit_While(self, node):
        self.generic_visit(node)
        if isinstance(node.test, ast.Constant) and node.test.value is False:
            return None
        return node


def apply_ast_rewrites(source, unused_vars=None, constant_folds=None, dead_code_lines=None):
    """Apply AST-based optimizations to source code."""
    try:
        tree = ast.parse(source)
        source_lines = source.splitlines()
        rewriter = OptimizationRewriter(
            unused_vars=unused_vars,
            constant_folds=constant_folds,
            dead_code_lines=dead_code_lines,
            source_lines=source_lines
        )
        new_tree = rewriter.visit(tree)
        ast.fix_missing_locations(new_tree)
        return ast.unparse(new_tree), rewriter.removed_lines
    except Exception as e:
        print(f"Warning: AST rewriting failed: {e}", file=sys.stderr)
        return source, set()


def collect_rewrite_info(optimizations):
    """Collect rewrite information from optimization suggestions."""
    unused_vars = []
    constant_folds = []
    dead_code_offsets = set()
    dead_code_blocks = set()

    for opt in optimizations:
        if opt.get("removable", False):
            if opt["type"] == "unused_assignment":
                unused_vars.append(opt)
            elif opt["type"] == "constant_folding":
                constant_folds.append(opt)
            elif opt["type"] == "dead_code":
                dead_code_blocks.add(opt.get("block"))
                dead_code_offsets.add(opt.get("start_offset"))

    return {
        "unused_vars": unused_vars,
        "constant_folds": constant_folds,
        "dead_code_offsets": dead_code_offsets,
        "dead_code_blocks": dead_code_blocks,
    }


def find_dead_code_lines(blocks, dead_code_blocks, instructions):
    """Find line numbers of dead code blocks."""
    dead_lines = set()
    for block in blocks:
        if block.id in dead_code_blocks:
            for instr in block.instructions:
                if instr.get("line"):
                    dead_lines.add(instr["line"])
    return dead_lines


def rewrite_source(source_path, optimizations, blocks, instructions):
    """Rewrite source code applying optimizations."""
    with open(source_path, "r", encoding="utf-8") as f:
        source = f.read()

    rewrite_info = collect_rewrite_info(optimizations)
    dead_code_lines = find_dead_code_lines(blocks, rewrite_info["dead_code_blocks"], instructions)

    optimized_source, removed_lines = apply_ast_rewrites(
        source,
        unused_vars=rewrite_info["unused_vars"],
        constant_folds=rewrite_info["constant_folds"],
        dead_code_lines=dead_code_lines
    )

    return optimized_source, {
        "removed_lines": removed_lines,
        "unused_vars_removed": len(rewrite_info["unused_vars"]),
        "constants_folded": len(rewrite_info["constant_folds"]),
        "dead_blocks_removed": len(rewrite_info["dead_code_blocks"]),
    }


def count_bytecode_instructions(code_obj):
    """Count the number of bytecode instructions in a code object (recursively)."""
    count = len(get_instructions(code_obj))
    for const in code_obj.co_consts:
        if isinstance(const, CodeType):
            count += count_bytecode_instructions(const)
    return count


def cmd_rewrite(args):
    """Handle 'rewrite' command."""
    use_color = not args.no_color

    try:
        code_obj = compile_source(args.source)
    except SyntaxError as e:
        print(f"Error: Failed to compile {args.source}: {e}", file=sys.stderr)
        sys.exit(1)

    all_codes = get_all_code_objects(code_obj)

    if args.function:
        name, co = find_function(code_obj, args.function)
        if co is None:
            print(f"Error: Function '{args.function}' not found", file=sys.stderr)
            sys.exit(1)
        codes_to_process = [(name, co)]
    else:
        codes_to_process = all_codes

    all_optimizations = []
    all_blocks = []
    all_instructions = []
    all_dataflow = []

    original_instr_count = count_bytecode_instructions(code_obj)

    for name, co in codes_to_process:
        instructions = get_instructions(co)
        blocks = build_basic_blocks(instructions)
        edges = build_cfg_edges(blocks)
        dataflow_results = analyze_dataflow(blocks, edges, co)
        optimizations = analyze_optimizations(blocks, edges, instructions, co, dataflow_results)

        all_optimizations.extend(optimizations)
        all_blocks.extend(blocks)
        all_instructions.extend(instructions)
        all_dataflow.append(dataflow_results)

    if not all_optimizations:
        print(Colors.colorize("No optimizations to apply.", Colors.GREEN, use_color))
        return

    print(Colors.colorize("=== Optimization Summary ===", Colors.BOLD + Colors.CYAN, use_color))
    print()
    removable = [o for o in all_optimizations if o.get("removable", False)]
    print(f"Total optimization suggestions: {len(all_optimizations)}")
    print(f"Automatically applicable: {len(removable)}")
    print()

    for i, opt in enumerate(removable, 1):
        print(f"  [{i}] {opt['type']}: {opt['description']}")
    print()

    optimized_source, stats = rewrite_source(args.source, all_optimizations, all_blocks, all_instructions)

    output_path = args.output or (os.path.splitext(args.source)[0] + "_optimized.py")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(optimized_source)

    print(Colors.colorize(f"Optimized source written to: {output_path}", Colors.GREEN, use_color))
    print()

    print(Colors.colorize("=== Rewrite Statistics ===", Colors.BOLD + Colors.CYAN, use_color))
    print()
    print(f"  Unused variable assignments removed: {stats['unused_vars_removed']}")
    print(f"  Constant expressions folded: {stats['constants_folded']}")
    print(f"  Dead code blocks removed: {stats['dead_blocks_removed']}")
    print(f"  Source lines removed: {len(stats['removed_lines'])}")
    print()

    try:
        optimized_code = compile(optimized_source, output_path, "exec")
        optimized_instr_count = count_bytecode_instructions(optimized_code)

        print(Colors.colorize("=== Bytecode Comparison ===", Colors.BOLD + Colors.CYAN, use_color))
        print()
        print(f"  Original instructions: {original_instr_count}")
        print(f"  Optimized instructions: {optimized_instr_count}")
        reduction = original_instr_count - optimized_instr_count
        reduction_pct = (reduction / original_instr_count * 100) if original_instr_count > 0 else 0
        if reduction > 0:
            print(Colors.colorize(f"  Reduction: {reduction} instructions ({reduction_pct:.1f}%)",
                                  Colors.GREEN, use_color))
        elif reduction < 0:
            print(Colors.colorize(f"  Increase: {-reduction} instructions", Colors.RED, use_color))
        else:
            print("  No change in instruction count")
        print()
    except SyntaxError as e:
        print(Colors.colorize(f"Warning: Could not compile optimized source for comparison: {e}",
                              Colors.YELLOW, use_color))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({
                "original_file": args.source,
                "optimized_file": output_path,
                "original_instructions": original_instr_count,
                "stats": stats,
                "optimizations_applied": removable,
            }, f, indent=2, default=str)
        print(f"JSON report written to: {args.json}")


# ========== Complexity Analysis ==========

class LoopInfo:
    """Information about a loop structure."""
    def __init__(self, loop_type, start_offset, header_block, body_blocks, exit_block, nesting_level=0):
        self.loop_type = loop_type
        self.start_offset = start_offset
        self.header_block = header_block
        self.body_blocks = body_blocks
        self.exit_block = exit_block
        self.nesting_level = nesting_level
        self.instruction_count = 0


def detect_loops(blocks, edges):
    """Detect loop structures in the CFG using back edges."""
    loops = []

    edge_map = {}
    for src, dst, kind in edges:
        if src.id not in edge_map:
            edge_map[src.id] = []
        edge_map[src.id].append((dst, kind))

    def dfs(current, visited, path):
        visited.add(current.id)
        path.append(current.id)

        for dst, kind in edge_map.get(current.id, []):
            if dst.id in path:
                loop_start_idx = path.index(dst.id)
                loop_blocks_ids = path[loop_start_idx:]
                loop_body_blocks = [b for b in blocks if b.id in loop_blocks_ids]
                exit_block = None
                for b in loop_body_blocks:
                    for out_dst, out_kind in edge_map.get(b.id, []):
                        if out_dst.id not in loop_blocks_ids:
                            exit_block = out_dst
                            break
                    if exit_block:
                        break

                loop_type = "for" if any(
                    any(i["opname"] == "FOR_ITER" for i in b.instructions)
                    for b in loop_body_blocks
                ) else "while"

                header_block = [b for b in blocks if b.id == loop_blocks_ids[0]][0]
                loops.append(LoopInfo(
                    loop_type=loop_type,
                    start_offset=header_block.start_offset,
                    header_block=header_block,
                    body_blocks=loop_body_blocks,
                    exit_block=exit_block,
                ))
            elif dst.id not in visited:
                dfs(dst, visited, path)

        path.pop()

    if blocks:
        dfs(blocks[0], set(), [])

    visited_loops = set()
    unique_loops = []
    for loop in loops:
        key = tuple(sorted(b.id for b in loop.body_blocks))
        if key not in visited_loops:
            visited_loops.add(key)
            unique_loops.append(loop)

    block_to_loops = {}
    for loop in unique_loops:
        for block in loop.body_blocks:
            if block.id not in block_to_loops:
                block_to_loops[block.id] = []
            block_to_loops[block.id].append(loop)

    for loop in unique_loops:
        max_nesting = 0
        for block in loop.body_blocks:
            nesting = len(block_to_loops.get(block.id, []))
            if nesting > max_nesting:
                max_nesting = nesting
        loop.nesting_level = max_nesting

        total_instr = 0
        for block in loop.body_blocks:
            total_instr += len(block.instructions)
        loop.instruction_count = total_instr

    return unique_loops


def compute_cyclomatic_complexity(blocks, edges):
    """Compute cyclomatic complexity: E - N + 2P, simplified to conditional_branches + 1."""
    conditional_edges = sum(1 for _, _, kind in edges if kind == "conditional")
    return conditional_edges + 1


def compute_max_nesting_depth(loops):
    """Compute maximum nesting depth from loops."""
    if not loops:
        return 0
    return max(loop.nesting_level for loop in loops)


def analyze_hot_paths(blocks, edges, instructions, loops):
    """Analyze hot paths - blocks inside loops."""
    loop_block_ids = set()
    for loop in loops:
        for block in loop.body_blocks:
            loop_block_ids.add(block.id)

    total_instructions = len(instructions)
    loop_instructions = sum(
        len(block.instructions) for block in blocks if block.id in loop_block_ids
    )

    hot_blocks = []
    for block in blocks:
        if block.id in loop_block_ids:
            hot_blocks.append({
                "block": block.id,
                "instruction_count": len(block.instructions),
                "start_offset": block.start_offset,
                "end_offset": block.end_offset,
            })

    return {
        "total_instructions": total_instructions,
        "loop_instructions": loop_instructions,
        "loop_ratio": (loop_instructions / total_instructions) if total_instructions > 0 else 0,
        "hot_blocks": hot_blocks,
    }


def analyze_function_complexity(name, code_obj):
    """Analyze complexity for a single function."""
    instructions = get_instructions(code_obj)
    blocks = build_basic_blocks(instructions)
    edges = build_cfg_edges(blocks)

    loops = detect_loops(blocks, edges)
    cyclomatic = compute_cyclomatic_complexity(blocks, edges)
    max_nesting = compute_max_nesting_depth(loops)
    hot_paths = analyze_hot_paths(blocks, edges, instructions, loops)

    return {
        "name": name,
        "function_name": code_obj.co_name,
        "instructions": len(instructions),
        "basic_blocks": len(blocks),
        "loops": loops,
        "loop_count": len(loops),
        "cyclomatic_complexity": cyclomatic,
        "max_nesting_depth": max_nesting,
        "hot_paths": hot_paths,
        "blocks": blocks,
        "edges": edges,
    }


def format_complexity_report(all_complexities, use_color=True):
    """Format complexity analysis report."""
    lines = []
    lines.append(Colors.colorize("=== Complexity Analysis Report ===", Colors.BOLD + Colors.CYAN, use_color))
    lines.append("")

    sorted_complexities = sorted(
        all_complexities,
        key=lambda x: x["cyclomatic_complexity"],
        reverse=True
    )

    lines.append(Colors.colorize("Function Complexity Ranking (by Cyclomatic Complexity):", Colors.BOLD, use_color))
    lines.append("")

    header = f"{'Rank':<6}{'Function':<30}{'CC':<6}{'Loops':<8}{'Nesting':<10}{'Instr':<10}"
    lines.append(Colors.colorize(header, Colors.BOLD + Colors.YELLOW, use_color))
    lines.append("-" * 70)

    for rank, comp in enumerate(sorted_complexities, 1):
        name = comp["name"]
        if len(name) > 28:
            name = name[:25] + "..."
        cc = comp["cyclomatic_complexity"]
        lc = comp["loop_count"]
        nd = comp["max_nesting_depth"]
        instr = comp["instructions"]

        cc_color = Colors.GREEN
        if cc > 10:
            cc_color = Colors.RED
        elif cc > 5:
            cc_color = Colors.YELLOW

        line = f"{rank:<6}{name:<30}"
        line += Colors.colorize(f"{cc:<6}", cc_color, use_color)
        line += f"{lc:<8}{nd:<10}{instr:<10}"
        lines.append(line)

    lines.append("")
    lines.append(Colors.colorize("Cyclomatic Complexity Guide:", Colors.DIM, use_color))
    lines.append(Colors.colorize("  1-5: Low complexity", Colors.GREEN, use_color))
    lines.append(Colors.colorize("  6-10: Moderate complexity", Colors.YELLOW, use_color))
    lines.append(Colors.colorize("  >10: High complexity - consider refactoring", Colors.RED, use_color))
    lines.append("")

    for comp in sorted_complexities:
        if comp["loops"]:
            lines.append(Colors.colorize(f"=== {comp['name']} Loop Details ===", Colors.BOLD + Colors.CYAN, use_color))
            lines.append("")

            for i, loop in enumerate(comp["loops"], 1):
                loop_type_str = "for" if loop.loop_type == "for" else "while"
                lines.append(f"  Loop {i}: {loop_type_str} loop at offset {loop.start_offset}")
                lines.append(f"    Type: {loop_type_str}")
                lines.append(f"    Nesting level: {loop.nesting_level}")
                lines.append(f"    Body blocks: {[b.label for b in loop.body_blocks]}")
                lines.append(f"    Instructions in loop: {loop.instruction_count}")
                lines.append(f"    Header block: {loop.header_block.label}")
                if loop.exit_block:
                    lines.append(f"    Exit to: {loop.exit_block.label}")
                lines.append("")

            hp = comp["hot_paths"]
            lines.append(f"  Hot Path Analysis:")
            lines.append(f"    Total instructions: {hp['total_instructions']}")
            lines.append(f"    Loop instructions: {hp['loop_instructions']}")
            ratio_pct = hp['loop_ratio'] * 100
            ratio_color = Colors.GREEN
            if ratio_pct > 50:
                ratio_color = Colors.RED
            elif ratio_pct > 20:
                ratio_color = Colors.YELLOW
            lines.append(Colors.colorize(
                f"    Loop ratio: {ratio_pct:.1f}%",
                ratio_color, use_color))

            if hp["hot_blocks"]:
                lines.append(f"    Hot blocks (in loops):")
                for hb in hp["hot_blocks"]:
                    lines.append(f"      {hb['block']:>4} ({hb['instruction_count']} instructions)")
            lines.append("")

    return "\n".join(lines)


def cmd_complexity(args):
    """Handle 'complexity' command."""
    use_color = not args.no_color

    try:
        code_obj = compile_source(args.source)
    except SyntaxError as e:
        print(f"Error: Failed to compile {args.source}: {e}", file=sys.stderr)
        sys.exit(1)

    all_codes = get_all_code_objects(code_obj)

    if args.function:
        name, co = find_function(code_obj, args.function)
        if co is None:
            print(f"Error: Function '{args.function}' not found", file=sys.stderr)
            sys.exit(1)
        codes_to_process = [(name, co)]
    else:
        codes_to_process = all_codes

    all_complexities = []

    for name, co in codes_to_process:
        if co.co_name == "<module>" and not args.function:
            continue
        complexity = analyze_function_complexity(name, co)
        all_complexities.append(complexity)

    if not all_complexities:
        print(Colors.colorize("No functions to analyze.", Colors.YELLOW, use_color))
        return

    print(format_complexity_report(all_complexities, use_color))

    if args.json:
        json_data = []
        for comp in all_complexities:
            loops_data = []
            for loop in comp["loops"]:
                loops_data.append({
                    "type": loop.loop_type,
                    "start_offset": loop.start_offset,
                    "nesting_level": loop.nesting_level,
                    "body_blocks": [b.id for b in loop.body_blocks],
                    "instruction_count": loop.instruction_count,
                    "header_block": loop.header_block.id,
                    "exit_block": loop.exit_block.id if loop.exit_block else None,
                })

            json_data.append({
                "name": comp["name"],
                "function_name": comp["function_name"],
                "instructions": comp["instructions"],
                "basic_blocks": comp["basic_blocks"],
                "loop_count": comp["loop_count"],
                "cyclomatic_complexity": comp["cyclomatic_complexity"],
                "max_nesting_depth": comp["max_nesting_depth"],
                "loops": loops_data,
                "hot_paths": comp["hot_paths"],
            })

        output = {"functions": json_data} if len(json_data) > 1 else json_data[0]
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"JSON report written to: {args.json}")


# ========== Call Graph Analysis ==========

class CallGraph:
    """Represents a function call graph."""

    def __init__(self):
        self.nodes = {}
        self.edges = []
        self.node_names = []

    def add_node(self, name, code_obj=None):
        """Add a function node to the graph."""
        if name not in self.nodes:
            self.nodes[name] = {
                "name": name,
                "code_obj": code_obj,
                "calls": [],
                "called_by": [],
                "call_count": 0,
                "in_degree": 0,
                "out_degree": 0,
            }
            self.node_names.append(name)

    def add_edge(self, caller, callee, offset=None, line=None):
        """Add a call edge from caller to callee (with deduplication)."""
        self.add_node(caller)
        self.add_node(callee)

        for existing in self.edges:
            if existing["caller"] == caller and existing["callee"] == callee:
                return

        self.edges.append({
            "caller": caller,
            "callee": callee,
            "offset": offset,
            "line": line,
        })

        self.nodes[caller]["calls"].append({
            "callee": callee,
            "offset": offset,
            "line": line,
        })
        self.nodes[caller]["out_degree"] += 1
        self.nodes[caller]["call_count"] += 1

        self.nodes[callee]["called_by"].append({
            "caller": caller,
            "offset": offset,
            "line": line,
        })
        self.nodes[callee]["in_degree"] += 1

    def get_recursive_calls(self):
        """Find all recursive calls (direct and indirect)."""
        direct_recursion = []
        indirect_recursion = []
        direct_self_callers = set()

        for caller, callee_list in [(n, self.nodes[n]["calls"]) for n in self.nodes]:
            for call in callee_list:
                if call["callee"] == caller:
                    direct_recursion.append({
                        "function": caller,
                        "offset": call["offset"],
                        "line": call["line"],
                    })
                    direct_self_callers.add(caller)

        cycles = self._find_cycles()
        seen_cycles = set()
        for cycle in cycles:
            unique_nodes = set(cycle[:-1])
            if len(unique_nodes) > 1:
                cycle_key = tuple(sorted(unique_nodes))
                if cycle_key not in seen_cycles:
                    seen_cycles.add(cycle_key)
                    indirect_recursion.append({
                        "cycle": cycle,
                        "length": len(unique_nodes),
                    })

        return {
            "direct": direct_recursion,
            "indirect": indirect_recursion,
        }

    def _find_cycles(self):
        """Find all cycles in the call graph using DFS."""
        cycles = []
        visited = set()
        path = []

        def dfs(node):
            if node in path:
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                cycles.append(cycle)
                return
            if node in visited:
                return

            visited.add(node)
            path.append(node)

            for call in self.nodes[node]["calls"]:
                dfs(call["callee"])

            path.pop()

        for node in self.node_names:
            if node not in visited:
                dfs(node)

        unique_cycles = []
        seen = set()
        for cycle in cycles:
            normalized = tuple(sorted(cycle[:-1]))
            if normalized not in seen:
                seen.add(normalized)
                unique_cycles.append(cycle)

        return unique_cycles

    def get_isolated_functions(self):
        """Find functions that are never called by any other function."""
        isolated = []
        for name in self.node_names:
            node = self.nodes[name]
            if node["in_degree"] == 0 and name != "<module>":
                isolated.append(name)
        return isolated

    def get_reachable_subgraph(self, root):
        """Get all nodes reachable from root."""
        if root not in self.nodes:
            return set()

        reachable = set()
        stack = [root]

        while stack:
            node = stack.pop()
            if node in reachable:
                continue
            reachable.add(node)
            for call in self.nodes[node]["calls"]:
                if call["callee"] not in reachable:
                    stack.append(call["callee"])

        return reachable

    def get_edges_for_subgraph(self, node_set):
        """Get edges where both endpoints are in node_set."""
        return [
            e for e in self.edges
            if e["caller"] in node_set and e["callee"] in node_set
        ]


def build_call_graph(code_obj):
    """Build a call graph by scanning CALL instructions in all code objects."""
    graph = CallGraph()
    all_codes = get_all_code_objects(code_obj)

    for name, co in all_codes:
        graph.add_node(name, co)

    function_name_map = {}
    for name, co in all_codes:
        short_name = co.co_name
        if short_name not in function_name_map:
            function_name_map[short_name] = []
        function_name_map[short_name].append(name)

    def find_callee_name(instructions, call_idx):
        """Find the callee name by simulating stack backward from the CALL instruction."""
        call_instr = instructions[call_idx]
        num_args = call_instr.get("arg", 1) or 1
        stack_depth = num_args + 1

        for i in range(call_idx - 1, max(-1, call_idx - 50), -1):
            if i < 0:
                break
            instr = instructions[i]
            opname = instr["opname"]
            arg = instr.get("arg")
            argval = instr.get("argval")
            argrepr = instr.get("argrepr")

            effect = compute_stack_effect(opname, arg)

            if effect > 0:
                stack_depth -= effect
                if stack_depth <= 0:
                    if opname == "LOAD_GLOBAL":
                        if isinstance(argval, tuple) and len(argval) > 0:
                            name = argval[0]
                            if isinstance(name, str):
                                return name
                        if isinstance(argrepr, str):
                            if " + " in argrepr:
                                return argrepr.split(" + ")[0]
                            return argrepr
                    elif opname == "LOAD_ATTR":
                        if isinstance(argval, str):
                            return argval
                        if isinstance(argrepr, str):
                            return argrepr
                    elif opname == "LOAD_METHOD":
                        if isinstance(argval, str):
                            return argval
                        if isinstance(argrepr, str):
                            return argrepr
                    elif opname == "LOAD_NAME":
                        if isinstance(argval, str):
                            return argval
                        if isinstance(argrepr, str):
                            return argrepr
                    elif opname in ("LOAD_FAST", "LOAD_FAST_BORROW"):
                        if isinstance(argval, str):
                            return argval
                        if isinstance(argrepr, str):
                            return argrepr
                    else:
                        return None
            elif effect < 0:
                stack_depth -= effect

            if opname in CALL_OPS and stack_depth > 0:
                break

        return None

    BUILTIN_FUNCTIONS = {
        "print", "range", "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple",
        "isinstance", "type",
        "append", "extend", "pop", "insert", "remove", "clear", "sort", "reverse", "index", "count",
        "format", "join", "split", "strip", "upper", "lower", "replace", "find", "startswith", "endswith",
        "open", "close", "read", "write", "readline", "readlines",
        "getattr", "setattr", "hasattr", "issubclass",
        "abs", "min", "max", "sum", "sorted", "reversed", "enumerate", "zip", "map", "filter", "reduce",
        "any", "all",
    }

    def resolve_callee(callee_name, caller_name):
        """Resolve a callee name to a fully qualified function name."""
        if not callee_name:
            return None

        if callee_name.startswith("__") and callee_name.endswith("__"):
            if callee_name in function_name_map:
                if len(function_name_map[callee_name]) == 1:
                    return function_name_map[callee_name][0]
            return None

        if callee_name in function_name_map:
            if len(function_name_map[callee_name]) == 1:
                return function_name_map[callee_name][0]
            else:
                prefix = ".".join(caller_name.split(".")[:-1]) if "." in caller_name else ""
                matching = [
                    n for n in function_name_map[callee_name]
                    if n.endswith("." + callee_name) or n == callee_name
                ]
                if len(matching) == 1:
                    return matching[0]
                if prefix:
                    prefixed = prefix + "." + callee_name
                    if prefixed in function_name_map.get(callee_name, []):
                        return prefixed
                return function_name_map[callee_name][0]
        elif callee_name in BUILTIN_FUNCTIONS:
            return callee_name
        return None

    for caller_name, co in all_codes:
        instructions = get_instructions(co)

        for idx, instr in enumerate(instructions):
            if instr["opname"] in CALL_OPS:
                callee_name = find_callee_name(instructions, idx)
                if callee_name:
                    actual_callee = resolve_callee(callee_name, caller_name)
                    if actual_callee:
                        graph.add_edge(
                            caller_name,
                            actual_callee,
                            offset=instr["offset"],
                            line=instr["line"],
                        )

    return graph


def format_call_graph_ascii(graph, root=None, max_depth=None, use_color=True):
    """Format call graph as ASCII tree."""
    lines = []
    lines.append(Colors.colorize("=== Call Graph ===", Colors.BOLD + Colors.CYAN, use_color))
    lines.append("")

    if root:
        if root not in graph.nodes:
            lines.append(Colors.colorize(f"Error: Root function '{root}' not found", Colors.RED, use_color))
            return "\n".join(lines)
        display_nodes = graph.get_reachable_subgraph(root)
        start_nodes = [root]
    else:
        display_nodes = set(graph.node_names)
        start_nodes = [
            n for n in graph.node_names
            if graph.nodes[n]["in_degree"] == 0 or n.startswith("<module>")
        ]
        if not start_nodes:
            start_nodes = [graph.node_names[0]]

    displayed_edges = graph.get_edges_for_subgraph(display_nodes)

    def print_tree(node, prefix="", is_last=True, depth=0, visited=None):
        if visited is None:
            visited = set()

        if max_depth is not None and depth > max_depth:
            return

        if node in visited:
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{Colors.colorize(node, Colors.DIM, use_color)} (already shown)")
            return

        visited.add(node)

        connector = "└── " if is_last else "├── "
        node_color = Colors.WHITE
        if node.startswith("<module>"):
            node_color = Colors.CYAN
        elif any(call["callee"] == node for call in graph.nodes[node]["calls"]):
            node_color = Colors.MAGENTA
        elif graph.nodes[node]["in_degree"] == 0:
            node_color = Colors.YELLOW

        node_str = Colors.colorize(node, node_color + Colors.BOLD, use_color)
        call_count = len(graph.nodes[node]["calls"])
        called_by_count = len(graph.nodes[node]["called_by"])
        suffix = Colors.colorize(
            f" [calls: {call_count}, called by: {called_by_count}]",
            Colors.DIM, use_color)
        lines.append(f"{prefix}{connector}{node_str}{suffix}")

        calls = sorted(graph.nodes[node]["calls"], key=lambda c: c["callee"])
        for i, call in enumerate(calls):
            if call["callee"] not in display_nodes:
                continue
            extension = "    " if is_last else "│   "
            is_last_child = (i == len(calls) - 1)
            print_tree(
                call["callee"],
                prefix + extension,
                is_last_child,
                depth + 1,
                visited.copy()
            )

    for i, start in enumerate(start_nodes):
        if start not in display_nodes:
            continue
        is_last_start = (i == len(start_nodes) - 1)
        if i > 0:
            lines.append("")
        print_tree(start, "", is_last_start, 0, set())

    recursion = graph.get_recursive_calls()
    isolated = graph.get_isolated_functions()

    if recursion["direct"]:
        lines.append("")
        lines.append(Colors.colorize("=== Direct Recursion ===", Colors.BOLD + Colors.MAGENTA, use_color))
        for r in recursion["direct"]:
            lines.append(f"  {Colors.colorize(r['function'], Colors.MAGENTA, use_color)} "
                         f"calls itself at offset {r['offset']} (line {r['line']})")

    if recursion["indirect"]:
        lines.append("")
        lines.append(Colors.colorize("=== Indirect Recursion Cycles ===", Colors.BOLD + Colors.MAGENTA, use_color))
        for r in recursion["indirect"]:
            cycle_str = " → ".join(
                Colors.colorize(n, Colors.MAGENTA, use_color) for n in r["cycle"]
            )
            lines.append(f"  Cycle ({r['length']} nodes): {cycle_str}")

    if isolated:
        lines.append("")
        lines.append(Colors.colorize("=== Isolated Functions (never called) ===", Colors.BOLD + Colors.YELLOW, use_color))
        for iso in sorted(isolated):
            lines.append(f"  {Colors.colorize(iso, Colors.YELLOW, use_color)}")

    lines.append("")
    lines.append(Colors.colorize("=== Legend ===", Colors.BOLD + Colors.DIM, use_color))
    lines.append(f"  {Colors.colorize('Cyan', Colors.CYAN, use_color)}: Module entry point")
    lines.append(f"  {Colors.colorize('Yellow', Colors.YELLOW, use_color)}: Isolated function (never called)")
    lines.append(f"  {Colors.colorize('Magenta', Colors.MAGENTA, use_color)}: Recursive function")
    lines.append("")

    return "\n".join(lines)


def format_call_graph_dot(graph, root=None, output_path=None):
    """Generate Graphviz DOT format call graph."""
    lines = []
    lines.append("digraph callgraph {")
    lines.append('  node [shape=box, style="rounded,filled", fillcolor="#e8f4fd"];')
    lines.append('  edge [fontsize=10, color="#666666"];')
    lines.append("")

    if root:
        display_nodes = graph.get_reachable_subgraph(root)
    else:
        display_nodes = set(graph.node_names)

    for name in sorted(display_nodes):
        if name not in graph.nodes:
            continue
        node = graph.nodes[name]
        label = f"{name}\\n"
        label += f"calls: {node['out_degree']}\\n"
        label += f"called by: {node['in_degree']}"
        label = label.replace('"', "'")

        fillcolor = "#e8f4fd"
        if name.startswith("<module>"):
            fillcolor = "#fffacd"
        elif any(call["callee"] == name for call in node["calls"]):
            fillcolor = "#ffe4e1"
        elif node["in_degree"] == 0:
            fillcolor = "#f0fff0"

        safe_name = name.replace(".", "_").replace("<", "").replace(">", "").replace(" ", "_")
        lines.append(f'  {safe_name} [label="{label}", fillcolor="{fillcolor}"];')

    lines.append("")

    edges_to_show = graph.get_edges_for_subgraph(display_nodes)
    for edge in edges_to_show:
        caller_safe = edge["caller"].replace(".", "_").replace("<", "").replace(">", "").replace(" ", "_")
        callee_safe = edge["callee"].replace(".", "_").replace("<", "").replace(">", "").replace(" ", "_")
        line_label = f"line {edge['line']}" if edge["line"] else ""
        lines.append(f'  {caller_safe} -> {callee_safe} [label="{line_label}"];')

    lines.append("}")

    dot_content = "\n".join(lines)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(dot_content)

    return dot_content


def cmd_callgraph(args):
    """Handle 'callgraph' command."""
    use_color = not args.no_color

    try:
        code_obj = compile_source(args.source)
    except SyntaxError as e:
        print(f"Error: Failed to compile {args.source}: {e}", file=sys.stderr)
        sys.exit(1)

    graph = build_call_graph(code_obj)

    if not graph.nodes:
        print(Colors.colorize("No functions found in the module.", Colors.YELLOW, use_color))
        return

    print(format_call_graph_ascii(graph, args.root, args.depth, use_color))

    if args.dot:
        format_call_graph_dot(graph, args.root, args.dot)
        print(f"Graphviz DOT file written to: {args.dot}")
        print()

    if args.json:
        recursion = graph.get_recursive_calls()
        isolated = graph.get_isolated_functions()

        nodes_data = []
        for name in sorted(graph.node_names):
            node = graph.nodes[name]
            nodes_data.append({
                "name": name,
                "in_degree": node["in_degree"],
                "out_degree": node["out_degree"],
                "calls": node["calls"],
                "called_by": [c["caller"] for c in node["called_by"]],
            })

        json_data = {
            "nodes": nodes_data,
            "edges": graph.edges,
            "recursion": recursion,
            "isolated_functions": isolated,
        }

        if args.root:
            reachable = list(graph.get_reachable_subgraph(args.root))
            json_data["root"] = args.root
            json_data["reachable_from_root"] = reachable

        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, default=str)
        print(f"JSON report written to: {args.json}")


# ========== Diff Function ==========

def diff_bytecodes(instructions1, instructions2, use_color=True):
    """Diff two sets of bytecode instructions."""
    instr_map1 = {i["offset"]: i for i in instructions1}
    instr_map2 = {i["offset"]: i for i in instructions2}

    all_offsets = sorted(set(instr_map1.keys()) | set(instr_map2.keys()))

    diff_result = {
        "added": [],
        "removed": [],
        "modified": [],
        "unchanged": [],
    }

    i = j = 0
    list1 = sorted(instructions1, key=lambda x: x["offset"])
    list2 = sorted(instructions2, key=lambda x: x["offset"])

    offset1_to_idx = {instr["offset"]: idx for idx, instr in enumerate(list1)}
    offset2_to_idx = {instr["offset"]: idx for idx, instr in enumerate(list2)}

    for offset in all_offsets:
        in1 = offset in instr_map1
        in2 = offset in instr_map2

        if in1 and in2:
            i1 = instr_map1[offset]
            i2 = instr_map2[offset]
            if i1["opname"] == i2["opname"] and i1["arg"] == i2["arg"]:
                diff_result["unchanged"].append(i2)
            else:
                diff_result["modified"].append({"old": i1, "new": i2})
        elif in1:
            diff_result["removed"].append(instr_map1[offset])
        elif in2:
            diff_result["added"].append(instr_map2[offset])

    return diff_result


def format_diff(diff_result, use_color=True):
    """Format diff results for display."""
    lines = []
    lines.append(Colors.colorize("=== Bytecode Diff ===", Colors.BOLD + Colors.CYAN, use_color))
    lines.append("")

    lines.append(Colors.colorize(f"Added: {len(diff_result['added'])} instructions",
                                 Colors.BOLD + Colors.GREEN, use_color))
    for instr in diff_result["added"]:
        line = f"  + {instr['offset']:4d} {instr['opname']:20s} {instr['argrepr'] if instr['argrepr'] else ''}"
        lines.append(Colors.colorize(line, Colors.GREEN, use_color))
    lines.append("")

    lines.append(Colors.colorize(f"Removed: {len(diff_result['removed'])} instructions",
                                 Colors.BOLD + Colors.RED, use_color))
    for instr in diff_result["removed"]:
        line = f"  - {instr['offset']:4d} {instr['opname']:20s} {instr['argrepr'] if instr['argrepr'] else ''}"
        lines.append(Colors.colorize(line, Colors.RED, use_color))
    lines.append("")

    lines.append(Colors.colorize(f"Modified: {len(diff_result['modified'])} instructions",
                                 Colors.BOLD + Colors.YELLOW, use_color))
    for m in diff_result["modified"]:
        old = m["old"]
        new = m["new"]
        line_old = f"  - {old['offset']:4d} {old['opname']:20s} {old['argrepr'] if old['argrepr'] else ''}"
        line_new = f"  + {new['offset']:4d} {new['opname']:20s} {new['argrepr'] if new['argrepr'] else ''}"
        lines.append(Colors.colorize(line_old, Colors.RED, use_color))
        lines.append(Colors.colorize(line_new, Colors.GREEN, use_color))
    lines.append("")

    lines.append(Colors.colorize(f"Unchanged: {len(diff_result['unchanged'])} instructions",
                                 Colors.DIM, use_color))
    lines.append("")

    return "\n".join(lines)


# ========== JSON Export ==========

def build_json_output(name, code_obj, instructions, blocks, edges, dataflow_results, optimizations):
    """Build complete JSON output structure."""
    data = {
        "function_name": name,
        "filename": code_obj.co_filename if hasattr(code_obj, 'co_filename') else "",
        "instructions": [
            {
                "offset": i["offset"],
                "opname": i["opname"],
                "opcode": i["opcode"],
                "arg": i["arg"],
                "argrepr": i["argrepr"],
                "line": i["line"],
                "is_jump_target": i["is_jump_target"],
            }
            for i in instructions
        ],
        "basic_blocks": [
            {
                "id": b.id,
                "label": b.label,
                "start_offset": b.start_offset,
                "end_offset": b.end_offset,
                "instructions": [i["offset"] for i in b.instructions],
            }
            for b in blocks
        ],
        "cfg_edges": [
            {
                "from": src.id,
                "to": dst.id,
                "type": kind,
            }
            for src, dst, kind in edges
        ],
        "dataflow": {
            "local_vars": dataflow_results.get("local_vars", []),
            "block_stack_effects": {str(k): v for k, v in dataflow_results.get("block_stack_effects", {}).items()},
            "var_defs": {k: [{"offset": d["offset"], "line": d["line"]} for d in v]
                         for k, v in dataflow_results.get("var_defs", {}).items()},
            "var_uses": {k: [{"offset": u["offset"], "line": u["line"]} for u in v]
                         for k, v in dataflow_results.get("var_uses", {}).items()},
            "unused_vars": dataflow_results.get("unused_vars", []),
            "maybe_uninitialized": dataflow_results.get("maybe_uninitialized", []),
        },
        "optimizations": optimizations,
    }
    return data


# ========== HTML Export ==========

def build_html_report(all_functions_data, output_path):
    """Build interactive HTML report."""
    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ByteView - Bytecode Analysis Report</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #1e1e1e; color: #d4d4d4; }
header { background: #252526; padding: 20px; border-bottom: 1px solid #3e3e42; }
h1 { color: #569cd6; font-size: 24px; }
.container { display: flex; height: calc(100vh - 80px); }
.sidebar { width: 300px; background: #252526; border-right: 1px solid #3e3e42; overflow-y: auto; }
.sidebar h2 { padding: 15px; font-size: 14px; color: #858585; text-transform: uppercase; }
.func-item { padding: 10px 15px; cursor: pointer; border-bottom: 1px solid #2d2d30; }
.func-item:hover { background: #2a2d2e; }
.func-item.active { background: #094771; }
.func-item .name { font-weight: bold; color: #dcdcaa; }
.func-item .detail { font-size: 11px; color: #858585; }
.main { flex: 1; overflow-y: auto; padding: 20px; }
.section { margin-bottom: 30px; }
.section h2 { color: #569cd6; margin-bottom: 15px; padding-bottom: 5px; border-bottom: 1px solid #3e3e42; }
.collapsible { cursor: pointer; user-select: none; }
.collapsible::before { content: '▼ '; display: inline-block; transition: transform 0.2s; }
.collapsible.collapsed::before { transform: rotate(-90deg); }
.instr-table { width: 100%; border-collapse: collapse; font-family: 'Consolas', 'Courier New', monospace; font-size: 13px; }
.instr-table th { text-align: left; padding: 8px; background: #2d2d30; color: #858585; }
.instr-table td { padding: 6px 8px; border-bottom: 1px solid #2d2d30; }
.instr-table tr:hover { background: #2a2d2e; }
.jump-target { border-left: 3px solid #f44747; }
.op-load { color: #4ec9b0; }
.op-store { color: #569cd6; }
.op-binary { color: #c586c0; }
.op-jump { color: #f44747; }
.op-call { color: #dcdcaa; }
.op-return { color: #ce9178; }
.bb-box { background: #2d2d30; border: 1px solid #3e3e42; border-radius: 4px; padding: 10px; margin-bottom: 15px; }
.bb-header { font-weight: bold; color: #dcdcaa; margin-bottom: 10px; padding-bottom: 5px; border-bottom: 1px solid #3e3e42; }
.edges { margin-top: 10px; padding-top: 10px; border-top: 1px dashed #3e3e42; }
.edge { display: inline-block; margin-right: 15px; font-size: 12px; }
.edge-uncond { color: #f44747; }
.edge-cond { color: #569cd6; }
.edge-fall { color: #858585; }
.warn { background: rgba(255, 198, 0, 0.1); border-left: 3px solid #ffc600; padding: 10px; margin-bottom: 10px; }
.info { background: rgba(0, 122, 204, 0.1); border-left: 3px solid #007acc; padding: 10px; margin-bottom: 10px; }
.diff-add { color: #4ec9b0; }
.diff-remove { color: #f44747; }
.dot-container { background: white; border-radius: 4px; padding: 20px; overflow-x: auto; }
.tab-nav { margin-bottom: 20px; border-bottom: 1px solid #3e3e42; }
.tab { display: inline-block; padding: 10px 20px; cursor: pointer; margin-right: 2px; }
.tab:hover { background: #2a2d2e; }
.tab.active { background: #094771; color: white; }
.tab-content { display: none; }
.tab-content.active { display: block; }
</style>
</head>
<body>
<header>
  <h1>ByteView - Bytecode Analysis Report</h1>
</header>
<div class="container">
  <div class="sidebar">
    <h2>Functions</h2>
    <div id="func-list"></div>
  </div>
  <div class="main">
    <div class="tab-nav">
      <div class="tab active" data-tab="disasm">Disassembly</div>
      <div class="tab" data-tab="cfg">Control Flow Graph</div>
      <div class="tab" data-tab="dataflow">Data Flow</div>
      <div class="tab" data-tab="optim">Optimizations</div>
    </div>
    <div id="tab-disasm" class="tab-content active"></div>
    <div id="tab-cfg" class="tab-content"></div>
    <div id="tab-dataflow" class="tab-content"></div>
    <div id="tab-optim" class="tab-content"></div>
  </div>
</div>
<script>
const FUNCTIONS = __FUNCTIONS_DATA__;

function renderFuncList() {
  const list = document.getElementById('func-list');
  FUNCTIONS.forEach((func, idx) => {
    const div = document.createElement('div');
    div.className = 'func-item' + (idx === 0 ? ' active' : '');
    div.dataset.idx = idx;
    div.innerHTML = `<div class="name">${func.function_name}</div>
                     <div class="detail">${func.instructions.length} instructions, ${func.basic_blocks.length} blocks</div>`;
    div.addEventListener('click', () => selectFunction(idx));
    list.appendChild(div);
  });
}

function selectFunction(idx) {
  document.querySelectorAll('.func-item').forEach((el, i) => {
    el.classList.toggle('active', i === idx);
  });
  const func = FUNCTIONS[idx];
  renderDisasm(func);
  renderCFG(func);
  renderDataflow(func);
  renderOptimizations(func);
}

function opClass(opname) {
  if (opname.startsWith('LOAD_')) return 'op-load';
  if (opname.startsWith('STORE_')) return 'op-store';
  if (opname.startsWith('BINARY_') || opname.startsWith('INPLACE_') || opname.startsWith('UNARY_')) return 'op-binary';
  if (opname.startsWith('JUMP_') || opname === 'FOR_ITER') return 'op-jump';
  if (opname.startsWith('CALL_')) return 'op-call';
  if (opname.startsWith('RETURN_')) return 'op-return';
  return '';
}

function renderDisasm(func) {
  let html = '<table class="instr-table"><thead><tr><th>Offset</th><th>Line</th><th>Opcode</th><th>Arg</th><th>Arg Repr</th></tr></thead><tbody>';
  func.instructions.forEach(instr => {
    const jt = instr.is_jump_target ? ' class="jump-target"' : '';
    const oc = opClass(instr.opname);
    html += `<tr${jt}>`;
    html += `<td>${instr.offset}</td>`;
    html += `<td>${instr.line || ''}</td>`;
    html += `<td class="${oc}">${instr.opname}</td>`;
    html += `<td>${instr.arg ?? ''}</td>`;
    html += `<td>${instr.argrepr || ''}</td>`;
    html += '</tr>';
  });
  html += '</tbody></table>';
  document.getElementById('tab-disasm').innerHTML = html;
}

function renderCFG(func) {
  let html = '';
  const edgeMap = {};
  func.cfg_edges.forEach(e => {
    if (!edgeMap[e.from]) edgeMap[e.from] = [];
    edgeMap[e.from].push(e);
  });

  func.basic_blocks.forEach(bb => {
    html += '<div class="bb-box">';
    html += `<div class="bb-header">${bb.label} (offset ${bb.start_offset}-${bb.end_offset})</div>`;
    html += '<table class="instr-table">';
    bb.instructions.forEach(off => {
      const instr = func.instructions.find(i => i.offset === off);
      if (instr) {
        const oc = opClass(instr.opname);
        html += `<tr><td>${instr.offset}</td><td class="${oc}">${instr.opname}</td><td>${instr.argrepr || ''}</td></tr>`;
      }
    });
    html += '</table>';

    if (edgeMap[bb.id]) {
      html += '<div class="edges">';
      edgeMap[bb.id].forEach(e => {
        const cls = e.type === 'unconditional' ? 'edge-uncond' : (e.type === 'conditional' ? 'edge-cond' : 'edge-fall');
        const dst = func.basic_blocks.find(b => b.id === e.to);
        html += `<span class="edge ${cls}">→ ${dst ? dst.label : '?'} (${e.type})</span>`;
      });
      html += '</div>';
    }
    html += '</div>';
  });

  document.getElementById('tab-cfg').innerHTML = html;
}

function renderDataflow(func) {
  const df = func.dataflow;
  let html = '';

  html += '<div class="section"><h3>Stack Effects</h3>';
  func.basic_blocks.forEach(bb => {
    const effect = df.block_stack_effects[bb.id] || 0;
    const sign = effect > 0 ? '+' : '';
    html += `<div>${bb.label}: <strong>${sign}${effect}</strong></div>`;
  });
  html += '</div>';

  html += '<div class="section"><h3>Local Variables</h3>';
  df.local_vars.forEach(v => {
    html += `<div><strong>${v}</strong></div>`;
    const defs = df.var_defs[v] || [];
    const uses = df.var_uses[v] || [];
    if (defs.length) html += `<div style="margin-left:20px">defs: ${defs.map(d => '@' + d.offset).join(', ')}</div>`;
    if (uses.length) html += `<div style="margin-left:20px">uses: ${uses.map(u => '@' + u.offset).join(', ')}</div>`;
    if (!defs.length && !uses.length) html += '<div style="margin-left:20px;color:#858585">(none)</div>';
  });
  html += '</div>';

  if (df.unused_vars && df.unused_vars.length) {
    html += '<div class="section"><h3>Unused Variable Assignments</h3>';
    df.unused_vars.forEach(uv => {
      html += `<div class="warn">Variable <strong>${uv.variable}</strong> assigned at offset ${uv.offset} (line ${uv.line}) is never used</div>`;
    });
    html += '</div>';
  }

  if (df.maybe_uninitialized && df.maybe_uninitialized.length) {
    html += '<div class="section"><h3>Potentially Uninitialized</h3>';
    df.maybe_uninitialized.forEach(mi => {
      html += `<div class="warn">Variable <strong>${mi.variable}</strong> may be uninitialized at offset ${mi.offset} (line ${mi.line})</div>`;
    });
    html += '</div>';
  }

  document.getElementById('tab-dataflow').innerHTML = html;
}

function renderOptimizations(func) {
  let html = '';
  const opts = func.optimizations;

  if (!opts || !opts.length) {
    html = '<div style="color:#4ec9b0">No optimization suggestions found.</div>';
  } else {
    opts.forEach((opt, i) => {
      const cls = opt.severity === 'warning' ? 'warn' : 'info';
      html += `<div class="${cls}">`;
      html += `<strong>[${i+1}] ${opt.type.toUpperCase()}</strong> (${opt.severity})<br>`;
      html += `Location: offset ${opt.start_offset || opt.offset || '?'} (line ${opt.line || '?'})<br>`;
      html += `${opt.description}<br>`;
      html += `<em>${opt.suggestion}</em>`;
      html += '</div>';
    });
  }

  document.getElementById('tab-optim').innerHTML = html;
}

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
  });
});

renderFuncList();
if (FUNCTIONS.length > 0) selectFunction(0);
</script>
</body>
</html>
"""

    json_str = json.dumps(all_functions_data, indent=2)
    html_content = html_template.replace("__FUNCTIONS_DATA__", json_str)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)


# ========== CLI Commands ==========

def cmd_disasm(args):
    """Handle 'disasm' command."""
    use_color = not args.no_color

    try:
        code_obj = compile_source(args.source)
    except SyntaxError as e:
        print(f"Error: Failed to compile {args.source}: {e}", file=sys.stderr)
        sys.exit(1)

    all_codes = get_all_code_objects(code_obj)

    if args.function:
        name, co = find_function(code_obj, args.function)
        if co is None:
            print(f"Error: Function '{args.function}' not found", file=sys.stderr)
            sys.exit(1)
        codes_to_process = [(name, co)]
    else:
        codes_to_process = all_codes

    all_data = []
    for name, co in codes_to_process:
        instructions = get_instructions(co)
        print(disassemble(co, name, use_color))

        blocks = build_basic_blocks(instructions)
        edges = build_cfg_edges(blocks)

        if args.cfg:
            print(cfg_to_ascii(blocks, edges, use_color))

        if args.dot:
            dot_content = cfg_to_dot(blocks, edges, name.replace(".", "_").replace("<", "").replace(">", ""))
            with open(args.dot, "w") as f:
                f.write(dot_content)
            print(f"Graphviz DOT file written to: {args.dot}")
            print()

        dataflow_results = None
        if args.dataflow:
            dataflow_results = analyze_dataflow(blocks, edges, co)
            print(format_dataflow_results(dataflow_results, blocks, use_color))

        optimizations = None
        if args.optimize:
            df_res = dataflow_results if dataflow_results else analyze_dataflow(blocks, edges, co)
            optimizations = analyze_optimizations(blocks, edges, instructions, co, df_res)
            print(format_optimizations(optimizations, use_color))

        if args.json:
            df_res = dataflow_results if dataflow_results else analyze_dataflow(blocks, edges, co)
            json_data = build_json_output(
                name, co, instructions, blocks, edges,
                df_res,
                optimizations if optimizations else analyze_optimizations(blocks, edges, instructions, co, df_res)
            )
            all_data.append(json_data)

        if args.html:
            df_res = dataflow_results if dataflow_results else analyze_dataflow(blocks, edges, co)
            json_data = build_json_output(
                name, co, instructions, blocks, edges,
                df_res,
                optimizations if optimizations else analyze_optimizations(blocks, edges, instructions, co, df_res)
            )
            all_data.append(json_data)

    if args.json:
        json_output = {"functions": all_data} if len(all_data) > 1 else all_data[0]
        with open(args.json, "w") as f:
            json.dump(json_output, f, indent=2)
        print(f"JSON report written to: {args.json}")

    if args.html:
        build_html_report(all_data, args.html)
        print(f"HTML report written to: {args.html}")


def cmd_diff(args):
    """Handle 'diff' command."""
    use_color = not args.no_color

    try:
        code1 = compile_source(args.source1)
        code2 = compile_source(args.source2)
    except SyntaxError as e:
        print(f"Error: Failed to compile: {e}", file=sys.stderr)
        sys.exit(1)

    if args.function:
        name1, co1 = find_function(code1, args.function)
        name2, co2 = find_function(code2, args.function)
        if co1 is None:
            print(f"Error: Function '{args.function}' not found in {args.source1}", file=sys.stderr)
            sys.exit(1)
        if co2 is None:
            print(f"Error: Function '{args.function}' not found in {args.source2}", file=sys.stderr)
            sys.exit(1)
    else:
        all1 = get_all_code_objects(code1)
        all2 = get_all_code_objects(code2)
        if len(all1) >= 2:
            name1, co1 = all1[1]
        else:
            name1, co1 = all1[0]
        if len(all2) >= 2:
            name2, co2 = all2[1]
        else:
            name2, co2 = all2[0]

    instr1 = get_instructions(co1)
    instr2 = get_instructions(co2)

    diff_result = diff_bytecodes(instr1, instr2, use_color)
    print(Colors.colorize(f"Comparing: {args.source1} <-> {args.source2}", Colors.BOLD, use_color))
    print(Colors.colorize(f"Function: {name1}", Colors.CYAN, use_color))
    print()
    print(format_diff(diff_result, use_color))

    if args.json:
        with open(args.json, "w") as f:
            json.dump({
                "file1": args.source1,
                "file2": args.source2,
                "function1": name1,
                "function2": name2,
                "added": diff_result["added"],
                "removed": diff_result["removed"],
                "modified": diff_result["modified"],
                "unchanged_count": len(diff_result["unchanged"]),
            }, f, indent=2)
        print(f"JSON diff written to: {args.json}")


def main():
    parser = argparse.ArgumentParser(
        prog="byteview",
        description="ByteView - Python Bytecode Disassembler and Control Flow Graph Generator"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    disasm_parser = subparsers.add_parser("disasm", help="Disassemble a Python source file")
    disasm_parser.add_argument("source", help="Path to .py source file")
    disasm_parser.add_argument("--function", "-f", help="Disassemble specific function only")
    disasm_parser.add_argument("--cfg", action="store_true", help="Show control flow graph (ASCII)")
    disasm_parser.add_argument("--dot", help="Output Graphviz DOT file")
    disasm_parser.add_argument("--dataflow", action="store_true", help="Run data flow analysis")
    disasm_parser.add_argument("--optimize", action="store_true", help="Show optimization suggestions")
    disasm_parser.add_argument("--json", help="Export results to JSON file")
    disasm_parser.add_argument("--html", help="Export interactive HTML report")
    disasm_parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    diff_parser = subparsers.add_parser("diff", help="Diff bytecode of two Python files")
    diff_parser.add_argument("source1", help="First .py source file")
    diff_parser.add_argument("source2", help="Second .py source file")
    diff_parser.add_argument("--function", "-f", help="Function name to compare")
    diff_parser.add_argument("--json", help="Export diff to JSON file")
    diff_parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    rewrite_parser = subparsers.add_parser("rewrite", help="Auto-rewrite source code with optimizations")
    rewrite_parser.add_argument("source", help="Path to .py source file")
    rewrite_parser.add_argument("--output", "-o", help="Output file path (default: <source>_optimized.py)")
    rewrite_parser.add_argument("--function", "-f", help="Optimize specific function only")
    rewrite_parser.add_argument("--json", help="Export rewrite report to JSON file")
    rewrite_parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    complexity_parser = subparsers.add_parser("complexity", help="Analyze function complexity and loops")
    complexity_parser.add_argument("source", help="Path to .py source file")
    complexity_parser.add_argument("--function", "-f", help="Analyze specific function only")
    complexity_parser.add_argument("--json", help="Export complexity report to JSON file")
    complexity_parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    callgraph_parser = subparsers.add_parser("callgraph", help="Generate function call graph")
    callgraph_parser.add_argument("source", help="Path to .py source file")
    callgraph_parser.add_argument("--root", "-r", help="Start from specific root function")
    callgraph_parser.add_argument("--depth", "-d", type=int, help="Maximum display depth")
    callgraph_parser.add_argument("--dot", help="Output Graphviz DOT file")
    callgraph_parser.add_argument("--json", help="Export call graph to JSON file")
    callgraph_parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    args = parser.parse_args()

    if args.command == "disasm":
        cmd_disasm(args)
    elif args.command == "diff":
        cmd_diff(args)
    elif args.command == "rewrite":
        cmd_rewrite(args)
    elif args.command == "complexity":
        cmd_complexity(args)
    elif args.command == "callgraph":
        cmd_callgraph(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
