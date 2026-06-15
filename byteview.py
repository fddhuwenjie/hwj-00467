#!/usr/bin/env python3
"""ByteView - Python Bytecode Disassembler and Control Flow Graph Generator."""

import argparse
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

def analyze_optimizations(blocks, edges, instructions, code_obj):
    """Detect optimization opportunities."""
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
                "description": f"Redundant LOAD_FAST + POP_TOP at offset {curr['offset']}",
                "suggestion": "The loaded value is immediately discarded; consider removing",
            })
        if curr["opname"] == "LOAD_CONST" and nxt["opname"] == "POP_TOP":
            suggestions.append({
                "type": "redundant_operation",
                "severity": "info",
                "offset": curr["offset"],
                "line": curr["line"],
                "description": f"Redundant LOAD_CONST + POP_TOP at offset {curr['offset']}",
                "suggestion": "The constant is immediately discarded; consider removing",
            })

    for block in blocks:
        for i, instr in enumerate(block.instructions):
            if instr["opname"] == "LOAD_CONST" and instr["arg"] is not None:
                const_val = instr["argval"]
                if i + 1 < len(block.instructions):
                    next_instr = block.instructions[i + 1]
                    if next_instr["opname"] == "LOAD_CONST":
                        if i + 2 < len(block.instructions):
                            third = block.instructions[i + 2]
                            if third["opname"] in BINARY_OPS:
                                suggestions.append({
                                    "type": "constant_folding",
                                    "severity": "info",
                                    "offset": instr["offset"],
                                    "line": instr["line"],
                                    "description": f"Constant expression at offset {instr['offset']} can be folded",
                                    "suggestion": "Two constants followed by binary operation can be pre-computed",
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
                        "description": f"Trivial jump at offset {instr['offset']} jumps to next instruction",
                        "suggestion": "Consider removing the redundant jump",
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
            optimizations = analyze_optimizations(blocks, edges, instructions, co)
            print(format_optimizations(optimizations, use_color))

        if args.json:
            json_data = build_json_output(
                name, co, instructions, blocks, edges,
                dataflow_results if dataflow_results else analyze_dataflow(blocks, edges, co),
                optimizations if optimizations else analyze_optimizations(blocks, edges, instructions, co)
            )
            all_data.append(json_data)

        if args.html:
            json_data = build_json_output(
                name, co, instructions, blocks, edges,
                dataflow_results if dataflow_results else analyze_dataflow(blocks, edges, co),
                optimizations if optimizations else analyze_optimizations(blocks, edges, instructions, co)
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

    args = parser.parse_args()

    if args.command == "disasm":
        cmd_disasm(args)
    elif args.command == "diff":
        cmd_diff(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
