from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from parser import ASTNode

from interpreter import coerce_switch_case_literal


@dataclass
class TACInstr:
    op: str
    arg1: Any = None
    arg2: Any = None
    result: Any = None
    # Optional type annotation for codegen purposes.
    # Examples: 'int', 'float', 'bool', 'char'
    value_type: Optional[str] = None

    def __repr__(self) -> str:
        def _fmt(x: Any) -> str:
            if isinstance(x, ASTNode):
                return f"<AST:{x.type}>"
            if isinstance(x, str):
                return x
            return repr(x)

        if self.op in {"LABEL", "GOTO"}:
            return f"({self.op}, {_fmt(self.result)})"
        if self.op == "IF_TRUE_GOTO":
            return f"(IF_TRUE_GOTO, {_fmt(self.arg1)}, {_fmt(self.result)})"
        if self.op in {"ASSIGN", "INHALE", "EXHALE", "INCDEC", "UMINUS", "LNOT"}:
            return f"({self.op}, {_fmt(self.arg1)}, {_fmt(self.result)})"
        if self.op == "DECL_NORM":
            return f"(DECL_NORM, {_fmt(self.arg1)}, {_fmt(self.arg2)}, {_fmt(self.result)})"
        if self.op == "ASSIGN_WITH_ACCESS":
            return f"(ASSIGN_WITH_ACCESS, {_fmt(self.arg1)}, {_fmt(self.arg2)}, {_fmt(self.result)})"
        if self.op in {"INDEX_LOAD", "STORE_INDEX"}:
            return f"({self.op}, {_fmt(self.arg1)}, {_fmt(self.arg2)}, {_fmt(self.result)})"
        if self.op == "CALL":
            return f"(CALL, {_fmt(self.arg1)}, {_fmt(self.arg2)}, {_fmt(self.result)})"
        if self.op == "BUILTIN_CALL":
            return f"(BUILTIN_CALL, {_fmt(self.arg1)}, {_fmt(self.result)})"
        # Default: treat as binary-op-like instruction
        return f"({self.op}, {_fmt(self.arg1)}, {_fmt(self.arg2)}, {_fmt(self.result)})"


class _TACContext:
    def __init__(self) -> None:
        self.temp_id = 0
        self.label_id = 0

    def new_temp(self) -> str:
        t = f"t{self.temp_id}"
        self.temp_id += 1
        return t

    def new_label(self, base: str = "L") -> str:
        l = f"{base}{self.label_id}"
        self.label_id += 1
        return l


