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
        return "%.6f"
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


# ---------------------------------------------------------------------------
# Extended TAC -> C helpers (structs, indexed access, ASSIGN_WITH_ACCESS)
# ---------------------------------------------------------------------------

_C_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _sanitize_c_ident(name: str) -> str:
    if _C_IDENT_RE.match(name or ""):
        return name
    safe = re.sub(r"[^A-Za-z0-9_]", "_", name or "m")
    if safe and safe[0].isdigit():
        safe = "m_" + safe
    return safe or "m"


def _actual_name(identifier_map: Dict[str, str], token: str) -> str:
    return identifier_map.get(token, token)


def _struct_tag(struct_type_token: str, identifier_map: Dict[str, str]) -> str:
    return "oxgust_" + _sanitize_c_ident(_actual_name(identifier_map, struct_type_token))


def _member_field_c(identifier_map: Dict[str, str], member_token: str) -> str:
    return _sanitize_c_ident(_actual_name(identifier_map, member_token))


def _is_struct_type(dtype: Optional[str], structures: Dict[str, Any]) -> bool:
    return bool(dtype) and dtype in structures


def _emit_struct_typedefs(semantic: Any, lines: List[str]) -> None:
    structures: Dict[str, Any] = getattr(semantic, "structures", {}) or {}
    identifier_map: Dict[str, str] = getattr(semantic, "identifier_map", {}) or {}
    if not structures:
        return
    for struct_tok, members in structures.items():
        tag = _struct_tag(struct_tok, identifier_map)
        lines.append(f"typedef struct {{")
        for m_tok, m_ty in members.items():
            lines.append(f"    {_c_type_for(m_ty)} {_member_field_c(identifier_map, m_tok)};")
        lines.append(f"}} {tag};")
        lines.append("")


