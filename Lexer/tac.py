from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from parser import ASTNode


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
        if self.op in {"ASSIGN", "INHALE", "EXHALE", "INCDEC", "UMINUS"}:
            return f"({self.op}, {_fmt(self.arg1)}, {_fmt(self.result)})"
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
            # Not needed for current test programs
            raise NotImplementedError("TAC generation for if/stream is not implemented yet")
        else:
            raise NotImplementedError(f"Unsupported statement node: {t}")

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
                raise NotImplementedError("do-while not implemented yet")
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
        # for_vals can be int_lit/float_lit/char_lit or id<id_access>
        if getattr(node, "type", None) == "for_vals":
            return node.value
        # In this parser, for_vals node is sometimes ASTNode('for_vals', value=lit.value)
        if hasattr(node, "value"):
            return node.value
        # Fallback: treat as expression
        return self._gen_expr_value(node)

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

        init_place = self._gen_norm_dec_init_place(first_norm_dec, data_type)
        self._emit("ASSIGN", arg1=init_place, result=first_id)
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
        init_place = self._gen_norm_dec_init_place(norm_dec_node, data_type)
        self._emit("ASSIGN", arg1=init_place, result=vid)
        self._gen_norm_tail(tail_node, data_type)

    def _gen_norm_dec_init_place(self, node: ASTNode, data_type: str) -> Any:
        if node is None:
            return self._default_value(data_type)
        if getattr(node, "type", None) == "norm_dec_empty":
            return self._default_value(data_type)
        if getattr(node, "type", None) != "norm_dec":
            # norm_dec node may be malformed; fallback
            return self._default_value(data_type)

        # norm_dec children: [row_size,array] or [operator '=', expr] or [norm_dec_empty]
        if not node.children:
            return self._default_value(data_type)
        first = node.children[0]
        if getattr(first, "type", None) == "operator" and getattr(first, "value", None) == "=":
            expr_node = node.children[1]
            return self._gen_expr_value(expr_node)
        # arrays not supported in TAC for now
        if getattr(first, "type", None) == "row_size":
            raise NotImplementedError("Array declarations not supported in this TAC generator yet")
        return self._default_value(data_type)

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

        # function call statement not supported yet
        if body.children and getattr(body.children[0], "type", None) in ("param_opts", "param_opts_empty"):
            raise NotImplementedError("Function calls in TAC not implemented yet")

        # body -> [id_access, id_stat_tail]
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
            self._gen_assignment_to_identifier(vid, first)
            return

        raise NotImplementedError(f"Unsupported identifier_stat tail: {getattr(first, 'type', None)}")

    def _gen_assignment_to_identifier(self, vid: str, assignment_node: ASTNode) -> None:
        # assignment_node -> [assi_op, expr]
        assi_op_node = assignment_node.children[0]
        expr_node = assignment_node.children[1]
        rhs_type = self._infer_type(expr_node)

        op = assi_op_node.children[0].value  # '=', '+=', '-=', '*=' ...
        rhs_place = self._gen_expr_value(expr_node)

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
            self._emit("INHALE", arg1=None, result=vid)
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
            raise NotImplementedError("Logical negation in TAC expressions not implemented yet")
        raise NotImplementedError(f"Unsupported primary in TAC: {getattr(node,'type',None)}")

    def _gen_negate(self, node: ASTNode) -> Any:
        # negate -> [expr] | [id_no, id_access]
        if node.children and getattr(node.children[0], "type", None) == "expr":
            v = self._gen_expr_value(node.children[0])
            temp = self.ctx.new_temp()
            self._emit("UMINUS", arg1=v, result=temp, value_type=self._infer_type(node))
            return temp

        # negate -> id_no id_access
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
            return node.children[0].value

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
                    return self._identifier_token_type_from_identifier_node(single)
                if getattr(single, "type", None) == "function_call":
                    raise NotImplementedError("Function call expressions not supported in TAC yet")

            # concat_node might be value (rare in this grammar for expression operands)
            if getattr(concat_node, "type", None) == "value":
                return concat_node.value

        raise NotImplementedError(f"Literal operand not supported in TAC: {getattr(node,'type',None)}")