class TACGenerator:
    """
    Generates a TAC-like IR from the existing OxCLang AST.

    This implementation targets the subset used by your test programs:
    - int/float declarations (with or without initializer)
    - assignments (=, +=, -=, *=, /=, %=')
    - arithmetic expressions (+, -, *, /, %)
    - relational conditions (>, <, >=, <=, ==, !=)
    - for-loop / echo blocks (parser mislabels them as while_loop with 4 children)
    - while loops (cycle(...){...})
    - if / elseif / else
    - stream (switch) with case / diffuse
    - 1D/2D array declarations (row_size), element read/write and += etc.
    - inhale into scalar or array element (inhale(arr[i]))
    - do { } cycle (cond) (do-while)
    - predefined builtins in expressions (BUILTIN_CALL → Interpreter._eval_function_call)
    - inhale/exhale for interactive I/O
    """

    def __init__(self, ast: ASTNode, semantic_analyzer: Optional[Any] = None):
        self.ast = ast
        self.semantic = semantic_analyzer
        self.ctx = _TACContext()
        self.code: List[TACInstr] = []

    # ---------------- Entry ----------------

    def generate(self) -> List[TACInstr]:
        if self.ast is None:
            return []
        if getattr(self.ast, "type", None) != "program":
            raise ValueError("TACGenerator expects a program AST root")
        self._gen_program(self.ast)
        return self.code

    # ---------------- Helpers ----------------

    def _emit(
        self,
        op: str,
        arg1: Any = None,
        arg2: Any = None,
        result: Any = None,
        value_type: Optional[str] = None,
    ) -> None:
        self.code.append(
            TACInstr(op=op, arg1=arg1, arg2=arg2, result=result, value_type=value_type)
        )

    def _infer_type(self, node: Optional[ASTNode]) -> Optional[str]:
        """
        Best-effort type inference for codegen.
        Uses SemanticAnalyzer._get_expression_type when available.
        """
        if node is None or self.semantic is None:
            return None
        try:
            infer = getattr(self.semantic, "_get_expression_type", None)
            if infer:
                return infer(node)
        except Exception:
            return None
        return None

    def _value_node_to_tac_constant(self, v: Any) -> Any:
        """
        int_lit / float_lit / yuh / naur from parse_value store lexer text in the AST.
        Emit Python int/float (and keep yuh/naur) so TACVM does not treat digit strings
        like string literals (fixes password == \"1234\" vs string variable).
        """
        if v in ("yuh", "naur"):
            return v
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        if isinstance(v, str):
            if v == "yuh" or v == "naur":
                return v
            try:
                if "." in v:
                    return float(v)
                return int(v)
            except ValueError:
                return v
        return v

    def _is_temp(self, place: Any) -> bool:
        return isinstance(place, str) and place.startswith("t")

    def _default_value(self, data_type: str) -> Any:
        if data_type == "int":
            return 0
        if data_type == "float":
            return 0.0
        if data_type == "bool":
            return False
        # char/string default is None in your interpreter
        return None

    def _identifier_token_type_from_identifier_node(self, node: ASTNode) -> str:
        """
        In expression parsing, identifiers can appear as:
        - ASTNode('identifier', value='id3')  [from check_id()]
        - ASTNode('identifier', children=[id_no, id_tail])  [from parse_identifier()]
        """
        if getattr(node, "value", None):
            return node.value
        if getattr(node, "children", None):
            if node.children and getattr(node.children[0], "type", None) == "identifier":
                return node.children[0].value
        raise ValueError("Could not extract identifier token type")

    # ---------------- Statements / Control Flow ----------------

    def _gen_program(self, node: ASTNode) -> None:
        # program -> [global_dec, sub_functions, body]
        if not getattr(node, "children", None):
            return
        if len(node.children) < 3:
            return
        body_node = node.children[2]
        self._gen_body(body_node)

    def _gen_body(self, node: ASTNode) -> None:
        # body -> stmt_list
        if not node or getattr(node, "type", None) is None:
            return
        if node.type == "body" and node.children:
            self._gen_stmt_list(node.children[0])

    def _gen_stmt_list(self, node: ASTNode) -> None:
        if node is None:
            return
        if getattr(node, "type", None) == "stmt_list_empty":
            return
        if getattr(node, "type", None) != "stmt_list":
            return
        # stmt_list -> [statement_node, stmt_list_node]
        statement_node = node.children[0]
        rest = node.children[1] if len(node.children) > 1 else None
        self._gen_statement(statement_node)
        self._gen_stmt_list(rest)

    def _gen_stmt_ctrl(self, node: ASTNode) -> None:
        if node is None:
            return
        if getattr(node, "type", None) == "stmt_ctrl_empty":
            return
        if getattr(node, "type", None) != "stmt_ctrl":
            return
        # stmt_ctrl -> [statement_node, stmt_ctrl_node]
        statement_node = node.children[0]
        rest = node.children[1] if len(node.children) > 1 else None
        self._gen_statement(statement_node)
        self._gen_stmt_ctrl(rest)

    def _gen_statement(self, node: ASTNode) -> None:
        if node is None or getattr(node, "type", None) != "statement":
            # Some places might pass statement-like nodes directly.
            if node is not None and getattr(node, "type", None) in {
                "declaration",
                "input_output",
                "identifier_stat",
                "iteration",
                "conditioner",
            }:
                self._gen_statement(ASTNode("statement", children=[node]))
            return

        if not getattr(node, "children", None) or not node.children:
            return

        inner = node.children[0]
        t = getattr(inner, "type", None)
        if t == "declaration":
            self._gen_declaration(inner)
        elif t == "input_output":
            self._gen_input_output(inner)
        elif t == "identifier_stat":
            self._gen_identifier_stat(inner)
        elif t == "iteration":
            self._gen_iteration(inner)
        elif t == "conditioner":
            self._gen_conditioner(inner)
        else:
            raise NotImplementedError(f"Unsupported statement node: {t}")

    def _gen_conditioner(self, node: ASTNode) -> None:
        """conditioner -> if_stat | switch_stat"""
        if not node.children:
            return
        inner = node.children[0]
        it = getattr(inner, "type", None)
        if it == "if_stat":
            self._gen_if_stat(inner)
        elif it == "switch_stat":
            self._gen_switch_stat(inner)
        else:
            raise NotImplementedError(f"Unsupported conditioner child: {it}")

    def _gen_if_stat(self, node: ASTNode) -> None:
        # if_stat -> [cond_stat, stmt_ctrl, if_tail]
        if not node.children or len(node.children) < 3:
            return
        cond_stat, stmt_ctrl, if_tail = node.children[0], node.children[1], node.children[2]
        L_merge = self.ctx.new_label("Lmerge")
        L_then = self.ctx.new_label("Lthen")
        L_rest = self.ctx.new_label("Lif")

        cond_place = self._gen_expr_value(cond_stat.children[0])
        self._emit("IF_TRUE_GOTO", arg1=cond_place, result=L_then)
        self._emit("GOTO", result=L_rest)

        self._emit("LABEL", result=L_then)
        self._gen_stmt_ctrl(stmt_ctrl)
        self._emit("GOTO", result=L_merge)

        self._emit("LABEL", result=L_rest)
        self._gen_if_tail(if_tail, L_merge)

        self._emit("LABEL", result=L_merge)

    def _gen_if_tail(self, if_tail_node: ASTNode, L_merge: str) -> None:
        t = getattr(if_tail_node, "type", None)
        if t == "if_tail_empty":
            return
        if t != "if_tail" or not if_tail_node.children:
            return

        ch = if_tail_node.children
        if len(ch) == 1:
            # else { stmt_ctrl }
            self._gen_stmt_ctrl(ch[0])
            self._emit("GOTO", result=L_merge)
            return

        if len(ch) == 3:
            # elseif (cond) { stmt_ctrl } if_tail
            cond_stat, stmt_ctrl, next_tail = ch[0], ch[1], ch[2]
            L_then = self.ctx.new_label("Lthen")
            L_rest = self.ctx.new_label("Lif")

            cond_place = self._gen_expr_value(cond_stat.children[0])
            self._emit("IF_TRUE_GOTO", arg1=cond_place, result=L_then)
            self._emit("GOTO", result=L_rest)

            self._emit("LABEL", result=L_then)
            self._gen_stmt_ctrl(stmt_ctrl)
            self._emit("GOTO", result=L_merge)

            self._emit("LABEL", result=L_rest)
            self._gen_if_tail(next_tail, L_merge)
            return

        raise NotImplementedError(f"Unexpected if_tail shape: {len(ch)} children")

    def _gen_switch_stat(self, node: ASTNode) -> None:
        # switch_stat -> [id_no, id_access, switch_cases, switch_def]
        # Matches interpreter._exec_switch_stat: compare stream variable to case literals.
        if not node.children or len(node.children) < 4:
            return
        id_no = node.children[0]
        id_access_node = node.children[1]
        switch_cases_node = node.children[2]
        switch_def_node = node.children[3]

        vid = getattr(id_no, "value", None)
        if vid is None:
            raise ValueError("switch_stat missing identifier")

        dim = self._dimension_node_from_id_access(id_access_node)
        if dim is not None:
            t_sw = self.ctx.new_temp()
            self._emit("INDEX_LOAD", arg1=vid, arg2=dim, result=t_sw)
            switch_place: Any = t_sw
        else:
            switch_place = vid

        switch_type: Optional[str] = None
        if self.semantic is not None:
            switch_type = getattr(self.semantic, "declared_types", {}).get(vid)

        cases: List[Tuple[Any, ASTNode]] = []
        cur: Optional[ASTNode] = switch_cases_node
        while cur is not None and getattr(cur, "type", None) == "switch_cases":
            switch_opts = cur.children[0]
            stmt_list = cur.children[1]
            case_val = getattr(switch_opts, "value", None)
            cases.append((case_val, stmt_list))
            cur = cur.children[2] if len(cur.children) > 2 else None

        L_merge = self.ctx.new_label("Lswm")
        has_default = getattr(switch_def_node, "type", None) == "switch_def" and getattr(
            switch_def_node, "children", None
        )

        case_labels = [self.ctx.new_label("Lcase") for _ in cases]
        L_default = self.ctx.new_label("Lswdef") if has_default else None

        for (case_val, _), L_case in zip(cases, case_labels):
            t = self.ctx.new_temp()
            cmp_rhs: Any = case_val
            if switch_type is not None and self.semantic is not None:
                cmp_rhs = coerce_switch_case_literal(self.semantic, case_val, switch_type)
            self._emit("==", arg1=switch_place, arg2=cmp_rhs, result=t, value_type=None)
            self._emit("IF_TRUE_GOTO", arg1=t, result=L_case)

        if L_default is not None:
            self._emit("GOTO", result=L_default)
        else:
            self._emit("GOTO", result=L_merge)

        for (_, stmt_list), L_case in zip(cases, case_labels):
            self._emit("LABEL", result=L_case)
            self._gen_stmt_list(stmt_list)
            self._emit("GOTO", result=L_merge)

        if L_default is not None:
            self._emit("LABEL", result=L_default)
            self._gen_stmt_list(switch_def_node.children[0])
            self._emit("GOTO", result=L_merge)

        self._emit("LABEL", result=L_merge)

    def _gen_iteration(self, node: ASTNode) -> None:
        # iteration -> while_loop
        if not node.children:
            return
        loop_node = node.children[0]
        if getattr(loop_node, "type", None) == "while_loop":
            self._gen_while_loop(loop_node)
        else:
            raise NotImplementedError(f"Unsupported iteration node: {getattr(loop_node, 'type', None)}")

    def _gen_while_loop(self, node: ASTNode) -> None:
        """
        while_loop children shape:
          - while_loop: [cond_stat, stmt_ctrl]
          - for_loop mislabel: [for_init, cond_stat, identifier_stat, stmt_ctrl]
          - do-while mislabel: [stmt_ctrl, cond_stat]
        """
        if not node.children:
            return

        if len(node.children) == 2:
            # while_loop OR do-while
            first, second = node.children[0], node.children[1]
            if getattr(first, "type", None) == "stmt_ctrl":
                # do { body } cycle (cond)~  →  body runs at least once
                body = first
                cond_stat = second
                self._gen_do_while_form(body, cond_stat)
                return
            # while: [cond_stat, stmt_ctrl]
            cond_stat = first
            body = second
            self._gen_while_form(cond_stat, body)
            return

        if len(node.children) == 4 and getattr(node.children[0], "type", None) == "for_init":
            # for-loop form (parser mislabels echo as while_loop with 4 children)
            for_init, cond_stat, update, body = node.children
            self._gen_for_form(for_init, cond_stat, update, body)
            return

        raise NotImplementedError(f"Unsupported while_loop shape: {[c.type for c in node.children]}")

    def _gen_for_form(self, for_init: ASTNode, cond_stat: ASTNode, update: ASTNode, body: ASTNode) -> None:
        # for_init: [data_type, id_no, for_vals]
        self._gen_for_init(for_init)

        L_start = self.ctx.new_label("L")
        L_body = self.ctx.new_label("Lbody")
        L_end = self.ctx.new_label("Lend")

        self._emit("LABEL", result=L_start)

        cond_place = self._gen_expr_value(cond_stat.children[0])
        self._emit("IF_TRUE_GOTO", arg1=cond_place, result=L_body)
        self._emit("GOTO", result=L_end)

        self._emit("LABEL", result=L_body)
        self._gen_stmt_ctrl(body)
        self._gen_identifier_stat(update)
        self._emit("GOTO", result=L_start)

        self._emit("LABEL", result=L_end)

    def _gen_while_form(self, cond_stat: ASTNode, body: ASTNode) -> None:
        L_start = self.ctx.new_label("L")
        L_body = self.ctx.new_label("Lbody")
        L_end = self.ctx.new_label("Lend")

        self._emit("LABEL", result=L_start)
        cond_place = self._gen_expr_value(cond_stat.children[0])
        self._emit("IF_TRUE_GOTO", arg1=cond_place, result=L_body)
        self._emit("GOTO", result=L_end)

        self._emit("LABEL", result=L_body)
        self._gen_stmt_ctrl(body)
        self._emit("GOTO", result=L_start)
        self._emit("LABEL", result=L_end)

    def _gen_do_while_form(self, body: ASTNode, cond_stat: ASTNode) -> None:
        """do { stmt_ctrl } cycle (cond)~ — body first, then repeat while cond."""
        L_start = self.ctx.new_label("Ldo")
        self._emit("LABEL", result=L_start)
        self._gen_stmt_ctrl(body)
        cond_place = self._gen_expr_value(cond_stat.children[0])
        self._emit("IF_TRUE_GOTO", arg1=cond_place, result=L_start)

    def _gen_for_init(self, node: ASTNode) -> None:
        # for_init -> [data_type_node, id_no, for_vals]
        # In your parser: ASTNode('for_init', children=[data_type_node, id_no, for_vals_node])
        if not node.children or len(node.children) != 3:
            raise NotImplementedError("Unsupported for_init format")

        data_type_node, id_no, for_vals_node = node.children
        data_type = getattr(data_type_node, "value", None)
        vid = getattr(id_no, "value", None)
        if data_type is None or vid is None:
            raise ValueError("for_init missing data_type or identifier")

        init_place = self._gen_for_vals_as_place(for_vals_node)
        self._emit("ASSIGN", arg1=init_place, result=vid)

    def _gen_for_vals_as_place(self, node: ASTNode) -> Any:
        """
        for_vals → int_lit | float_lit | char_lit | id<id_access>

        Literal form: ASTNode('for_vals', value=...) with no children.
        Identifier form: ASTNode('for_vals', children=[id_no, id_access]) — value is unset;
        must load via INDEX_LOAD (dimension may be dimension_empty after `id~` in echo header)
        or plain identifier token for ASSIGN.
        """
        if node is None:
            return 0
        if getattr(node, "type", None) != "for_vals":
            if hasattr(node, "value"):
                return node.value
            return self._gen_expr_value(node)

        children = getattr(node, "children", None) or []
        if len(children) == 0:
            return getattr(node, "value", None)

        id_no = children[0]
        vid = getattr(id_no, "value", None)
        if vid is None:
            vid = self._identifier_token_type_from_identifier_node(id_no)
        id_access = children[1] if len(children) > 1 else None
        dim = self._dimension_node_from_id_access(id_access)
        if dim is not None:
            t_load = self.ctx.new_temp()
            self._emit("INDEX_LOAD", arg1=vid, arg2=dim, result=t_load)
            return t_load
        return vid

    # ---------------- Declarations & assignments ----------------

    def _gen_declaration(self, node: ASTNode) -> None:
        # declaration -> normal | structure | wind | ...
        if not node.children:
            return
        inner = node.children[0]
        if getattr(inner, "type", None) == "normal":
            self._gen_normal_decl(inner)
        else:
            raise NotImplementedError(f"Declaration type not supported in TAC: {getattr(inner,'type',None)}")

    def _gen_normal_decl(self, node: ASTNode) -> None:
        # normal -> [data_type, id_no, norm_dec, norm_tail] where id_no/norm_dec/norm_tail might be nested
        if len(node.children) < 4:
            return
        data_type_node = node.children[0]
        first_id = node.children[1].value
        data_type = data_type_node.value
        first_norm_dec = node.children[2]
        first_tail = node.children[3]

        self._gen_emit_norm_declaration(first_id, data_type, first_norm_dec)
        self._gen_norm_tail(first_tail, data_type)

    def _gen_norm_tail(self, node: Optional[ASTNode], data_type: str) -> None:
        if node is None:
            return
        if getattr(node, "type", None) == "norm_tail_empty":
            return
        if getattr(node, "type", None) != "norm_tail":
            return
        # norm_tail -> [id_no, norm_dec_node, norm_tail_node]
        vid = node.children[0].value
        norm_dec_node = node.children[1]
        tail_node = node.children[2] if len(node.children) > 2 else None
        self._gen_emit_norm_declaration(vid, data_type, norm_dec_node)
        self._gen_norm_tail(tail_node, data_type)

    def _gen_emit_norm_declaration(self, vid: str, data_type: str, norm_dec_node: Optional[ASTNode]) -> None:
        """Emit ASSIGN or DECL_NORM for one identifier in a normal declaration."""
        if norm_dec_node is None or getattr(norm_dec_node, "type", None) == "norm_dec_empty":
            self._emit("ASSIGN", arg1=self._default_value(data_type), result=vid)
            return
        if getattr(norm_dec_node, "type", None) != "norm_dec":
            self._emit("ASSIGN", arg1=self._default_value(data_type), result=vid)
            return
        if not norm_dec_node.children:
            self._emit("ASSIGN", arg1=self._default_value(data_type), result=vid)
            return
        first = norm_dec_node.children[0]
        if getattr(first, "type", None) == "operator" and getattr(first, "value", None) == "=":
            expr_node = norm_dec_node.children[1]
            rhs = self._gen_expr_value(expr_node)
            self._emit("ASSIGN", arg1=rhs, result=vid)
            return
        if getattr(first, "type", None) == "row_size":
            # Full norm_dec node (row_size + optional array init) — VM uses Interpreter._declare_one
            self._emit("DECL_NORM", arg1=data_type, arg2=norm_dec_node, result=vid)
            return
        self._emit("ASSIGN", arg1=self._default_value(data_type), result=vid)

    def _dimension_node_from_id_access(self, id_access_node: Optional[ASTNode]) -> Optional[ASTNode]:
        if not id_access_node or not getattr(id_access_node, "children", None):
            return None
        first = id_access_node.children[0]
        if getattr(first, "type", None) == "dimension":
            return first
        return None

    def _id_access_has_dimension(self, id_access_node: Optional[ASTNode]) -> bool:
        return self._dimension_node_from_id_access(id_access_node) is not None

    def _gen_identifier_stat(self, node: ASTNode) -> None:
        # Two shapes in this grammar:
        # 1) prefix inc/dec: identifier_stat -> [unary_op, id_no, id_access]
        # 2) postfix or assignment: identifier_stat -> [id_no, id_stat_body]
        if not node.children:
            return
        if getattr(node.children[0], "type", None) == "unary_op":
            op = node.children[0].value
            vid = node.children[1].value
            self._emit("INCDEC", arg1=op, result=vid)
            return

        vid = node.children[0].value
        body = node.children[1]

        # function call statement form: id(<param_opts>)~
        if body.children and getattr(body.children[0], "type", None) in ("param_opts", "param_opts_empty"):
            param_opts_node = body.children[0]
            temp = self.ctx.new_temp()
            self._emit("CALL", arg1=vid, arg2=param_opts_node, result=temp)
            return

        # body -> [id_access, id_stat_tail]
        id_access = body.children[0]
        tail = body.children[1]
        if getattr(tail, "type", None) != "id_stat_tail" or not tail.children:
            return

        first = tail.children[0]

        # postfix inc/dec: id_stat_tail -> [unary_op]
        if getattr(first, "type", None) == "unary_op":
            self._emit("INCDEC", arg1=first.value, result=vid)
            return

        # assignment tail: id_stat_tail -> [assignment]
        if getattr(first, "type", None) == "assignment":
            self._gen_assignment_to_identifier(vid, first, id_access=id_access)
            return

        raise NotImplementedError(f"Unsupported identifier_stat tail: {getattr(first, 'type', None)}")

    def _gen_assignment_to_identifier(
        self, vid: str, assignment_node: ASTNode, id_access: Optional[ASTNode] = None
    ) -> None:
        # assignment_node -> [assi_op, expr]
        assi_op_node = assignment_node.children[0]
        expr_node = assignment_node.children[1]
        rhs_type = self._infer_type(expr_node)

        op = assi_op_node.children[0].value  # '=', '+=', '-=', '*=' ...
        rhs_place = self._gen_expr_value(expr_node)

        dim = self._dimension_node_from_id_access(id_access)

        if dim is not None:
            if op == "=":
                self._emit("ASSIGN_WITH_ACCESS", arg1=vid, arg2=id_access, result=assignment_node)
                return
            # compound assignment to element: load, combine, store
            t_load = self.ctx.new_temp()
            self._emit("INDEX_LOAD", arg1=vid, arg2=dim, result=t_load)
            base_op = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%"}[op]
            t_new = self.ctx.new_temp()
            self._emit(base_op, arg1=t_load, arg2=rhs_place, result=t_new, value_type=rhs_type)
            self._emit("STORE_INDEX", arg1=vid, arg2=dim, result=t_new)
            return

        if op == "=":
            self._emit("ASSIGN", arg1=rhs_place, result=vid)
            return

        base_op = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%"}[op]
        t = self.ctx.new_temp()
        self._emit(base_op, arg1=vid, arg2=rhs_place, result=t, value_type=rhs_type)
        self._emit("ASSIGN", arg1=t, result=vid)

    def _gen_input_output(self, node: ASTNode) -> None:
        # input_output -> ['inhale', id_no, id_access] OR ['exhale', output_node]
        if not node.children or len(node.children) < 2:
            return
        kind = node.children[0]
        if kind == "inhale":
            vid = node.children[1].value
            id_access = node.children[2] if len(node.children) > 2 else None
            dim = self._dimension_node_from_id_access(id_access)
            self._emit("INHALE", arg1=dim, result=vid)
            return
        if kind == "exhale":
            output_node = node.children[1]
            self._emit("EXHALE", arg1=output_node, result=None)
            return
        raise NotImplementedError(f"Unsupported IO kind: {kind}")

    # ---------------- Expressions ----------------

    def _gen_expr_value(self, node: ASTNode) -> Any:
        """
        Returns a "place" that can be:
        - a constant (int/float/bool or numeric strings)
        - an identifier token type string (e.g. 'id3')
        - a temp name (e.g. 't0')
        """
        if node is None:
            return 0
        t = getattr(node, "type", None)
        if t == "expr":
            return self._gen_expr_value(node.children[0])
        if t == "logic_expr":
            and_node, or_tail = node.children
            left = self._gen_expr_value(and_node)
            return self._gen_or_tail(left, or_tail)
        if t == "and_expr":
            rela_node, and_tail = node.children
            left = self._gen_expr_value(rela_node)
            return self._gen_and_tail(left, and_tail)
        if t == "rela_expr":
            arith_node, rela_tail = node.children
            left = self._gen_expr_value(arith_node)
            if getattr(rela_tail, "type", None) == "rela_tail_empty":
                return left
            # rela_tail -> [rela_sym, arith_expr]
            rela_sym, right_arith = rela_tail.children
            op = rela_sym.children[0].value  # '>', '<=', ...
            right = self._gen_expr_value(right_arith)
            temp = self.ctx.new_temp()
            self._emit(op, arg1=left, arg2=right, result=temp, value_type=self._infer_type(node))
            return temp
        if t == "arith_expr":
            term_node, arith_tail = node.children
            left = self._gen_expr_value(term_node)
            return self._gen_arith_tail(left, arith_tail)
        if t == "term":
            factor_node, term_tail = node.children
            left = self._gen_expr_value(factor_node)
            return self._gen_term_tail(left, term_tail)
        if t == "factor":
            return self._gen_expr_value(node.children[0])
        if t == "primary":
            return self._gen_primary(node.children[0])
        if t == "negate":
            return self._gen_negate(node)
        if t == "output":
            # In expression context, output wraps literal/value or identifier.
            return self._gen_output_as_value(node)
        if t == "literal":
            return self._gen_literal_as_value(node)
        if t == "value":
            return node.value

        # Fallback: if node.value exists, treat it as constant
        if hasattr(node, "value"):
            return getattr(node, "value", None)

        raise NotImplementedError(f"Expression node not supported in TAC: {t}")

    def _gen_or_tail(self, left: Any, node: ASTNode) -> Any:
        if getattr(node, "type", None) == "or_tail_empty":
            return left
        if getattr(node, "type", None) != "or_tail":
            return left
        # or_tail -> [and_expr, or_tail]
        rhs_and, next_tail = node.children
        rhs = self._gen_expr_value(rhs_and)
        temp = self.ctx.new_temp()
        self._emit("||", arg1=left, arg2=rhs, result=temp, value_type=self._infer_type(node))
        return self._gen_or_tail(temp, next_tail)

    def _gen_and_tail(self, left: Any, node: ASTNode) -> Any:
        if getattr(node, "type", None) == "and_tail_empty":
            return left
        if getattr(node, "type", None) != "and_tail":
            return left
        # and_tail -> [rela_expr, and_tail]
        rhs_rela, next_tail = node.children
        rhs = self._gen_expr_value(rhs_rela)
        temp = self.ctx.new_temp()
        self._emit("&&", arg1=left, arg2=rhs, result=temp, value_type=self._infer_type(node))
        return self._gen_and_tail(temp, next_tail)

    def _gen_arith_tail(self, left: Any, node: ASTNode) -> Any:
        if getattr(node, "type", None) == "arith_tail_empty":
            return left
        if getattr(node, "type", None) != "arith_tail":
            return left
        op_node, term_node, next_tail = node.children
        op = op_node.children[0].value  # '+' or '-'
        right = self._gen_expr_value(term_node)
        temp = self.ctx.new_temp()
        self._emit(op, arg1=left, arg2=right, result=temp, value_type=self._infer_type(node))
        return self._gen_arith_tail(temp, next_tail)

    def _gen_term_tail(self, left: Any, node: ASTNode) -> Any:
        if getattr(node, "type", None) == "term_tail_empty":
            return left
        if getattr(node, "type", None) != "term_tail":
            return left
        op_node, factor_node, next_tail = node.children
        op = op_node.children[0].value  # '*','/','%'
        right = self._gen_expr_value(factor_node)
        temp = self.ctx.new_temp()
        self._emit(op, arg1=left, arg2=right, result=temp, value_type=self._infer_type(node))
        return self._gen_term_tail(temp, next_tail)

    def _gen_primary(self, node: ASTNode) -> Any:
        # primary children[0] is one of: expr / negate / output / logic_expr
        if getattr(node, "type", None) == "expr":
            return self._gen_expr_value(node)
        if getattr(node, "type", None) == "negate":
            return self._gen_negate(node)
        if getattr(node, "type", None) == "output":
            return self._gen_output_as_value(node)
        if getattr(node, "type", None) == "logic_expr":
            # Parser: primary -> !( <logic_expr> ) — same as Interpreter: not bool(_eval_logic(...))
            inner = self._gen_expr_value(node)
            temp = self.ctx.new_temp()
            self._emit("LNOT", arg1=inner, result=temp, value_type="bool")
            return temp
        raise NotImplementedError(f"Unsupported primary in TAC: {getattr(node,'type',None)}")

    def _gen_negate(self, node: ASTNode) -> Any:
        # negate -> [expr] | [id_no, id_access]
        if node.children and getattr(node.children[0], "type", None) == "expr":
            v = self._gen_expr_value(node.children[0])
            temp = self.ctx.new_temp()
            self._emit("UMINUS", arg1=v, result=temp, value_type=self._infer_type(node))
            return temp

        # negate -> id_no id_access (check_id returns id node with .value token type)
        if len(node.children) >= 2:
            id_no = node.children[0]
            id_access = node.children[1]
            vid = getattr(id_no, "value", None)
            if vid is None:
                vid = self._identifier_token_type_from_identifier_node(id_no)
            dim = self._dimension_node_from_id_access(id_access)
            if dim is not None:
                t_load = self.ctx.new_temp()
                self._emit("INDEX_LOAD", arg1=vid, arg2=dim, result=t_load)
                temp = self.ctx.new_temp()
                self._emit("UMINUS", arg1=t_load, result=temp, value_type=self._infer_type(node))
                return temp

        vid_node = node.children[0]
        vid = self._identifier_token_type_from_identifier_node(vid_node)
        temp = self.ctx.new_temp()
        self._emit("UMINUS", arg1=vid, result=temp, value_type=self._infer_type(node))
        return temp

    def _gen_output_as_value(self, node: ASTNode) -> Any:
        # output -> literal
        if not node.children:
            return 0
        literal_node = node.children[0]
        return self._gen_literal_as_value(literal_node)

    def _gen_literal_as_value(self, node: ASTNode) -> Any:
        # literal -> [value] or [output_concat, output_tail]
        if not node.children:
            return 0
        if len(node.children) == 1 and getattr(node.children[0], "type", None) == "value":
            return self._value_node_to_tac_constant(node.children[0].value)

        if len(node.children) >= 2:
            concat_node = node.children[0]
            tail_node = node.children[1]
            # For numeric/boolean operands in arithmetic, tail is expected to be empty.
            if getattr(tail_node, "type", None) != "output_tail_empty":
                raise NotImplementedError("String concatenation in arithmetic expressions not supported in TAC yet")

            # concat_node might be ASTNode('output', [identifier/function_call]) for identifiers
            if getattr(concat_node, "type", None) == "output":
                if not concat_node.children:
                    return 0
                single = concat_node.children[0]
                if getattr(single, "type", None) == "identifier":
                    # identifier -> id id_tail: function call, array index, or plain variable
                    if (
                        getattr(single, "children", None)
                        and len(single.children) >= 2
                        and getattr(single.children[1], "type", None) == "id_tail"
                        and getattr(single.children[1], "children", None)
                        and single.children[1].children
                    ):
                        tail0 = single.children[1].children[0]
                        if getattr(tail0, "type", None) in ("param_opts", "param_opts_empty"):
                            func_id_token_type = single.children[0].value
                            param_opts_node = tail0
                            temp = self.ctx.new_temp()
                            self._emit("CALL", arg1=func_id_token_type, arg2=param_opts_node, result=temp)
                            return temp
                        if getattr(tail0, "type", None) == "id_access":
                            dim = self._dimension_node_from_id_access(tail0)
                            if dim is not None:
                                vid = single.children[0].value
                                temp = self.ctx.new_temp()
                                self._emit("INDEX_LOAD", arg1=vid, arg2=dim, result=temp)
                                return temp
                    return self._identifier_token_type_from_identifier_node(single)
                if getattr(single, "type", None) == "function_call":
                    temp = self.ctx.new_temp()
                    self._emit(
                        "BUILTIN_CALL",
                        arg1=single,
                        result=temp,
                        value_type=self._infer_type(single),
                    )
                    return temp

            # concat_node might be value (rare in this grammar for expression operands)
            if getattr(concat_node, "type", None) == "value":
                return self._value_node_to_tac_constant(concat_node.value)

            # char_lit / string_lit in literal → output_content (e.g. char a = 'A'~, string x = "hi"~)
            # Pass through lexer token text ('"hi"' / "'A'') so TACVM _get_value uses
            # _literal_to_value; do NOT decode here — digit-only strings would match int path.
            if getattr(concat_node, "type", None) == "output_content":
                return getattr(concat_node, "value", None)

        raise NotImplementedError(f"Literal operand not supported in TAC: {getattr(node,'type',None)}")

