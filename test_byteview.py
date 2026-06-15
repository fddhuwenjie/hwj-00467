"""pytest unit tests for ByteView core modules."""

import pytest
import sys
from types import CodeType

sys.path.insert(0, '/Users/huwenjie/项目/胡文杰题目汇总/项目/hwj-00467')

from byteview import (
    BasicBlock,
    build_basic_blocks,
    build_cfg_edges,
    get_instructions,
    analyze_dataflow,
    CallGraph,
    build_call_graph,
    get_all_code_objects,
)


def compile_function(source: str) -> CodeType:
    """Compile source code and extract the first function's code object."""
    compiled = compile(source, "<test>", "exec")
    for const in compiled.co_consts:
        if isinstance(const, CodeType):
            return const
    raise ValueError("No function found in source")


class TestControlFlowGraph:
    """Test control flow graph construction logic."""

    def test_if_else_basic_blocks_count(self):
        """Verify a simple if-else function is split into expected number of basic blocks."""
        source = """
def test_func(x):
    if x > 0:
        result = 1
    else:
        result = 0
    return result
"""
        code_obj = compile_function(source)
        instructions = get_instructions(code_obj)
        blocks = build_basic_blocks(instructions)

        assert len(blocks) >= 3, f"Expected at least 3 basic blocks, got {len(blocks)}"

        block_ids = [b.id for b in blocks]
        assert block_ids == list(range(len(blocks))), "Block IDs should be sequential"

    def test_if_else_edges_conditional_and_fallthrough(self):
        """Verify conditional jump edges and fallthrough edges are both recognized."""
        source = """
def test_func(x):
    if x > 0:
        result = 1
    else:
        result = 0
    return result
"""
        code_obj = compile_function(source)
        instructions = get_instructions(code_obj)
        blocks = build_basic_blocks(instructions)
        edges = build_cfg_edges(blocks)

        has_conditional = any(kind == "conditional" for _, _, kind in edges)
        has_fallthrough = any(kind == "fallthrough" for _, _, kind in edges)

        assert has_conditional, "CFG should have conditional edges for if-else"
        assert has_fallthrough, "CFG should have fallthrough edges for sequential execution"

        edge_kinds = [kind for _, _, kind in edges]
        assert "conditional" in edge_kinds
        assert "fallthrough" in edge_kinds

    def test_for_loop_back_edge_cycle(self):
        """Verify a for loop function detects back edges forming cycles."""
        source = """
def test_func(n):
    total = 0
    for i in range(n):
        total += i
    return total
"""
        code_obj = compile_function(source)
        instructions = get_instructions(code_obj)
        blocks = build_basic_blocks(instructions)
        edges = build_cfg_edges(blocks)

        has_loop_body = any(kind == "loop_body" for _, _, kind in edges)
        has_loop_exit = any(kind == "loop_exit" for _, _, kind in edges)

        assert has_loop_body, "CFG should have loop_body edges for for-loop iteration"
        assert has_loop_exit, "CFG should have loop_exit edges for for-loop exit"

        block_ids = {b.id for b in blocks}
        visited = set()
        has_cycle = False

        def dfs(block_id, path):
            nonlocal has_cycle
            if has_cycle:
                return
            if block_id in path:
                has_cycle = True
                return
            if block_id in visited:
                return
            visited.add(block_id)
            path.add(block_id)
            for src, dst, _ in edges:
                if src.id == block_id:
                    dfs(dst.id, path.copy())

        for block in blocks:
            dfs(block.id, set())
            if has_cycle:
                break

        assert has_cycle, "For loop should create a cycle in the control flow graph"


class TestDataFlowAnalysis:
    """Test data flow analysis logic."""

    def test_unused_variable_assignment_detected(self):
        """Construct a function with unused variable assignment and verify it's detected."""
        source = """
def test_func(x):
    unused = 42
    result = x * 2
    return result
"""
        code_obj = compile_function(source)
        instructions = get_instructions(code_obj)
        blocks = build_basic_blocks(instructions)
        edges = build_cfg_edges(blocks)
        results = analyze_dataflow(blocks, edges, code_obj)

        unused_vars = [uv["variable"] for uv in results["unused_vars"]]
        assert "unused" in unused_vars, f"Expected 'unused' to be detected as unused, got {unused_vars}"
        assert "result" not in unused_vars, "'result' should not be flagged as unused"
        assert "x" not in unused_vars, "'x' should not be flagged as unused"

        unused_info = [uv for uv in results["unused_vars"] if uv["variable"] == "unused"][0]
        assert unused_info["type"] == "unused_assignment"
        assert unused_info["line"] is not None
        assert unused_info["offset"] is not None

    def test_parameter_always_defined_no_false_positive(self):
        """Verify parameters defined on all paths are not falsely reported as uninitialized."""
        source = """
def test_func(x, y):
    if x > 0:
        z = x + y
    else:
        z = x - y
    return z
"""
        code_obj = compile_function(source)
        instructions = get_instructions(code_obj)
        blocks = build_basic_blocks(instructions)
        edges = build_cfg_edges(blocks)
        results = analyze_dataflow(blocks, edges, code_obj)

        maybe_uninit_vars = [mi["variable"] for mi in results["maybe_uninitialized"]]
        assert "x" not in maybe_uninit_vars, "Parameter 'x' should not be reported as uninitialized"
        assert "y" not in maybe_uninit_vars, "Parameter 'y' should not be reported as uninitialized"
        assert "z" not in maybe_uninit_vars, "'z' is defined on all paths, should not be uninitialized"

    def test_branch_defined_variable_maybe_uninitialized(self):
        """Verify variable defined only in one branch is flagged when used in another."""
        source = """
def test_func(flag):
    if flag:
        value = 10
    result = value * 2
    return result
"""
        code_obj = compile_function(source)
        instructions = get_instructions(code_obj)
        blocks = build_basic_blocks(instructions)
        edges = build_cfg_edges(blocks)
        results = analyze_dataflow(blocks, edges, code_obj)

        maybe_uninit_vars = [mi["variable"] for mi in results["maybe_uninitialized"]]
        assert "value" in maybe_uninit_vars, \
            f"'value' defined only in if-branch should be flagged as maybe uninitialized, got {maybe_uninit_vars}"

        value_info = [mi for mi in results["maybe_uninitialized"] if mi["variable"] == "value"][0]
        assert value_info["block"] is not None
        assert value_info["offset"] is not None
        assert value_info["line"] is not None


