from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from parser import ASTNode
from tac import TACInstr


def _c_type_for(dtype: str) -> str:
    if dtype == "int":
        return "long long"
    if dtype == "float":
        return "double"
    if dtype == "bool":
        return "long long"
    if dtype == "char":
        return "char"
    if dtype == "string":
        return "char*"
    return "long long"


def _c_scanf_for(dtype: str) -> str:
    if dtype == "int":
        return "%lld"
    if dtype == "float":
        return "%lf"
    if dtype == "bool":
        # We'll treat bool input as integer 0/1 for now.
        return "%lld"
    if dtype == "char":
        return " %c"
    # string input isn't implemented in this minimal generator
    return "%s"


def _c_printf_for(dtype: str) -> str:
    if dtype == "int":
        return "%lld"
    if dtype == "bool":
        return "%lld"
    if dtype == "float":
        return "%g"
    if dtype == "char":
        return "%c"
    if dtype == "string":
        return "%s"
    return "%lld"


def _escape_c_quotes(s: str) -> str:
    return s.replace('"', '\\"')


def _operand_to_c_expr(operand: Any) -> str:
    if operand is None:
        return "0"
    if isinstance(operand, str):
        if operand == "yuh":
            return "1"
        if operand == "naur":
            return "0"
        if operand.startswith("t") or operand.startswith("id"):
            return operand
        # numeric literals usually come through as "5" or "3.14"
        # but in case lexer produced them as str, keep them.
        if operand.replace(".", "", 1).lstrip("-").isdigit():
            return operand
        return operand
    if isinstance(operand, bool):
        return "1" if operand else "0"
    if isinstance(operand, (int, float)):
        return str(operand)
    return str(operand)


def _extract_string_from_exhale_output(output_node: ASTNode) -> Optional[str]:
    """
    Extracts the raw inner string content from a simple:
      exhale("...@{x}...")~
    representation in your AST.

    Returns the inner string (without surrounding quotes), or None.
    """
    if output_node is None or getattr(output_node, "type", None) != "output":
        return None
    if not getattr(output_node, "children", None) or not output_node.children:
        return None
    lit = output_node.children[0]
    if getattr(lit, "type", None) != "literal":
        return None
    if not getattr(lit, "children", None) or not lit.children:
        return None
    concat = lit.children[0]
    if getattr(concat, "type", None) != "output_content":
        return None
    raw = getattr(concat, "value", None)
    if not isinstance(raw, str):
        return None
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1]
    return None


def _gen_exhale_stmt(output_node: ASTNode, semantic: Any) -> List[str]:
    """
    Generates C statements for:
      exhale(output)~
    Minimal support:
      - string literals with @{identifier} interpolation
    """
    raw = _extract_string_from_exhale_output(output_node)
    if raw is None:
        # Fallback: we don't know how to print this form yet.
        return ['/* exhale: unsupported output node form */']

    # Build reverse map: actual name -> token type (idX)
    id_map = getattr(semantic, "identifier_map", {}) or {}
    reverse_ids: Dict[str, str] = {actual: token_type for token_type, actual in id_map.items()}

    # Replace @{name} occurrences with proper printf format + args.
    interp_re = re.compile(r"(?<!\\)@\{([^}]+)\}")

    args: List[str] = []
    fmt_parts: List[str] = []

    last = 0
    for m in interp_re.finditer(raw):
        # literal segment before placeholder
        before = raw[last:m.start()]
        # Escape % to avoid accidental format specifiers.
        before = before.replace("%", "%%")
        fmt_parts.append(before)

        inner = m.group(1).strip()
        # Support only simple identifier placeholders: @{sum2}
        name_m = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)", inner)
        if not name_m:
            # Unknown interpolation expression; keep it as literal text.
            fmt_parts.append(re.escape(m.group(0)))
            last = m.end()
            continue

        actual_name = name_m.group(1)
        tok_type = reverse_ids.get(actual_name)
        if not tok_type:
            fmt_parts.append(re.escape(m.group(0)))
            last = m.end()
            continue

        dtype = getattr(semantic, "declared_types", {}).get(tok_type, "int")
        fmt_parts.append(_c_printf_for(dtype))
        args.append(tok_type)
        last = m.end()

    # remaining tail
    tail = raw[last:]
    tail = tail.replace("%", "%%")
    fmt_parts.append(tail)

    fmt = "".join(fmt_parts)
    fmt_c = '"' + _escape_c_quotes(fmt) + '"'
    if args:
        return [f'printf({fmt_c}, {", ".join(args)});']
    return [f'printf({fmt_c});']