def _arith_tail_to_c(left: str, tail_node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if tail_node is None or getattr(tail_node, "type", None) == "arith_tail_empty":
        return left
    if getattr(tail_node, "type", None) == "arith_tail" and tail_node.children and len(tail_node.children) >= 3:
        op = tail_node.children[0].children[0].value
        term = tail_node.children[1]
        next_tail = tail_node.children[2]
        rhs = _term_to_c(term, idmap)
        return _arith_tail_to_c(f"({left} {op} {rhs})", next_tail, idmap)
    return left


def _term_tail_to_c(left: str, tail_node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if tail_node is None or getattr(tail_node, "type", None) == "term_tail_empty":
        return left
    if getattr(tail_node, "type", None) == "term_tail" and tail_node.children and len(tail_node.children) >= 3:
        op = tail_node.children[0].children[0].value
        fac = tail_node.children[1]
        next_tail = tail_node.children[2]
        rhs = _factor_to_c(fac, idmap)
        return _term_tail_to_c(f"({left} {op} {rhs})", next_tail, idmap)
    return left


def _term_to_c(node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if node is None or not getattr(node, "children", None) or len(node.children) < 2:
        return "0"
    fac, term_tail = node.children[0], node.children[1]
    left = _factor_to_c(fac, idmap)
    return _term_tail_to_c(left, term_tail, idmap)


def _arith_expr_to_c(node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    """arith_expr -> term arith_tail"""
    if node is None or getattr(node, "type", None) != "arith_expr" or not node.children:
        return "0"
    term_node = node.children[0]
    arith_tail = node.children[1] if len(node.children) > 1 else None
    left = _term_to_c(term_node, idmap)
    return _arith_tail_to_c(left, arith_tail, idmap)


def _rela_expr_to_c(node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if node is None or not getattr(node, "children", None):
        return "0"
    left = _arith_expr_to_c(node.children[0], idmap)
    rela_tail = node.children[1] if len(node.children) > 1 else None
    if rela_tail is None or getattr(rela_tail, "type", None) == "rela_tail_empty":
        return left
    if getattr(rela_tail, "type", None) == "rela_tail" and rela_tail.children and len(rela_tail.children) >= 2:
        op = rela_tail.children[0].children[0].value
        right = _arith_expr_to_c(rela_tail.children[1], idmap)
        return f"({left} {op} {right})"
    return left


def _and_tail_to_c(left: str, tail_node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if tail_node is None or getattr(tail_node, "type", None) == "and_tail_empty":
        return left
    if getattr(tail_node, "type", None) == "and_tail" and tail_node.children and len(tail_node.children) >= 2:
        rela = tail_node.children[0]
        nxt = tail_node.children[1]
        rhs = _rela_expr_to_c(rela, idmap)
        combined = f"(({left}) != 0 && ({rhs}) != 0)"
        return _and_tail_to_c(combined, nxt, idmap)
    return left


def _and_expr_to_c(node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if node is None or not getattr(node, "children", None):
        return "0"
    left = _rela_expr_to_c(node.children[0], idmap)
    and_tail = node.children[1] if len(node.children) > 1 else None
    return _and_tail_to_c(left, and_tail, idmap)


def _or_tail_to_c(left: str, tail_node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if tail_node is None or getattr(tail_node, "type", None) == "or_tail_empty":
        return left
    if getattr(tail_node, "type", None) == "or_tail" and tail_node.children and len(tail_node.children) >= 2:
        rhs_and = tail_node.children[0]
        nxt = tail_node.children[1]
        rhs = _and_expr_to_c(rhs_and, idmap)
        combined = f"(({left}) != 0 || ({rhs}) != 0)"
        return _or_tail_to_c(combined, nxt, idmap)
    return left


def _logic_expr_to_c(node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if node is None or not getattr(node, "children", None):
        return "0"
    left = _and_expr_to_c(node.children[0], idmap)
    or_tail = node.children[1] if len(node.children) > 1 else None
    return _or_tail_to_c(left, or_tail, idmap)


def _factor_to_c(node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if node is None:
        return "0"
    t = getattr(node, "type", None)
    if t == "factor" and node.children:
        return _primary_to_c(node.children[0], idmap)
    if t == "primary" and node.children:
        return _primary_to_c(node, idmap)
    return "0"


def _primary_to_c(node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if node is None or not node.children:
        return "0"
    inner = node.children[0]
    it = getattr(inner, "type", None)
    if it == "expr":
        return _expr_ast_to_c(inner, idmap)
    if it == "negate":
        return _negate_to_c(inner, idmap)
    if it == "output":
        return _output_to_c(inner, idmap)
    if it == "logic_expr":
        return _logic_expr_to_c(inner, idmap)
    return "0"


def _negate_to_c(node: ASTNode, idmap: Dict[str, str]) -> str:
    if not node.children:
        return "0"
    if getattr(node.children[0], "type", None) == "expr":
        return f"(-({_expr_ast_to_c(node.children[0], idmap)}))"
    if len(node.children) >= 2:
        vid = getattr(node.children[0], "value", None)
        if vid is None:
            return "0"
        return f"(-({_lvalue_from_id_access(str(vid), node.children[1], idmap)}))"
    return f"(-({_expr_ast_to_c(node.children[0], idmap)}))"


def _output_to_c(node: ASTNode, idmap: Dict[str, str]) -> str:
    if not node.children:
        return "0"
    lit = node.children[0]
    return _literal_piece_to_c(lit, idmap)


def _literal_piece_to_c(node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if node is None:
        return "0"
    if getattr(node, "type", None) == "literal" and node.children:
        ch0 = node.children[0]
        if getattr(ch0, "type", None) == "value":
            return _value_to_c(getattr(ch0, "value", None))
        return _literal_piece_to_c(ch0, idmap)
    if getattr(node, "type", None) == "value":
        return _value_to_c(getattr(node, "value", None))
    if getattr(node, "type", None) == "output_content":
        return _value_to_c(getattr(node, "value", None))
    if getattr(node, "type", None) == "identifier":
        return _identifier_to_c(node, idmap)
    return "0"


def _value_to_c(raw: Any) -> str:
    if raw is None:
        return "0"
    if isinstance(raw, str):
        if raw in ("yuh", "naur"):
            return "1" if raw == "yuh" else "0"
        if raw.startswith('"') and raw.endswith('"'):
            inner = raw[1:-1].replace("\\", "\\\\").replace('"', '\\"')
            return f'"{inner}"'
        if raw.startswith("'") and raw.endswith("'"):
            ch = raw[1:-1] if len(raw) >= 2 else ""
            if ch == "":
                return "0"
            esc = ch.replace("\\", "\\\\").replace("'", "\\'")
            return f"'{esc}'"
    return str(raw)


def _identifier_to_c(node: ASTNode, idmap: Dict[str, str]) -> str:
    if not getattr(node, "children", None) or not node.children:
        return str(getattr(node, "value", "0"))
    first = node.children[0]
    id_tail = node.children[1]
    if getattr(id_tail, "type", None) != "id_tail" or not id_tail.children:
        return str(getattr(first, "value", "0"))
    tail0 = id_tail.children[0]
    if getattr(tail0, "type", None) == "id_access":
        return _lvalue_from_id_access(str(first.value), tail0, idmap)
    if getattr(tail0, "type", None) in ("param_opts", "param_opts_empty"):
        return "0"
    return str(getattr(first, "value", "0"))


def _expr_ast_to_c(node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    """AST expression (assignment RHS, indices, etc.) -> C expression string."""
    if node is None:
        return "0"
    t = getattr(node, "type", None)
    if t == "expr" and node.children:
        return _expr_ast_to_c(node.children[0], idmap)
    if t == "logic_expr":
        return _logic_expr_to_c(node, idmap)
    if t in ("arith_expr", "term", "factor", "primary"):
        if t == "arith_expr":
            return _arith_expr_to_c(node, idmap)
        if t == "term":
            return _term_to_c(node, idmap)
        if t == "factor":
            return _factor_to_c(node, idmap)
        if t == "primary":
            return _primary_to_c(node, idmap)
    if t == "rela_expr":
        return _rela_expr_to_c(node, idmap)
    if t == "and_expr":
        return _and_expr_to_c(node, idmap)
    if t == "value":
        return _value_to_c(getattr(node, "value", None))
    if t == "identifier":
        return _identifier_to_c(node, idmap)
    if t == "function_call":
        return "0"
    if t == "output":
        return _output_to_c(node, idmap)
    if getattr(node, "children", None):
        return _expr_ast_to_c(node.children[0], idmap)
    return "0"


def _lvalue_from_id_access(vid: str, id_access: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    """Build a C lvalue: id, id[i], id.m, id[i].m."""
    s = vid
    if not id_access or not getattr(id_access, "children", None) or len(id_access.children) < 2:
        return s
    dim_node = id_access.children[0]
    member_node = id_access.children[1]

    if getattr(dim_node, "type", None) == "dimension" and dim_node.children:
        row_size = dim_node.children[0]
        if getattr(row_size, "type", None) == "row_size" and row_size.children:
            size_node = row_size.children[0]
            if getattr(size_node, "type", None) == "size" and size_node.children:
                s += f"[({_expr_ast_to_c(size_node.children[0], idmap)})]"
            if len(row_size.children) >= 2:
                col_size = row_size.children[1]
                if getattr(col_size, "type", None) == "col_size" and col_size.children:
                    pd = col_size.children[0]
                    if getattr(pd, "type", None) == "pdim_size" and pd.children:
                        s += f"[({_expr_ast_to_c(pd.children[0], idmap)})]"

    if getattr(member_node, "type", None) == "id_member" and getattr(member_node, "children", None):
        if len(member_node.children) >= 2:
            mid = member_node.children[1]
            mtok = getattr(mid, "value", None)
            if mtok is not None:
                s += "." + _member_field_c(idmap, str(mtok))
    return s


def _lvalue_from_dimension(vid: str, dimension_node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    """Array element lvalue: vid[i] or vid[r][c] from a dimension AST node."""
    fake_access = ASTNode("id_access", children=[dimension_node, ASTNode("id_member_empty")])
    return _lvalue_from_id_access(vid, fake_access, idmap)


def _tac_decl_to_c(
    instr: TACInstr, semantic: Any, idmap: Dict[str, str]
) -> Optional[Tuple[str, str]]:
    """
    If this TAC op declares an identifier, return (vid_token, full C line).
    Otherwise return None.
    """
    structures: Dict[str, Any] = getattr(semantic, "structures", {}) or {}
    op = instr.op
    if op == "DECL_STRUCT":
        struct_t = instr.arg1
        vid = instr.result
        if not isinstance(vid, str) or not isinstance(struct_t, str):
            return None
        tag = _struct_tag(struct_t, idmap)
        return vid, f"    {tag} {vid} = {{0}};"

    if op == "DECL_STRUCT_INIT":
        struct_t, init_node, vid = instr.arg1, instr.arg2, instr.result
        if not isinstance(vid, str) or not isinstance(struct_t, str):
            return None
        tag = _struct_tag(struct_t, idmap)
        parts = _collect_1d_init_exprs(init_node, idmap)
        if parts:
            return vid, f"    {tag} {vid} = {{ {', '.join(parts)} }};"
        return vid, f"    {tag} {vid} = {{0}};"

    if op == "DECL_STRUCT_ARRAY":
        struct_t, size_node, vid = instr.arg1, instr.arg2, instr.result
        if not isinstance(vid, str) or not isinstance(struct_t, str):
            return None
        tag = _struct_tag(struct_t, idmap)
        sz = _struct_array_size_expr(size_node, idmap)
        return vid, f"    {tag} {vid}[{sz}] = {{0}};"

    if op == "DECL_STRUCT_ARRAY_INIT":
        struct_t, pack, vid = instr.arg1, instr.arg2, instr.result
        if not isinstance(vid, str) or not isinstance(struct_t, str) or not isinstance(pack, tuple):
            return None
        size_node = pack[0]
        tag = _struct_tag(struct_t, idmap)
        sz = _struct_array_size_expr(size_node, idmap)
        return vid, f"    {tag} {vid}[{sz}] = {{0}}; /* struct array init: see TAC VM */"

    if op == "DECL_NORM":
        dt, norm_dec, vid = instr.arg1, instr.arg2, instr.result
        if not isinstance(vid, str) or not isinstance(dt, str) or norm_dec is None:
            return None
        return vid, _emit_decl_norm_line(dt, norm_dec, vid, idmap)

    return None


def _extract_assi_op_and_expr(assignment_node: ASTNode) -> Tuple[str, Optional[ASTNode]]:
    if not getattr(assignment_node, "children", None) or len(assignment_node.children) < 2:
        return "=", None
    assi = assignment_node.children[0]
    expr_n = assignment_node.children[1]
    op = "="
    if getattr(assi, "type", None) == "assi_op" and assi.children:
        opn = assi.children[0]
        op = getattr(opn, "value", "=")
    return op, expr_n


def _emit_decl_norm_line(
    data_type: str, norm_dec_node: ASTNode, vid: str, identifier_map: Dict[str, str]
) -> str:
    """Emit one C declaration line for DECL_NORM (primitive arrays)."""
    cbase = _c_type_for(data_type)
    if getattr(norm_dec_node, "type", None) != "norm_dec" or not norm_dec_node.children:
        return f"    {cbase} {vid};"
    first = norm_dec_node.children[0]
    if getattr(first, "type", None) == "operator" and getattr(first, "value", None) == "=":
        rhs = _expr_ast_to_c(norm_dec_node.children[1], identifier_map)
        return f"    {cbase} {vid} = ({cbase})({rhs});"
    if getattr(first, "type", None) != "row_size":
        return f"    {cbase} {vid};"
    row_size = first
    dims: List[str] = []
    if row_size.children:
        sz = row_size.children[0]
        if getattr(sz, "type", None) == "size" and sz.children:
            dims.append(f"[({_expr_ast_to_c(sz.children[0], identifier_map)})]")
        if len(row_size.children) >= 2:
            cs = row_size.children[1]
            if getattr(cs, "type", None) == "col_size" and cs.children:
                pd = cs.children[0]
                if getattr(pd, "type", None) == "pdim_size" and pd.children:
                    dims.append(f"[({_expr_ast_to_c(pd.children[0], identifier_map)})]")
    br = "".join(dims)
    init_suffix = ""
    if len(norm_dec_node.children) >= 2:
        arr_node = norm_dec_node.children[1]
        if getattr(arr_node, "type", None) == "array" and arr_node.children:
            opn = arr_node.children[0]
            if getattr(opn, "type", None) == "operator" and getattr(opn, "value", None) == "=":
                elems: List[str] = []

                def collect(n: Optional[ASTNode]) -> None:
                    if n is None:
                        return
                    nt = getattr(n, "type", None)
                    if nt in ("value", "output_content"):
                        elems.append(_expr_ast_to_c(n, identifier_map))
                        return
                    if nt == "identifier":
                        elems.append(_expr_ast_to_c(n, identifier_map))
                        return
                    if getattr(n, "children", None):
                        for ch in n.children:
                            collect(ch)

                if len(arr_node.children) >= 2:
                    one_d = arr_node.children[1]
                    collect(one_d)
                if elems:
                    init_suffix = " = {" + ", ".join(elems) + "}"

    return f"    {cbase} {vid}{br}{init_suffix};"


def _collect_1d_init_exprs(init_1d_node: Optional[ASTNode], idmap: Dict[str, str]) -> List[str]:
    out: List[str] = []

    def walk(n: Optional[ASTNode]) -> None:
        if n is None:
            return
        nt = getattr(n, "type", None)
        if nt == "1d_element" and n.children:
            out_node = n.children[0]
            if getattr(out_node, "type", None) == "output" and out_node.children:
                walk(out_node.children[0])
            if len(n.children) > 1:
                walk(n.children[1])
        elif nt == "element_tail" and n.children:
            out_node = n.children[0]
            if getattr(out_node, "type", None) == "output" and out_node.children:
                walk(out_node.children[0])
            if len(n.children) > 1:
                walk(n.children[1])
        elif nt == "literal" and n.children:
            walk(n.children[0])
        elif nt in ("value", "output_content", "identifier", "expr"):
            out.append(_expr_ast_to_c(n, idmap))

    walk(init_1d_node)
    return out


def _size_expr_from_size_node(size_node: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    if size_node is None or getattr(size_node, "type", None) != "size" or not size_node.children:
        return "1"
    return f"({_expr_ast_to_c(size_node.children[0], idmap)})"


def _struct_array_size_expr(size_or_row: Optional[ASTNode], idmap: Dict[str, str]) -> str:
    """TAC passes row_size or size as arg2 for DECL_STRUCT_ARRAY; normalize to one bracket size expr."""
    if size_or_row is None:
        return "1"
    if getattr(size_or_row, "type", None) == "row_size" and size_or_row.children:
        sz = size_or_row.children[0]
        return _size_expr_from_size_node(sz, idmap)
    if getattr(size_or_row, "type", None) == "size" and size_or_row.children:
        return _size_expr_from_size_node(size_or_row, idmap)
    return f"({_expr_ast_to_c(size_or_row, idmap)})"


def generate_c_program(tac_code: Sequence[TACInstr], semantic: Any) -> str:
    """
    Minimal TAC -> C generator.

    Produces a C program using:
    - goto + labels for control flow
    - scanf for INHALE
    - printf for EXHALE (string interpolation only, minimal)
    - struct typedefs + DECL_STRUCT* / DECL_NORM declarations
    - ASSIGN_WITH_ACCESS, INDEX_LOAD, STORE_INDEX, MEMBER_LOAD, INCDEC (with id_access)
    """
    declared_types: Dict[str, str] = getattr(semantic, "declared_types", {}) or {}
    identifier_map: Dict[str, str] = getattr(semantic, "identifier_map", {}) or {}
    structures: Dict[str, Any] = getattr(semantic, "structures", {}) or {}

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

    for t in used_temps:
        if t not in temp_type:
            temp_type[t] = "float"

    lines: List[str] = []
    lines.append("#include <stdio.h>")
    lines.append("#include <stdlib.h>")
    lines.append("#include <stdbool.h>")
    lines.append("")
    _emit_struct_typedefs(semantic, lines)
    lines.append("#define OXC_INT_MIN (-9999999999LL)")
    lines.append("#define OXC_INT_MAX (9999999999LL)")
    lines.append("static long long oxc_checked_int(long long v, const char* name) {")
    lines.append("    if (v < OXC_INT_MIN || v > OXC_INT_MAX) {")
    lines.append('        printf("Runtime error: int literal/assignment out of range for %s\\n", name);')
    lines.append("        exit(1);")
    lines.append("    }")
    lines.append("    return v;")
    lines.append("}")
    lines.append("")
    lines.append("int main() {")

    decl_emitted: set[str] = set()
    for instr in tac_code:
        decl_pair = _tac_decl_to_c(instr, semantic, identifier_map)
        if decl_pair:
            vid_decl, decl_line = decl_pair
            lines.append(decl_line)
            decl_emitted.add(vid_decl)

    for vid in sorted(used_vars):
        if vid in decl_emitted:
            continue
        # Gust type *name* tokens appear as TAC operands but are not C variables.
        if vid in structures:
            continue
        dtype = declared_types.get(vid, "int")
        if _is_struct_type(dtype, structures):
            tag = _struct_tag(dtype, identifier_map)
            lines.append(f"    {tag} {vid} = {{0}}; /* fallback: no matching DECL in TAC */")
            continue
        ctyp = _c_type_for(dtype)
        lines.append(f"    {ctyp} {vid};")

    for t in sorted(used_temps):
        dtype = temp_type.get(t, "float")
        ctyp = _c_type_for(dtype)
        init = "0.0" if ctyp == "double" else "0"
        lines.append(f"    {ctyp} {t} = {init};")

    _DECL_SKIP = frozenset(
        {
            "DECL_STRUCT",
            "DECL_STRUCT_INIT",
            "DECL_STRUCT_ARRAY",
            "DECL_STRUCT_ARRAY_INIT",
            "DECL_NORM",
        }
    )

    for instr in tac_code:
        op = instr.op
        if op in _DECL_SKIP:
            continue
        if op == "DECL_CONST":
            lines.append(f"    /* DECL_CONST: not translated */")
            continue
        if op == "LABEL":
            if isinstance(instr.result, str):
                lines.append(f"{instr.result}: ;")
            continue
        if op == "GOTO":
            lines.append(f"    goto {instr.result};")
            continue
        if op == "IF_TRUE_GOTO":
            cond_expr = _operand_to_c_expr(instr.arg1)
            lines.append(f"    if ({cond_expr} != 0) goto {instr.result};")
            continue
        if op == "ASSIGN":
            dst = instr.result
            rhs = _operand_to_c_expr(instr.arg1)
            if (
                isinstance(dst, str)
                and dst.startswith("id")
                and isinstance(instr.arg1, str)
                and instr.arg1.startswith("id")
            ):
                dt = declared_types.get(dst, "int")
                rt = declared_types.get(instr.arg1, "int")
                if _is_struct_type(dt, structures) and dt == rt:
                    lines.append(f"    {dst} = {rhs};")
                    continue
            if isinstance(dst, str) and dst.startswith("id"):
                dtype = declared_types.get(dst, "int")
            elif isinstance(dst, str) and dst.startswith("t"):
                dtype = temp_type.get(dst, "float")
            else:
                dtype = "int"
            ctyp = _c_type_for(dtype)
            if dtype == "int":
                lines.append(f'    {dst} = oxc_checked_int((long long)({rhs}), "{dst}");')
            else:
                lines.append(f"    {dst} = ({ctyp})({rhs});")
            continue
        if op == "ASSIGN_WITH_ACCESS":
            vid = instr.arg1
            id_access = instr.arg2
            assign_node = instr.result
            if not isinstance(vid, str) or assign_node is None:
                lines.append("    /* ASSIGN_WITH_ACCESS: bad operands */")
                continue
            assi_op, expr_n = _extract_assi_op_and_expr(assign_node)
            lhs = _lvalue_from_id_access(vid, id_access, identifier_map)
            rhs_c = _expr_ast_to_c(expr_n, identifier_map)
            vid_type = declared_types.get(vid, "int")
            if assi_op == "=":
                if vid_type == "int":
                    lines.append(f'    {lhs} = oxc_checked_int((long long)({rhs_c}), "{lhs}");')
                else:
                    lines.append(f"    {lhs} = {rhs_c};")
            elif assi_op in ("+=", "-=", "*=", "/=", "%="):
                lines.append(f"    {lhs} {assi_op} {rhs_c};")
                if vid_type == "int":
                    lines.append(f'    {lhs} = oxc_checked_int((long long)({lhs}), "{lhs}");')
            else:
                lines.append(f"    /* ASSIGN_WITH_ACCESS: unsupported op {assi_op} */")
            continue
        if op == "INDEX_LOAD":
            dst = instr.result
            vid = instr.arg1
            dim = instr.arg2
            if isinstance(dst, str) and isinstance(vid, str):
                rhs = _lvalue_from_dimension(vid, dim, identifier_map)
                lines.append(f"    {dst} = {rhs};")
            else:
                lines.append("    /* INDEX_LOAD: bad operands */")
            continue
        if op == "STORE_INDEX":
            vid = instr.arg1
            dim = instr.arg2
            val = _operand_to_c_expr(instr.result)
            if isinstance(vid, str):
                lhs = _lvalue_from_dimension(vid, dim, identifier_map)
                vid_type = declared_types.get(vid, "int")
                if vid_type == "int":
                    lines.append(f'    {lhs} = oxc_checked_int((long long)({val}), "{lhs}");')
                else:
                    lines.append(f"    {lhs} = ({val});")
            else:
                lines.append("    /* STORE_INDEX: bad operands */")
            continue
        if op == "MEMBER_LOAD":
            dst = instr.result
            vid = instr.arg1
            id_access = instr.arg2
            if isinstance(dst, str) and isinstance(vid, str):
                rhs = _lvalue_from_id_access(vid, id_access, identifier_map)
                lines.append(f"    {dst} = {rhs};")
            else:
                lines.append("    /* MEMBER_LOAD: bad operands */")
            continue
        if op == "INHALE":
            vid = instr.result
            if instr.arg1 is not None:
                lines.append("    /* INHALE with id_access: not translated */")
                continue
            dtype = declared_types.get(vid, "int")
            fmt = _c_scanf_for(dtype)
            if dtype in ("string",):
                lines.append(f'    scanf("{fmt}", {vid});')
            elif dtype in ("char",):
                lines.append(f'    scanf("{fmt}", &{vid});')
            elif dtype in ("float",):
                lines.append(f'    scanf("{fmt}", &{vid});')
            else:
                lines.append(f'    scanf("{fmt}", &{vid});')
            if dtype == "int":
                lines.append(f'    {vid} = oxc_checked_int((long long){vid}, "{vid}");')
            continue
        if op == "EXHALE":
            out_node = instr.arg1
            stmt_lines = _gen_exhale_stmt(out_node, semantic)
            for s in stmt_lines:
                lines.append(f"    {s}")
            lines.append("    fflush(stdout);")
            continue
        if op == "INCDEC":
            inc_op = instr.arg1
            vid = instr.result
            id_access = instr.arg2
            if not isinstance(vid, str):
                continue
            if id_access and getattr(id_access, "children", None):
                lval = _lvalue_from_id_access(vid, id_access, identifier_map)
                if inc_op == "++":
                    lines.append(f"    ({lval})++;")
                elif inc_op == "--":
                    lines.append(f"    ({lval})--;")
                else:
                    lines.append(f"    /* INCDEC unsupported op {inc_op} */")
            else:
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
        if op == "LNOT":
            dst = instr.result
            rhs = _operand_to_c_expr(instr.arg1)
            lines.append(f"    {dst} = !(({rhs}) != 0);")
            continue
        if op in {"CALL", "BUILTIN_CALL"}:
            lines.append(f"    /* {op}: not translated */")
            continue
        if op in {"+", "-", "*", "/", "%", ">", "<", ">=", "<=", "==", "!=", "||", "&&"}:
            dst = instr.result
            a = _operand_to_c_expr(instr.arg1)
            b = _operand_to_c_expr(instr.arg2)
            if op in {"||", "&&"}:
                lines.append(f"    {dst} = (({a} != 0) {op} ({b} != 0));")
                continue
            lines.append(f"    {dst} = ({a} {op} {b});")
            continue

        lines.append(f"    /* unsupported TAC op: {op} */")

    lines.append("    return 0;")
    lines.append("}")
    return "\n".join(lines)