class TestCallGraph:
    """Test call graph construction logic."""

    def test_direct_recursion_detected(self):
        """Construct a directly recursive function and verify recursive call is recognized."""
        source = """
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)
"""
        compiled = compile(source, "<test>", "exec")
        graph = build_call_graph(compiled)

        recursive_calls = graph.get_recursive_calls()
        direct_recursion = recursive_calls["direct"]

        factorial_names = [n for n in graph.node_names if "factorial" in n]
        assert len(factorial_names) > 0, "factorial function should be in call graph"

        factorial_fullname = factorial_names[0]
        direct_callers = [dr["function"] for dr in direct_recursion]

        assert factorial_fullname in direct_callers, \
            f"Direct recursion should be detected for factorial, got {direct_callers}"

    def test_indirect_recursion_cycle_detected(self):
        """Construct two mutually calling functions and verify indirect recursion cycle."""
        source = """
def is_even(n):
    if n == 0:
        return True
    return is_odd(n - 1)

def is_odd(n):
    if n == 0:
        return False
    return is_even(n - 1)
"""
        compiled = compile(source, "<test>", "exec")
        graph = build_call_graph(compiled)

        recursive_calls = graph.get_recursive_calls()
        indirect_recursion = recursive_calls["indirect"]

        assert len(indirect_recursion) > 0, "Indirect recursion cycle should be detected"

        cycle_found = False
        for ir in indirect_recursion:
            cycle = ir["cycle"]
            has_even = any("is_even" in str(node) for node in cycle)
            has_odd = any("is_odd" in str(node) for node in cycle)
            if has_even and has_odd:
                cycle_found = True
                assert ir["length"] >= 2, f"Indirect cycle should have at least 2 nodes, got {ir['length']}"
                break

        assert cycle_found, "Indirect recursion between is_even and is_odd should be detected"

    def test_isolated_function_detected(self):
        """Construct an orphan function never called and verify it's marked as isolated."""
        source = """
def used_func(x):
    return x * 2

def orphan_func(x):
    return x + 1

def main():
    result = used_func(5)
    return result
"""
        compiled = compile(source, "<test>", "exec")
        graph = build_call_graph(compiled)

        isolated = graph.get_isolated_functions()

        orphan_names = [n for n in graph.node_names if "orphan_func" in n]
        assert len(orphan_names) > 0, "orphan_func should be in call graph"

        orphan_fullname = orphan_names[0]
        assert orphan_fullname in isolated, \
            f"orphan_func should be detected as isolated, isolated list: {isolated}"

        used_names = [n for n in graph.node_names if "used_func" in n]
        if used_names:
            assert used_names[0] not in isolated, "used_func should not be isolated (it's called by main)"

        main_names = [n for n in graph.node_names if "main" in n]
        if main_names:
            assert main_names[0] in isolated, "main should be isolated (no one calls it in this test)"

    def test_call_graph_node_and_edge_counts(self):
        """Verify call graph has correct number of nodes and edges."""
        source = """
def a(x):
    return b(x) + c(x)

def b(x):
    return x * 2

def c(x):
    return x + 1

def main():
    return a(5)
"""
        compiled = compile(source, "<test>", "exec")
        graph = build_call_graph(compiled)

        func_names = ["a", "b", "c", "main"]
        for name in func_names:
            assert any(name in node_name for node_name in graph.node_names), \
                f"Function {name} should be in call graph"

        function_nodes = [n for n in graph.node_names if n != "<module>"]
        assert len(function_nodes) >= 4, f"Expected at least 4 function nodes, got {len(function_nodes)}"

        assert len(graph.edges) >= 3, f"Expected at least 3 call edges, got {len(graph.edges)}"

        edge_pairs = [(e["caller"], e["callee"]) for e in graph.edges]
        a_names = [n for n in graph.node_names if n.endswith(".a") or n == "a"]
        b_names = [n for n in graph.node_names if n.endswith(".b") or n == "b"]
        c_names = [n for n in graph.node_names if n.endswith(".c") or n == "c"]
        main_names = [n for n in graph.node_names if n.endswith(".main") or n == "main"]

        if a_names and b_names:
            assert any(caller == a_names[0] and callee == b_names[0] for caller, callee in edge_pairs), \
                "a should call b"
        if a_names and c_names:
            assert any(caller == a_names[0] and callee == c_names[0] for caller, callee in edge_pairs), \
                "a should call c"
        if main_names and a_names:
            assert any(caller == main_names[0] and callee == a_names[0] for caller, callee in edge_pairs), \
                "main should call a"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