def generate_c_program(tac_code: Sequence[TACInstr], semantic: Any) -> str:
    """
    Minimal TAC -> C generator.

    Produces a C program using:
    - goto + labels for control flow
    - scanf for INHALE
    - printf for EXHALE (string interpolation only, minimal)
    """
    declared_types: Dict[str, str] = getattr(semantic, "declared_types", {}) or {}
    identifier_map: Dict[str, str] = getattr(semantic, "identifier_map", {}) or {}

    # Collect identifiers used in TAC
    used_vars: set[str] = set()
    used_temps: set[str] = set()
    temp_type: Dict[str, str] = {}

    def maybe_add_operand(opnd: Any) -> None:
        if isinstance(opnd, str):
            if opnd.startswith("id"):
                used_vars.add(opnd)
            elif opnd.startswith("t"):
                used_temps.add(opnd)

    for instr in tac_code:
        maybe_add_operand(getattr(instr, "arg1", None))
        maybe_add_operand(getattr(instr, "arg2", None))
        if isinstance(instr.result, str):
            if instr.result.startswith("id"):
                used_vars.add(instr.result)
            elif instr.result.startswith("t"):
                used_temps.add(instr.result)
                if getattr(instr, "value_type", None):
                    temp_type[instr.result] = instr.value_type

    # Default temp types if not annotated
    for t in used_temps:
        if t not in temp_type:
            temp_type[t] = "float"

    # Declare C variables
    lines: List[str] = []
    lines.append("#include <stdio.h>")
    lines.append("#include <stdbool.h>")
    lines.append("")
    lines.append("int main() {")

    # Variable declarations
    for vid in sorted(used_vars):
        dtype = declared_types.get(vid, "int")
        ctyp = _c_type_for(dtype)
        lines.append(f"    {ctyp} {vid};")

    for t in sorted(used_temps):
        dtype = temp_type.get(t, "float")
        ctyp = _c_type_for(dtype)
        # temps as long long/double/char; init to 0/0.0 for deterministic C.
        init = "0"
        if ctyp == "double":
            init = "0.0"
        lines.append(f"    {ctyp} {t} = {init};")

    # Emit TAC instructions as C statements
    label_set: set[str] = set(instr.result for instr in tac_code if instr.op == "LABEL" and isinstance(instr.result, str))
    for instr in tac_code:
        op = instr.op
        if op == "LABEL":
            if isinstance(instr.result, str):
                lines.append(f"{instr.result}: ;")
            continue
        if op == "GOTO":
            lines.append(f"    goto {instr.result};")
            continue
        if op == "IF_TRUE_GOTO":
            cond_expr = _operand_to_c_expr(instr.arg1)
            # Cond may be float/double or int; treat non-zero as true.
            # We'll just use != 0 here.
            lines.append(f"    if ({cond_expr} != 0) goto {instr.result};")
            continue
        if op == "ASSIGN":
            dst = instr.result
            rhs = _operand_to_c_expr(instr.arg1)
            if isinstance(dst, str) and dst.startswith("id"):
                dtype = declared_types.get(dst, "int")
            elif isinstance(dst, str) and dst.startswith("t"):
                dtype = temp_type.get(dst, "float")
            else:
                dtype = "int"
            ctyp = _c_type_for(dtype)
            lines.append(f"    {dst} = ({ctyp})({rhs});")
            continue
        if op == "INHALE":
            vid = instr.result
            dtype = declared_types.get(vid, "int")
            fmt = _c_scanf_for(dtype)
            if dtype in ("string",):
                lines.append(f"    scanf(\"{fmt}\", {vid});")
            elif dtype in ("char",):
                lines.append(f"    scanf(\"{fmt}\", &{vid});")
            elif dtype in ("float",):
                lines.append(f"    scanf(\"{fmt}\", &{vid});")
            else:
                # int/bool -> long long
                lines.append(f"    scanf(\"{fmt}\", &{vid});")
            continue
        if op == "EXHALE":
            out_node = instr.arg1
            stmt_lines = _gen_exhale_stmt(out_node, semantic)
            for s in stmt_lines:
                lines.append(f"    {s}")
            lines.append("    fflush(stdout);")
            continue
        if op == "INCDEC":
            inc_op = instr.arg1  # '++' or '--'
            vid = instr.result
            if inc_op == "++":
                lines.append(f"    {vid}++;")
            elif inc_op == "--":
                lines.append(f"    {vid}--;")
            else:
                lines.append(f"    /* INCDEC unsupported op {inc_op} */")
            continue
        if op == "UMINUS":
            dst = instr.result
            rhs = _operand_to_c_expr(instr.arg1)
            lines.append(f"    {dst} = -({rhs});")
            continue

        # Binary operations are emitted directly with op set to '+', '-', '*', '/', '>', ...
        if op in {"+", "-", "*", "/", "%", ">", "<", ">=", "<=", "==", "!=", "||", "&&"}:
            dst = instr.result
            a = _operand_to_c_expr(instr.arg1)
            b = _operand_to_c_expr(instr.arg2)

            # Logical ops: normalize operands for C truthiness.
            if op in {"||", "&&"}:
                lines.append(f"    {dst} = (({a} != 0) {op} ({b} != 0));")
                continue

            lines.append(f"    {dst} = ({a} {op} {b});")
            continue

        # Unknown op
        lines.append(f"    /* unsupported TAC op: {op} */")

    lines.append("    return 0;")
    lines.append("}")
    return "\n".join(lines)

