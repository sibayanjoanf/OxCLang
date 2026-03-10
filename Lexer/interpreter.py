from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import re


class RuntimeSignal(Exception):
    pass


class BreakSignal(RuntimeSignal):
    pass


class ContinueSignal(RuntimeSignal):
    pass


@dataclass
class ReturnSignal(RuntimeSignal):
    value: Any


@dataclass
class InputRequest:
    target_identifier: str  # identifier token-type, e.g. "id3"
    prompt: str = ""


class InterpreterError(Exception):
    def __init__(self, message: str, line: int = 0, column: int = 0):
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column

    def to_dict(self) -> Dict[str, Any]:
        return {"message": self.message, "line": self.line, "column": self.column}


class Interpreter:
    """
    Executes the OxC AST produced by parser.py.
    Designed for 'terminal-like' execution: pauses on inhale and resumes with provided input.
    """

    def __init__(self, semantic_analyzer, tokens: Optional[list] = None):
        self.semantic = semantic_analyzer
        self.tokens = tokens or []

        # key is identifier token-type (id3), value is runtime value
        self.scopes: List[Dict[str, Any]] = [{}]
        self.output: List[str] = []

        # function metadata
        self.functions: Dict[str, Any] = {}  # name -> air_func node
        self.function_params: Dict[str, List[Tuple[str, str]]] = {}  # name -> [(param_id, param_type)]
        self.function_return_type: Dict[str, str] = {}  # name -> return type

        # interactive input state
        self.waiting_for_input: bool = False
        self.input_request: Optional[InputRequest] = None

        # pause/resume support
        self._paused_stack: List[Tuple[Any, int]] = []  # (node, next_child_index)
        self._paused_after_inhale: bool = False

    # -------------------- Public API --------------------

    def run(self, ast) -> None:
        """Run from the program root until completion or input request."""
        if ast is None:
            return
        self._index_functions(ast)
        self._exec(ast)

    def provide_input(self, user_text: str) -> None:
        """Provide input to the last inhale request and resume execution."""
        if not self.waiting_for_input or not self.input_request:
            return

        target_id = self.input_request.target_identifier
        expected_type = self._lookup_declared_type(target_id)
        value = self._convert_input(user_text, expected_type)

        # Echo the raw input so the transcript keeps the full history
        # (prompt from exhale + this line of input).
        self.emit(str(user_text) + "\n")
        self._assign(target_id, value)

        self.waiting_for_input = False
        self.input_request = None

        # resume from paused point
        self._paused_after_inhale = False
        if self._paused_stack:
            node, child_i = self._paused_stack.pop()
            self._exec(node, resume_child_index=child_i)

    # -------------------- Output helpers --------------------

    def emit(self, text: str) -> None:
        self.output.append(text)

    def emit_line(self, text: str = "") -> None:
        self.output.append(text + "\n")

    # -------------------- Scope helpers --------------------

    def push_scope(self) -> None:
        self.scopes.append({})

    def pop_scope(self) -> None:
        if len(self.scopes) > 1:
            self.scopes.pop()

    def _lookup(self, identifier_token_type: str) -> Any:
        for scope in reversed(self.scopes):
            if identifier_token_type in scope:
                return scope[identifier_token_type]
        return None

    def _assign(self, identifier_token_type: str, value: Any) -> None:
        for scope in reversed(self.scopes):
            if identifier_token_type in scope:
                scope[identifier_token_type] = value
                return
        self.scopes[-1][identifier_token_type] = value

    # -------------------- Core execution --------------------

    def _exec(self, node, resume_child_index: int = 0) -> Any:
        if node is None:
            return None

        t = getattr(node, "type", None)
        if t is None:
            # primitive / string child in AST
            return node

        method = getattr(self, f"_exec_{t}", None)
        if method:
            return method(node, resume_child_index=resume_child_index)
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_generic(self, node, resume_child_index: int = 0) -> Any:
        """
        Generic executor: walk children in order.
        DOES NOT manage pause/resume points; sequence-like nodes (e.g. stmt_list)
        are responsible for pushing to _paused_stack when inhale is hit.
        """
        if not getattr(node, "children", None):
            return None
        result = None
        for i in range(resume_child_index, len(node.children)):
            child = node.children[i]
            result = self._exec(child)
            if self.waiting_for_input:
                return None
        return result

    # -------------------- Program / functions --------------------

    def _index_functions(self, program_node) -> None:
        # program children: global_dec, sub_functions, body
        if not getattr(program_node, "children", None):
            return
        for child in program_node.children:
            if getattr(child, "type", None) == "sub_functions":
                self._collect_air_funcs(child)

    def _collect_air_funcs(self, node) -> None:
        if not getattr(node, "children", None):
            return
        for child in node.children:
            if getattr(child, "type", None) == "air_func":
                name_id = child.children[1].value  # identifier token-type
                actual_name = self.semantic.get_actual_name(name_id)
                self.functions[actual_name] = child
                self.function_return_type[actual_name] = self._read_return_type(child.children[0])
                self.function_params[actual_name] = self._read_params(child.children[2])
            elif getattr(child, "type", None) in ("sub_functions",):
                self._collect_air_funcs(child)

    def _read_return_type(self, return_type_node) -> str:
        if return_type_node.type == "return_type" and getattr(return_type_node, "value", None) == "vacuum":
            return "vacuum"
        # return_type -> data_type
        dt = return_type_node.children[0]
        return dt.value

    def _read_params(self, params_node) -> List[Tuple[str, str]]:
        if params_node.type == "params_empty":
            return []
        params: List[Tuple[str, str]] = []
        # params: [data_type, identifier, params_dim, params_tail]
        cur = params_node
        while cur and getattr(cur, "type", None) == "params":
            dt = cur.children[0].value
            pid = cur.children[1].value
            params.append((pid, dt))
            tail = cur.children[3]
            if getattr(tail, "type", None) == "params_tail_empty":
                break
            # params_tail: [data_type, identifier, params_dim, params_tail]
            cur = tail
        return params

    # -------------------- Statements --------------------

    def _exec_program(self, node, resume_child_index: int = 0) -> Any:
        # run globals + subfunctions (already indexed) + atmosphere body
        for i in range(resume_child_index, len(node.children)):
            child = node.children[i]
            if getattr(child, "type", None) == "body":
                self.push_scope()
                try:
                    self._exec(child)
                finally:
                    self.pop_scope()
            else:
                self._exec(child)
            if self.waiting_for_input:
                return None
        return None

    def _exec_body(self, node, resume_child_index: int = 0) -> Any:
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_stmt_list(self, node, resume_child_index: int = 0) -> Any:
        """
        Sequence of statements. This is where we set the resume point when
        an inhale triggers waiting_for_input.
        """
        if not getattr(node, "children", None):
            return None
        for i in range(resume_child_index, len(node.children)):
            child = node.children[i]
            self._exec(child)
            if self.waiting_for_input:
                # Only set the resume point for the *innermost* stmt_list.
                # Outer stmt_lists (and program/body) should not overwrite it.
                if not self._paused_stack:
                    self._paused_stack = [(node, i + 1)]
                return None
        return None

    def _exec_statement(self, node, resume_child_index: int = 0) -> Any:
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_declaration(self, node, resume_child_index: int = 0) -> Any:
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_normal(self, node, resume_child_index: int = 0) -> Any:
        # children: data_type, identifier, norm_dec, norm_tail
        data_type = node.children[0].value
        first_id = node.children[1].value
        self._declare_one(first_id, data_type, node.children[2])
        self._declare_tail(node.children[3], data_type)
        return None

    def _declare_tail(self, norm_tail_node, data_type: str) -> None:
        if getattr(norm_tail_node, "type", None) == "norm_tail_empty":
            return
        # norm_tail: [identifier, norm_dec, norm_tail]
        cur = norm_tail_node
        while cur and getattr(cur, "type", None) == "norm_tail":
            vid = cur.children[0].value
            self._declare_one(vid, data_type, cur.children[1])
            cur = cur.children[2]

    def _declare_one(self, identifier_token_type: str, data_type: str, norm_dec_node) -> None:
        # norm_dec: row_size/array OR '=' expr OR empty
        if getattr(norm_dec_node, "type", None) == "norm_dec_empty":
            self._assign(identifier_token_type, self._default_value(data_type))
            return
        if norm_dec_node.type == "norm_dec" and norm_dec_node.children:
            first = norm_dec_node.children[0]
            if getattr(first, "type", None) == "operator" and first.value == "=":
                expr = norm_dec_node.children[1]
                val = self._eval_expr(expr)
                self._assign(identifier_token_type, self._coerce_to(data_type, val))
                return
            if getattr(first, "type", None) == "row_size":
                # arrays: initialize default; initialization lists can be added later
                self._assign(identifier_token_type, [])
                return
        self._assign(identifier_token_type, self._default_value(data_type))

    def _exec_identifier_stat(self, node, resume_child_index: int = 0) -> Any:
        # either [unary_op, id, id_access] or [id, id_stat_body]
        if node.children and getattr(node.children[0], "type", None) == "unary_op":
            op = node.children[0].value
            vid = node.children[1].value
            self._apply_incdec(op, vid, prefix=True)
            return None
        vid = node.children[0].value
        body = node.children[1]
        return self._exec_id_stat_body(vid, body)

    def _exec_id_stat_body(self, vid: str, body_node) -> Any:
        # either function call statement: body=[param_opts]
        if body_node.children and getattr(body_node.children[0], "type", None) in ("param_opts", "param_opts_empty"):
            # user-defined function call as statement
            self._call_user_function(vid, body_node.children[0])
            return None
        # otherwise: [id_access, id_stat_tail]
        id_access = body_node.children[0]
        tail = body_node.children[1]
        # only support plain var for now (no member/index assignment)
        if tail.children and getattr(tail.children[0], "type", None) == "unary_op":
            op = tail.children[0].value
            self._apply_incdec(op, vid, prefix=False)
            return None
        assignment = tail.children[0]
        return self._exec_assignment(vid, assignment)

    def _exec_assignment(self, vid: str, assignment_node) -> Any:
        # children: assi_op, expr
        op_node = assignment_node.children[0]
        expr_node = assignment_node.children[1]
        op = op_node.children[0].value  # operator node value
        rhs = self._eval_expr(expr_node)
        cur = self._lookup(vid)
        if op == "=":
            self._assign(vid, rhs)
        elif op == "+=":
            self._assign(vid, (cur if cur is not None else 0) + rhs)
        elif op == "-=":
            self._assign(vid, (cur if cur is not None else 0) - rhs)
        elif op == "*=":
            self._assign(vid, (cur if cur is not None else 0) * rhs)
        elif op == "/=":
            if rhs == 0:
                raise InterpreterError("Division by zero")
            self._assign(vid, (cur if cur is not None else 0) / rhs)
        elif op == "%=":
            if rhs == 0:
                raise InterpreterError("Modulo by zero")
            self._assign(vid, (cur if cur is not None else 0) % rhs)
        return None

    def _exec_input_output(self, node, resume_child_index: int = 0) -> Any:
        # children: ['inhale', id, id_access] OR ['exhale', output]
        kind = node.children[0]
        if kind == "inhale":
            vid = node.children[1].value
            # request input
            self.waiting_for_input = True
            self.input_request = InputRequest(target_identifier=vid, prompt="")
            return None
        if kind == "exhale":
            out_node = node.children[1]
            text = self._eval_output(out_node)
            # Behave like C printf: no automatic newline; rely on \n in the string
            self.emit(str(text))
            return None
        return None

    def _exec_conditioner(self, node, resume_child_index: int = 0) -> Any:
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_if_stat(self, node, resume_child_index: int = 0) -> Any:
        cond = node.children[0]
        then_block = node.children[1]
        tail = node.children[2]
        if self._eval_cond(cond):
            self.push_scope()
            try:
                self._exec(then_block)
            finally:
                self.pop_scope()
            return None
        return self._exec_if_tail(tail)

    def _exec_if_tail(self, node) -> Any:
        if getattr(node, "type", None) == "if_tail_empty":
            return None
        # elseif: [cond_stat, stmt_ctrl, if_tail] OR else: [stmt_ctrl]
        if len(node.children) == 1:
            self.push_scope()
            try:
                self._exec(node.children[0])
            finally:
                self.pop_scope()
            return None
        if self._eval_cond(node.children[0]):
            self.push_scope()
            try:
                self._exec(node.children[1])
            finally:
                self.pop_scope()
            return None
        return self._exec_if_tail(node.children[2])

    def _exec_iteration(self, node, resume_child_index: int = 0) -> Any:
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_while_loop(self, node, resume_child_index: int = 0) -> Any:
        # NOTE: parser.py mistakenly uses 'while_loop' for for_loop/dowhile too.
        # Detect by child count / shape:
        # - while_loop: [cond_stat, stmt_ctrl]
        # - for_loop mislabeled: [for_init, cond_stat, identifier_stat, stmt_ctrl]
        # - do-while mislabeled: [stmt_ctrl, cond_stat]
        if len(node.children) == 2 and getattr(node.children[0], "type", None) == "stmt_ctrl":
            # do-while form
            body = node.children[0]
            cond = node.children[1]
            while True:
                try:
                    self.push_scope()
                    try:
                        self._exec(body)
                    finally:
                        self.pop_scope()
                except BreakSignal:
                    break
                except ContinueSignal:
                    pass
                if self.waiting_for_input:
                    return None
                if not self._eval_cond(cond):
                    break
            return None

        if len(node.children) == 4 and getattr(node.children[0], "type", None) == "for_init":
            # for-loop form
            init, cond, update, body = node.children
            self.push_scope()
            try:
                self._exec(init)
                while self._eval_cond(cond):
                    try:
                        self.push_scope()
                        try:
                            self._exec(body)
                        finally:
                            self.pop_scope()
                    except BreakSignal:
                        break
                    except ContinueSignal:
                        pass
                    if self.waiting_for_input:
                        return None
                    self._exec(update)
            finally:
                self.pop_scope()
            return None

        # while-loop form
        cond, body = node.children
        while self._eval_cond(cond):
            try:
                self.push_scope()
                try:
                    self._exec(body)
                finally:
                    self.pop_scope()
            except BreakSignal:
                break
            except ContinueSignal:
                continue
            if self.waiting_for_input:
                return None
        return None

    def _exec_for_init(self, node, resume_child_index: int = 0) -> Any:
        # forms:
        # - [id, id_access, for_vals]
        # - [data_type, id, for_vals]
        if getattr(node.children[0], "type", None) == "data_type":
            dt = node.children[0].value
            vid = node.children[1].value
            v = node.children[2].value
            self._assign(vid, self._coerce_to(dt, self._literal_to_value(v)))
            return None
        vid = node.children[0].value
        v = node.children[2].value
        self._assign(vid, self._literal_to_value(v))
        return None

    def _exec_stmt_ctrl(self, node, resume_child_index: int = 0) -> Any:
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_ctrl_flow(self, node, resume_child_index: int = 0) -> Any:
        v = getattr(node, "value", None)
        if v == "resist":
            raise BreakSignal()
        if v == "flow":
            raise ContinueSignal()
        # return_stat is nested in ctrl_flow via statement production
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_return_stat(self, node, resume_child_index: int = 0) -> Any:
        val = self._eval_expr(node.children[0])
        raise ReturnSignal(val)

    # -------------------- Function calls --------------------

    def _call_user_function(self, func_id_token_type: str, param_opts_node) -> Any:
        func_name = self.semantic.get_actual_name(func_id_token_type)
        if func_name not in self.functions:
            raise InterpreterError(f"Undefined function '{func_name}'")

        args = self._eval_param_opts(param_opts_node)
        params = self.function_params.get(func_name, [])

        if len(args) != len(params):
            raise InterpreterError(
                f"Function '{func_name}' expects {len(params)} args, got {len(args)}"
            )

        func_node = self.functions[func_name]
        body = func_node.children[3]
        return_stat = func_node.children[4]

        self.push_scope()
        try:
            for (pid, ptype), aval in zip(params, args):
                self._assign(pid, self._coerce_to(ptype, aval))
            try:
                self._exec(body)
                # explicit return statement node exists; execute it to return value or nothing
                if getattr(return_stat, "type", None) == "return_stat":
                    self._exec(return_stat)
                return None
            except ReturnSignal as r:
                return r.value
        finally:
            self.pop_scope()

    # -------------------- Expressions --------------------

    def _eval_param_opts(self, node) -> List[Any]:
        if getattr(node, "type", None) == "param_opts_empty":
            return []
        # param_opts: [param_list]
        return self._eval_param_list(node.children[0])

    def _eval_param_list(self, node) -> List[Any]:
        # param_list: [param_item, param_tail]
        item = self._eval_expr(node.children[0].children[0])
        tail = node.children[1]
        if getattr(tail, "type", None) == "param_tail_empty":
            return [item]
        return [item] + self._eval_param_list(tail.children[0])

    def _eval_cond(self, cond_stat_node) -> bool:
        v = self._eval_expr(cond_stat_node.children[0])
        return bool(v)

    def _eval_expr(self, expr_node) -> Any:
        return self._eval_logic(expr_node.children[0])

    def _eval_logic(self, node) -> Any:
        if node.type == "logic_expr":
            left = self._eval_and(node.children[0])
            return self._eval_or_tail(left, node.children[1])
        return self._eval_generic_expr(node)

    def _eval_or_tail(self, left, node) -> Any:
        if node.type == "or_tail_empty":
            return left
        right = self._eval_and(node.children[0])
        result = bool(left) or bool(right)
        return self._eval_or_tail(result, node.children[1])

    def _eval_and(self, node) -> Any:
        if node.type == "and_expr":
            left = self._eval_rela(node.children[0])
            return self._eval_and_tail(left, node.children[1])
        return self._eval_generic_expr(node)

    def _eval_and_tail(self, left, node) -> Any:
        if node.type == "and_tail_empty":
            return left
        right = self._eval_rela(node.children[0])
        result = bool(left) and bool(right)
        return self._eval_and_tail(result, node.children[1])

    def _eval_rela(self, node) -> Any:
        if node.type == "rela_expr":
            left = self._eval_arith(node.children[0])
            tail = node.children[1]
            if tail.type == "rela_tail_empty":
                return left
            op = tail.children[0].children[0].value
            right = self._eval_arith(tail.children[1])
            if op == "==":
                return left == right
            if op == "!=":
                return left != right
            if op == ">":
                return left > right
            if op == "<":
                return left < right
            if op == ">=":
                return left >= right
            if op == "<=":
                return left <= right
        return self._eval_generic_expr(node)

    def _eval_arith(self, node) -> Any:
        if node.type == "arith_expr":
            left = self._eval_term(node.children[0])
            return self._eval_arith_tail(left, node.children[1])
        return self._eval_generic_expr(node)

    def _eval_arith_tail(self, left, node) -> Any:
        if node.type == "arith_tail_empty":
            return left
        op = node.children[0].children[0].value
        right = self._eval_term(node.children[1])
        if op == "+":
            left = left + right
        else:
            left = left - right
        return self._eval_arith_tail(left, node.children[2])

    def _eval_term(self, node) -> Any:
        if node.type == "term":
            left = self._eval_factor(node.children[0])
            return self._eval_term_tail(left, node.children[1])
        return self._eval_generic_expr(node)

    def _eval_term_tail(self, left, node) -> Any:
        if node.type == "term_tail_empty":
            return left
        op = node.children[0].children[0].value
        right = self._eval_factor(node.children[1])
        if op == "*":
            left = left * right
        elif op == "/":
            if right == 0:
                raise InterpreterError("Division by zero")
            left = left / right
        elif op == "%":
            if right == 0:
                raise InterpreterError("Modulo by zero")
            left = left % right
        return self._eval_term_tail(left, node.children[2])

    def _eval_factor(self, node) -> Any:
        # factor -> primary
        return self._eval_primary(node.children[0])

    def _eval_primary(self, node) -> Any:
        # primary -> (expr) | -negate | o | !(logic_expr)
        if node.children and getattr(node.children[0], "type", None) == "expr":
            return self._eval_expr(node.children[0])
        if node.children and getattr(node.children[0], "type", None) == "negate":
            v = self._eval_negate(node.children[0])
            return -v
        if node.children and getattr(node.children[0], "type", None) == "output":
            return self._eval_output(node.children[0])
        if node.children and getattr(node.children[0], "type", None) == "logic_expr":
            return not bool(self._eval_logic(node.children[0]))
        return self._eval_generic_expr(node)

    def _eval_negate(self, node) -> Any:
        # negate -> (expr) | id id_access
        if node.children and getattr(node.children[0], "type", None) == "expr":
            return self._eval_expr(node.children[0])
        vid = node.children[0].value
        return self._lookup(vid)

    def _eval_output(self, node) -> Any:
        # output -> identifier | function_call | literal
        child = node.children[0]
        if child.type == "identifier":
            return self._eval_identifier(child)
        if child.type == "function_call":
            # predefined builtins only for now are handled by semantic;
            # user funcs are id_tail calls in identifier.
            return None
        if child.type == "literal":
            return self._eval_literal(child)
        return None

    def _eval_literal(self, node) -> Any:
        # literal -> value OR output_concat + output_tail (string/char concatenation)
        c0 = node.children[0]
        if c0.type == "value":
            return self._literal_to_value(c0.value)
        # concat form: treat everything as string and join
        parts: List[str] = []
        self._collect_output_concat(node, parts)
        return "".join(parts)

    def _collect_output_concat(self, node, parts: List[str]) -> None:
        if node is None or not getattr(node, "children", None):
            return
        for ch in node.children:
            if getattr(ch, "type", None) == "output_content":
                parts.append(str(self._literal_to_value(ch.value)))
            elif getattr(ch, "type", None) == "value":
                parts.append(str(self._literal_to_value(ch.value)))
            else:
                self._collect_output_concat(ch, parts)

    def _eval_identifier(self, node) -> Any:
        # identifier -> [id, id_tail] OR [unary_op, id_access] (prefix op)
        if node.children and getattr(node.children[0], "type", None) == "unary_op":
            op = node.children[0].value
            # child[1] is id_access only; actual id was consumed earlier in parser;
            # this form isn't very usable here
            return None
        id_no = node.children[0].value
        tail = node.children[1]
        if (
            tail.type == "id_tail"
            and tail.children
            and getattr(tail.children[0], "type", None) in ("param_opts", "param_opts_empty")
        ):
            return self._call_user_function(id_no, tail.children[0])
        # plain variable reference
        return self._lookup(id_no)

    def _eval_generic_expr(self, node) -> Any:
        # wrapper nodes: delegate to first meaningful child
        if not getattr(node, "children", None):
            if node.type == "value":
                return self._literal_to_value(node.value)
            return None
        return self._exec_generic(node)

    # -------------------- Utilities --------------------

    def _apply_incdec(self, op: str, vid: str, prefix: bool) -> Any:
        cur = self._lookup(vid)
        if cur is None:
            cur = 0
        if op == "++":
            new = cur + 1
        else:
            new = cur - 1
        self._assign(vid, new)
        return new if prefix else cur

    def _literal_to_value(self, raw) -> Any:
        if raw == "yuh":
            return True
        if raw == "naur":
            return False
        if isinstance(raw, str):
            if raw.startswith('"') and raw.endswith('"'):
                inner = raw[1:-1].encode("utf-8").decode("unicode_escape")
                return self._interpolate_string(inner)
            if raw.startswith("'") and raw.endswith("'"):
                inner = raw[1:-1]
                return inner if inner != "" else None
        # number parsing
        try:
            if isinstance(raw, str) and "." in raw:
                return float(raw)
            return int(raw)
        except Exception:
            return raw

    def _interpolate_string(self, s: str) -> str:
        """
        Handle OxC format specifier @{name} inside string literals.
        Uses semantic.identifier_map (token_type -> actual name) to find the runtime variable.
        """
        if "@{" not in s or "}" not in s:
            return s

        # Build reverse map: actual name -> token_type
        reverse_ids: Dict[str, str] = {}
        id_map = getattr(self.semantic, "identifier_map", {}) or {}
        for token_type, actual in id_map.items():
            reverse_ids[actual] = token_type

        def repl(match: re.Match) -> str:
            inner = match.group(1).strip()
            if not inner:
                return match.group(0)
            token_type = reverse_ids.get(inner)
            if not token_type:
                return match.group(0)
            val = self._lookup(token_type)
            return "" if val is None else str(val)

        return re.sub(r"@\{([^}]+)\}", repl, s)

    def _default_value(self, data_type: str) -> Any:
        if data_type == "int":
            return 0
        if data_type == "float":
            return 0.0
        if data_type == "bool":
            return False
        if data_type in ("char", "string"):
            return None
        return None

    def _coerce_to(self, data_type: str, value: Any) -> Any:
        # minimal coercions per spec for assignments/declarations
        if data_type == "int":
            if isinstance(value, bool):
                return 1 if value else 0
            if isinstance(value, float):
                return int(value)
            if value is None:
                return 0
            return int(value)
        if data_type == "float":
            if isinstance(value, bool):
                return 1.0 if value else 0.0
            if value is None:
                return 0.0
            return float(value)
        if data_type == "bool":
            if isinstance(value, (int, float)):
                return False if value == 0 else True
            if value is None:
                return False
            if isinstance(value, str):
                return False if value == "" else True
            return bool(value)
        if data_type == "char":
            if value is None:
                return None
            if isinstance(value, str):
                return value[0] if value else None
            if isinstance(value, (int, float)):
                return chr(int(value))
        if data_type == "string":
            if value is None:
                return None
            if isinstance(value, bool):
                return "yuh" if value else "naur"
            return str(value)
        return value

    def _lookup_declared_type(self, identifier_token_type: str) -> str:
        """
        Determine the declared data type of an identifier using the
        SemanticAnalyzer's persistent declared_types map. This is keyed
        by identifier token type (e.g. 'id1', 'id2', ...) so it remains
        valid even after semantic scopes are popped.
        """
        declared_types = getattr(self.semantic, "declared_types", {})
        dtype = declared_types.get(identifier_token_type)
        if dtype:
            return dtype
        # If we reach this point, there is a mismatch between the interpreter
        # and the semantic analyzer (identifier not recorded). Surface this
        # clearly as a runtime error.
        raise InterpreterError(
            f"Internal error: no declared type for identifier '{identifier_token_type}'"
        )

    def _convert_input(self, user_text: str, expected_type: str) -> Any:
        txt = user_text.rstrip("\r\n")
        if expected_type == "string":
            return txt
        if expected_type == "char":
            return txt[0] if txt else None
        if expected_type == "int":
            try:
                # Allow numeric strings like "3" or "3.0" but reject non-numeric.
                return int(float(txt))
            except Exception:
                raise InterpreterError(f"Invalid int input: '{txt}'")
        if expected_type == "float":
            try:
                return float(txt)
            except Exception:
                raise InterpreterError(f"Invalid float input: '{txt}'")
        if expected_type == "bool":
            if txt.lower() in ("yuh", "true", "1"):
                return True
            if txt.lower() in ("naur", "false", "0", ""):
                return False
            return True
        return txt
