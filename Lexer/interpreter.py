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
    # If set, inhale targets arr[i] / arr[r][c] (AST node type 'dimension').
    dimension_node: Any = None
    # Full id_access node when inhale targets arr[i].member or other access forms.
    id_access_node: Any = None


@dataclass
class ContinuationFrame:
    node: Any
    resume_child_index: int
    scope_depth: int


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
        # name -> [(param_id, param_type, is_array)]
        self.function_params: Dict[str, List[Tuple[str, str, bool]]] = {}
        self.function_return_type: Dict[str, str] = {}  # name -> return type

        # interactive input state
        self.waiting_for_input: bool = False
        self.input_request: Optional[InputRequest] = None

        # pause/resume support
        self._paused_stack: List[ContinuationFrame] = []
        self._paused_after_inhale: bool = False
        self._pending_loop_signal: Optional[str] = None

        # current function name (set during _call_user_function) so return value can be normalized
        self._current_function_name: Optional[str] = None
        # Pending user-function call paused by inhale().
        # Shape: {'func_name': str, 'return_stat': ASTNode, 'scope_depth': int}
        self._pending_call: Optional[Dict[str, Any]] = None
        self._pending_call_result: Any = None

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
        id_access = getattr(self.input_request, "id_access_node", None)
        dim = getattr(self.input_request, "dimension_node", None)
        self.assign_input_target(target_id, value, id_access_node=id_access, dimension_node=dim)

        self.waiting_for_input = False
        self.input_request = None

        # resume from paused point; process stack until we pause again or stack is empty
        self._paused_after_inhale = False
        while self._paused_stack:
            frame = self._paused_stack.pop()
            try:
                self._exec(frame.node, resume_child_index=frame.resume_child_index)
            except BreakSignal:
                # Route break to the nearest paused loop frame.
                self._resume_after_loop_signal(is_break=True)
            except ContinueSignal:
                # Route continue to the nearest paused loop frame.
                self._resume_after_loop_signal(is_break=False)
            except ReturnSignal as r:
                # Resumed inside a paused function call and reached gasp.
                if self._pending_call:
                    self._pending_call_result = self._normalize_return(
                        r.value, self._pending_call["func_name"]
                    )
                    self._pending_call = None
                    self._current_function_name = None
                    self.pop_scope()
                else:
                    raise
            # A resumed function can complete and immediately reach an outer inhale;
            # finalize pending call as soon as its own continuation frames are done.
            self._finalize_pending_call_if_ready()
            if self.waiting_for_input:
                break
        # If a pending function call resumed and completed without explicit ReturnSignal,
        # finish call epilogue now (e.g., vacuum function or implicit return path).
        self._finalize_pending_call_if_ready()

    def _resume_after_loop_signal(self, is_break: bool) -> None:
        """
        When Break/Continue is raised while resuming from inhale, locate the nearest
        paused while_loop frame and resume it with control-flow intent.
        """
        while self._paused_stack:
            loop_frame = self._paused_stack.pop()
            if getattr(loop_frame.node, "type", None) == "while_loop":
                self._pending_loop_signal = "break" if is_break else "continue"
                self._exec(loop_frame.node, resume_child_index=loop_frame.resume_child_index)
                return

    def _push_pause_frame(self, node: Any, resume_child_index: int) -> None:
        self._paused_stack.append(
            ContinuationFrame(node=node, resume_child_index=resume_child_index, scope_depth=len(self.scopes))
        )

    def _insert_pause_frame(self, at_index: int, node: Any, resume_child_index: int) -> None:
        self._paused_stack.insert(
            at_index,
            ContinuationFrame(node=node, resume_child_index=resume_child_index, scope_depth=len(self.scopes)),
        )

    def _drop_top_pause_frame(self, node: Any, resume_child_index: int) -> None:
        if (
            self._paused_stack
            and self._paused_stack[-1].node is node
            and self._paused_stack[-1].resume_child_index == resume_child_index
        ):
            self._paused_stack.pop()

    def consume_pending_call_result(self) -> Any:
        """Used by TACVM CALL resume path to retrieve function result after inhale."""
        v = self._pending_call_result
        self._pending_call_result = None
        return v

    def _finalize_pending_call_if_ready(self) -> None:
        if not self._pending_call:
            return
        pending_scope_depth = int(self._pending_call.get("scope_depth", len(self.scopes)))
        # A paused frame at or deeper than pending function scope means we are still
        # inside that function's continuation path.
        for frame in self._paused_stack:
            if frame.scope_depth >= pending_scope_depth:
                return
        if len(self.scopes) < pending_scope_depth:
            # Function scope was already unwound (e.g., control-flow exit while
            # resuming). Treat pending call as finished to avoid stale-call leaks.
            self._pending_call_result = None
            self._pending_call = None
            self._current_function_name = None
            return
        pending = self._pending_call
        func_name = pending["func_name"]
        return_stat = pending["return_stat"]
        result = None
        rt = getattr(return_stat, "type", None)
        if rt == "return_stat" or (
            getattr(return_stat, "children", None) and len(return_stat.children) > 0
        ):
            try:
                self._exec(return_stat)
            except ReturnSignal as r:
                result = self._normalize_return(r.value, func_name)
        self._pending_call_result = result
        self._pending_call = None
        self._current_function_name = None
        self.pop_scope()

    # -------------------- Output helpers --------------------

    def emit(self, text: str) -> None:
        self.output.append(text)

    def emit_line(self, text: str = "") -> None:
        self.output.append(text + "\n")

    def _to_oxc_text(self, value: Any) -> str:
        """Render runtime values in OxC output form."""
        if value is None:
            return ""
        if isinstance(value, bool):
            return "yuh" if value else "naur"
        if isinstance(value, float):
            return f"{value:.6f}"
        return str(value)

    # -------------------- Scope helpers --------------------

    def push_scope(self) -> None:
        self.scopes.append({})

    def pop_scope(self) -> None:
        if len(self.scopes) > 1:
            self.scopes.pop()

    def _scope_key(self, identifier: str) -> str:
        """
        Normalize identifier to the key used in scope. Use the actual name (lexeme)
        so that the same variable name always maps to the same key regardless of
        token type (e.g. id2 vs id8 for different occurrences of TAX_RATE).
        """
        if not identifier:
            return identifier
        id_map = getattr(self.semantic, "identifier_map", {}) or {}
        # token_type -> lexeme; use lexeme as key so all refs to same name share one key
        return id_map.get(identifier, identifier)

    def _lookup(self, identifier_token_type: str) -> Any:
        key = self._scope_key(identifier_token_type)
        for scope in reversed(self.scopes):
            if key in scope:
                return scope[key]
        # key is the lexeme (actual name); use it for the error message
        name = key if key else identifier_token_type
        raise InterpreterError(f"Undefined variable '{name}'")

    def _assign(self, identifier_token_type: str, value: Any) -> None:
        # Never store "naur"/"yuh" as-is; always store Python bool so conditions work
        if value == "naur" or getattr(value, "value", None) == "naur":
            value = False
        elif value == "yuh" or getattr(value, "value", None) == "yuh":
            value = True
        dtype = self._lookup_declared_type(identifier_token_type)
        value = self._coerce_to(dtype, value)
        key = self._scope_key(identifier_token_type)
        for scope in reversed(self.scopes):
            if key in scope:
                scope[key] = value
                return
        self.scopes[-1][key] = value

    def _declare_in_current_scope(self, identifier_token_type: str, value: Any) -> None:
        """
        Declare or re-declare a variable in the current scope only.
        This preserves lexical shadowing (locals must not overwrite outers).
        """
        if value == "naur" or getattr(value, "value", None) == "naur":
            value = False
        elif value == "yuh" or getattr(value, "value", None) == "yuh":
            value = True
        key = self._scope_key(identifier_token_type)
        self.scopes[-1][key] = value

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

    def _read_params(self, params_node) -> List[Tuple[str, str, bool]]:
        if params_node.type == "params_empty":
            return []
        params: List[Tuple[str, str, bool]] = []
        # params: [data_type, identifier, params_dim, params_tail]
        # params_tail (next param): [data_type, identifier, params_dim, params_tail] — same shape
        cur = params_node
        while cur and getattr(cur, "type", None) in ("params", "params_tail"):
            dt = cur.children[0].value
            pid = cur.children[1].value
            # params_dim indicates array parameters (1D or 2D). For 1D it's [].
            is_array = False
            if len(cur.children) > 2:
                pd = cur.children[2]
                if getattr(pd, "type", None) == "params_dim" and getattr(pd, "children", None):
                    is_array = True
            params.append((pid, dt, is_array))
            tail = cur.children[3]
            if getattr(tail, "type", None) == "params_tail_empty":
                break
            cur = tail
        return params

    # -------------------- Statements --------------------

    def _exec_program(self, node, resume_child_index: int = 0) -> Any:
        # run globals, then atmosphere body only (sub_functions are indexed, not executed)
        for i in range(resume_child_index, len(node.children)):
            child = node.children[i]
            t = getattr(child, "type", None)
            if t == "body":
                self.push_scope()
                try:
                    self._exec(child)
                finally:
                    # Do not pop when pausing for input: variables (e.g. password, correct)
                    # live in this scope; resume must see them.
                    if not self.waiting_for_input:
                        self.pop_scope()
            elif t == "global_dec":
                self._exec(child)
            elif t in ("sub_functions", "sub_functions_empty"):
                pass
            # else: skip (e.g. sub_functions already handled)
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
            paused_depth_before = len(self._paused_stack)
            self._exec(child)
            if self.waiting_for_input:
                # Always persist parent sequence continuation. If child already
                # pushed frames, insert parent below those child frames so child
                # continuation runs first, then parent resumes at next sibling.
                if len(self._paused_stack) == paused_depth_before:
                    self._push_pause_frame(node, i + 1)
                else:
                    self._insert_pause_frame(paused_depth_before, node, i + 1)
                return None
        return None

    def _exec_statement(self, node, resume_child_index: int = 0) -> Any:
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_declaration(self, node, resume_child_index: int = 0) -> Any:
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_structure(self, node, resume_child_index: int = 0) -> Any:
        """
        Execute structure declarations:
        - gust T { ... }~                 -> type definition only, no runtime value
        - gust T x~                       -> default struct instance
        - gust T x = {...}~               -> initialized struct instance
        - gust T arr[n]~                  -> array of struct instances
        - gust T arr[n] = {{...},{...}}~  -> initialized array of structs
        """
        if not getattr(node, "children", None) or len(node.children) < 2:
            return None
        struct_type = node.children[0].value
        struct_tail = node.children[1]
        if getattr(struct_tail, "type", None) != "struct_tail" or not getattr(struct_tail, "children", None):
            return None

        first = struct_tail.children[0]
        # Type definition only: gust T { int a~ ... }~
        if getattr(first, "type", None) == "data_type":
            return None

        # Variable declaration path: gust T var ...
        if getattr(first, "type", None) != "identifier":
            return None
        var_id = first.value
        st2 = struct_tail.children[1] if len(struct_tail.children) > 1 else None

        # Precompute member schema from semantic table.
        members = self.semantic.get_structure(struct_type) if self.semantic else None
        if members is None:
            members = {}

        def make_default_struct() -> Dict[str, Any]:
            obj: Dict[str, Any] = {}
            for member_name, member_type in members.items():
                obj[self._scope_key(member_name)] = self._default_value(member_type)
            return obj

        # gust T var~
        if st2 is None or getattr(st2, "type", None) == "struct_tail2_empty":
            self._declare_in_current_scope(var_id, make_default_struct())
            return None

        # gust T var = { ... }~
        if getattr(st2, "type", None) == "struct_tail2" and getattr(st2, "children", None):
            st2_first = st2.children[0]
            if getattr(st2_first, "type", None) == "operator" and getattr(st2_first, "value", None) == "=":
                values = self._collect_1d_init_values(st2.children[1] if len(st2.children) > 1 else None)
                out = make_default_struct()
                member_names = list(members.keys())
                for i, raw in enumerate(values):
                    if i >= len(member_names):
                        break
                    mn = member_names[i]
                    mt = members[mn]
                    out[self._scope_key(mn)] = self._coerce_to(mt, raw)
                self._declare_in_current_scope(var_id, out)
                return None

            # gust T arr[size] <struct_tail3>
            size_node = st2_first
            declared_len = self._eval_size_to_int(size_node)
            if declared_len is None or declared_len < 0:
                declared_len = 0
            arr = [make_default_struct() for _ in range(declared_len)]
            st3 = st2.children[1] if len(st2.children) > 1 else None
            if getattr(st3, "type", None) == "struct_tail3" and getattr(st3, "children", None):
                init_rows = self._collect_2d_init_rows(st3.children[1] if len(st3.children) > 1 else None)
                member_names = list(members.keys())
                for r, row_vals in enumerate(init_rows):
                    if r >= len(arr):
                        break
                    for i, raw in enumerate(row_vals):
                        if i >= len(member_names):
                            break
                        mn = member_names[i]
                        mt = members[mn]
                        arr[r][self._scope_key(mn)] = self._coerce_to(mt, raw)
            self._declare_in_current_scope(var_id, arr)
            return None
        return None

    def _exec_normal(self, node, resume_child_index: int = 0) -> Any:
        # children: data_type, identifier, norm_dec, norm_tail
        data_type = node.children[0].value
        first_id = node.children[1].value
        self._declare_one(first_id, data_type, node.children[2])
        self._declare_tail(node.children[3], data_type)
        return None

    def _exec_constant(self, node, resume_child_index: int = 0) -> Any:
        # wind <constant>: constant node children = [data_type, id_no, const_dec]
        if not getattr(node, "children", None) or len(node.children) < 3:
            return None
        data_type = node.children[0].value
        id_no = node.children[1].value
        const_dec = node.children[2]
        self._exec_const_dec_one(data_type, id_no, const_dec)
        if getattr(const_dec, "children", None) and len(const_dec.children) >= 3:
            tail = const_dec.children[2]
            if tail and getattr(tail, "type", None) != "const_tail_empty":
                self._exec_const_tail(data_type, tail)
        return None

    def _exec_const_dec_one(self, data_type: str, id_no: str, const_dec) -> None:
        # const_dec: [operator '=', literal_node, const_tail] or [row_size, ...] for array
        if not getattr(const_dec, "children", None) or len(const_dec.children) < 2:
            self._declare_in_current_scope(id_no, self._default_value(data_type))
            return
        first = const_dec.children[0]
        if getattr(first, "type", None) == "operator" and getattr(first, "value", None) == "=":
            literal_node = const_dec.children[1]
            val = self._eval_literal_as_value(literal_node)
            self._declare_in_current_scope(id_no, self._coerce_to(data_type, val))
            return
        if getattr(first, "type", None) == "row_size":
            self._declare_in_current_scope(id_no, [])
            return
        self._declare_in_current_scope(id_no, self._default_value(data_type))

    def _exec_const_tail(self, data_type: str, const_tail_node) -> None:
        # const_tail: [id_no, const_dec]
        if not const_tail_node or getattr(const_tail_node, "type", None) == "const_tail_empty":
            return
        if not getattr(const_tail_node, "children", None) or len(const_tail_node.children) < 2:
            return
        id_no = const_tail_node.children[0].value
        const_dec = const_tail_node.children[1]
        self._exec_const_dec_one(data_type, id_no, const_dec)
        if getattr(const_dec, "children", None) and len(const_dec.children) >= 3:
            tail = const_dec.children[2]
            if tail and getattr(tail, "type", None) != "const_tail_empty":
                self._exec_const_tail(data_type, tail)

    def _declare_tail(self, norm_tail_node, data_type: str) -> None:
        # Same as working interpreter branch: [identifier, norm_dec, norm_tail]
        if norm_tail_node is None or getattr(norm_tail_node, "type", None) == "norm_tail_empty":
            return
        cur = norm_tail_node
        while cur and getattr(cur, "type", None) == "norm_tail":
            if not getattr(cur, "children", None) or len(cur.children) < 2:
                break
            first = cur.children[0]
            vid = getattr(first, "value", None) if first is not None else None
            if vid is None:
                break
            self._declare_one(vid, data_type, cur.children[1])
            cur = cur.children[2] if len(cur.children) > 2 else None

    def _declare_one(self, identifier_token_type: str, data_type: str, norm_dec_node) -> None:
        # norm_dec: row_size/array OR '=' expr OR empty (None when parser hit error)
        if norm_dec_node is None or getattr(norm_dec_node, "type", None) == "norm_dec_empty":
            self._declare_in_current_scope(identifier_token_type, self._default_value(data_type))
            return
        if getattr(norm_dec_node, "type", None) == "norm_dec" and norm_dec_node.children:
            first = norm_dec_node.children[0]
            if getattr(first, "type", None) == "operator" and first.value == "=":
                expr = norm_dec_node.children[1]
                val = self._eval_expr(expr)
                self._declare_in_current_scope(identifier_token_type, self._coerce_to(data_type, val))
                return
            if getattr(first, "type", None) == "row_size":
                # arrays: build runtime list; support initializer list if present
                # norm_dec children: [row_size, array] when array initializer exists
                init_array = None
                if len(norm_dec_node.children) > 1:
                    maybe_array = norm_dec_node.children[1]
                    if getattr(maybe_array, "type", None) == "array" and getattr(maybe_array, "children", None):
                        init_array = maybe_array
                value = self._build_array_value(data_type, first, init_array)
                self._declare_in_current_scope(identifier_token_type, value)
                return
        self._declare_in_current_scope(identifier_token_type, self._default_value(data_type))

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
        # support element assignment for arrays (vid[index] = expr)
        if tail.children and getattr(tail.children[0], "type", None) == "unary_op":
            op = tail.children[0].value
            self._apply_incdec(op, vid, prefix=False)
            return None
        assignment = tail.children[0]
        return self._exec_assignment_with_access(vid, id_access, assignment)

    def _exec_assignment(self, vid: str, assignment_node) -> Any:
        # children: assi_op, expr
        op_node = assignment_node.children[0]
        expr_node = assignment_node.children[1]
        op = op_node.children[0].value  # operator node value
        rhs = self._eval_expr(expr_node)
        if op == "=":
            self._assign(vid, rhs)
            return None
        cur = self._lookup(vid)
        if op == "+=":
            self._assign(vid, (cur if cur is not None else 0) + rhs)
        elif op == "-=":
            self._assign(vid, (cur if cur is not None else 0) - rhs)
        elif op == "*=":
            self._assign(vid, (cur if cur is not None else 0) * rhs)
        elif op == "/=":
            if rhs == 0:
                raise InterpreterError("Division by zero")
            cur_val = cur if cur is not None else 0
            if isinstance(cur_val, int) and isinstance(rhs, int):
                self._assign(vid, cur_val // rhs)
            else:
                self._assign(vid, cur_val / rhs)
        elif op == "%=":
            if rhs == 0:
                raise InterpreterError("Modulo by zero")
            if isinstance(rhs, float):
                raise InterpreterError("Modulo operator requires integer operands")
            cur_val = cur if cur is not None else 0
            self._assign(vid, cur_val % rhs)
        return None

    def _exec_assignment_with_access(self, vid: str, id_access_node, assignment_node) -> Any:
        """
        Assign to either a plain variable (vid) or an indexed array element (vid[...]).
        """
        # If there's no dimension in id_access, fall back to whole-variable assignment
        if not id_access_node or not getattr(id_access_node, "children", None):
            return self._exec_assignment(vid, assignment_node)
        first = id_access_node.children[0]
        member_node = id_access_node.children[1] if len(id_access_node.children) > 1 else None
        member_id = None
        if getattr(member_node, "type", None) == "id_member" and getattr(member_node, "children", None):
            member_id = member_node.children[1].value

        # Evaluate RHS first
        op_node = assignment_node.children[0]
        expr_node = assignment_node.children[1]
        op = op_node.children[0].value
        rhs = self._eval_expr(expr_node)

        # Struct member assignment path (with or without array index).
        if member_id is not None:
            return self._assign_struct_member_with_access(vid, first, member_id, op, rhs)

        # Only '=' supported for array element assignment right now
        if op != "=":
            return self._exec_assignment(vid, assignment_node)

        if getattr(first, "type", None) != "dimension":
            return self._exec_assignment(vid, assignment_node)

        # Evaluate indices from dimension -> row_size
        indices = self._eval_dimension_indices(first)
        if not indices:
            return self._exec_assignment(vid, assignment_node)

        arr = self._lookup(vid)
        if not isinstance(arr, list):
            raise InterpreterError(f"'{self.semantic.get_actual_name(vid)}' is not an array")

        if len(indices) == 1:
            i = indices[0]
            if i < 0 or i >= len(arr):
                raise InterpreterError("Array out of bounds")
            arr[i] = self._coerce_to(self._lookup_declared_type(vid), rhs)
            return None
        if len(indices) == 2:
            r, c = indices
            if r < 0 or r >= len(arr) or not isinstance(arr[r], list):
                raise InterpreterError("Array out of bounds")
            if c < 0 or c >= len(arr[r]):
                raise InterpreterError("Array out of bounds")
            arr[r][c] = self._coerce_to(self._lookup_declared_type(vid), rhs)
            return None
        return None

    def _exec_input_output(self, node, resume_child_index: int = 0) -> Any:
        # children: ['inhale', id, id_access] OR ['exhale', output]
        if not getattr(node, "children", None) or len(node.children) < 2:
            return None
        kind = node.children[0]
        if kind == "inhale":
            id_node = node.children[1]
            vid = getattr(id_node, "value", None)
            if vid is None:
                return None
            dim = None
            id_access = None
            if len(node.children) > 2:
                id_access = node.children[2]
                if id_access and getattr(id_access, "children", None):
                    first = id_access.children[0]
                    if getattr(first, "type", None) == "dimension":
                        dim = first
            self.waiting_for_input = True
            self.input_request = InputRequest(
                target_identifier=vid,
                prompt="",
                dimension_node=dim,
                id_access_node=id_access,
            )
            return None
        if kind == "exhale":
            out_node = node.children[1]
            try:
                text = self._eval_output(out_node)
            except Exception:
                text = ""
            self.emit(self._to_oxc_text(text))
            return None
        return None

    def _exec_conditioner(self, node, resume_child_index: int = 0) -> Any:
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_switch_stat(self, node, resume_child_index: int = 0) -> Any:
        # switch_stat: [id_no, id_access_node, switch_cases_node, switch_def_node]
        id_no, id_access_node, switch_cases_node, switch_def_node = node.children[0], node.children[1], node.children[2], node.children[3]
        vid = id_no.value
        dim = None
        if id_access_node and getattr(id_access_node, "children", None):
            first = id_access_node.children[0]
            if getattr(first, "type", None) == "dimension":
                dim = first
        if dim is not None:
            switch_val = self.read_indexed_value(vid, dim)
        else:
            switch_val = self._lookup(vid)
        matched = False
        cur = switch_cases_node
        switch_dtype = self._lookup_declared_type(vid)
        while cur and getattr(cur, "type", None) == "switch_cases":
            case_raw = cur.children[0].value
            # Normalize case constant using literal semantics so both int and char
            # cases compare correctly against the runtime switch value.
            case_const = self._literal_to_value(case_raw)
            # Apply same implicit conversions as assignment (e.g. char case vs int variable → ASCII).
            case_const = self._coerce_to(switch_dtype, case_const)
            stmt_list = cur.children[1]
            if switch_val == case_const:
                matched = True
                self.push_scope()
                try:
                    self._exec(stmt_list)
                finally:
                    self.pop_scope()
                break
            cur = cur.children[2]
        if not matched and getattr(switch_def_node, "type", None) == "switch_def" and getattr(switch_def_node, "children", None):
            self.push_scope()
            try:
                self._exec(switch_def_node.children[0])
            finally:
                self.pop_scope()
        return None

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
            if resume_child_index == 2:
                loop_signal = self._pending_loop_signal
                self._pending_loop_signal = None
                self.pop_scope()  # body scope preserved during pause
                if loop_signal == "break":
                    self.pop_scope()  # for-loop outer scope
                    return None
                self._exec(update)
                while self._eval_cond(cond):
                    try:
                        self.push_scope()
                        try:
                            self._push_pause_frame(node, 2)
                            self._exec(body)
                        finally:
                            if not self.waiting_for_input:
                                self.pop_scope()
                                self._drop_top_pause_frame(node, 2)
                    except BreakSignal:
                        self.pop_scope()
                        return None
                    except ContinueSignal:
                        if self.waiting_for_input:
                            return None
                        self._exec(update)
                        continue
                    if self.waiting_for_input:
                        return None
                    self._exec(update)
                self.pop_scope()  # for-loop outer scope
                return None
            self.push_scope()
            try:
                self._exec(init)
                while self._eval_cond(cond):
                    try:
                        self.push_scope()
                        try:
                            self._push_pause_frame(node, 2)
                            self._exec(body)
                        finally:
                            if not self.waiting_for_input:
                                self.pop_scope()
                                self._drop_top_pause_frame(node, 2)
                    except BreakSignal:
                        break
                    except ContinueSignal:
                        if self.waiting_for_input:
                            return None
                        self._exec(update)
                        continue
                    if self.waiting_for_input:
                        return None
                    self._exec(update)
            finally:
                if not self.waiting_for_input:
                    self.pop_scope()
            return None

        # while-loop form
        cond, body = node.children
        # resume_child_index=1: one more iteration (re-eval condition, run body); used when resuming after inhale
        if resume_child_index == 1:
            loop_signal = self._pending_loop_signal
            self._pending_loop_signal = None
            if loop_signal == "break":
                self.pop_scope()  # body scope preserved during pause
                return None
            if loop_signal == "continue":
                self.pop_scope()  # body scope preserved during pause
            if not self._eval_cond(cond):
                return None
            self.push_scope()
            try:
                self._push_pause_frame(node, 1)
                try:
                    self._exec(body)
                except BreakSignal:
                    # Break raised while resuming current while-body should exit
                    # this loop, not be re-routed through outer paused frames.
                    return None
                except ContinueSignal:
                    # Continue while resuming one iteration ends this resumed
                    # body run; caller will continue from correct loop context.
                    return None
            finally:
                if not self.waiting_for_input:
                    self.pop_scope()
                    self._drop_top_pause_frame(node, 1)
            return None
        while self._eval_cond(cond):
            try:
                self.push_scope()
                try:
                    self._push_pause_frame(node, 1)
                    self._exec(body)
                finally:
                    if not self.waiting_for_input:
                        self.pop_scope()
                        self._drop_top_pause_frame(node, 1)
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
        # for_vals can be literal (node.value) or id<id_access> (node.children = [id_no, id_access_node])
        for_vals_node = node.children[2]
        if getattr(for_vals_node, "children", None) and len(for_vals_node.children) >= 1:
            # id<id_access> form: evaluate the variable
            vid_for = for_vals_node.children[0].value
            val = self._lookup(vid_for)
            if val is None:
                raise InterpreterError("Undefined variable in for loop initial value")
        else:
            val = self._literal_to_value(getattr(for_vals_node, "value", None))
        if getattr(node.children[0], "type", None) == "data_type":
            dt = node.children[0].value
            vid = node.children[1].value
            self._declare_in_current_scope(vid, self._coerce_to(dt, val))
            return None
        vid = node.children[0].value
        self._assign(vid, val)
        return None

    def _exec_stmt_ctrl(self, node, resume_child_index: int = 0) -> Any:
        """
        Same as stmt_list: iterate children and set resume point when inhale
        triggers waiting_for_input, so execution continues after the inhale (e.g.
        if/else in a cycle body) when the user provides input.
        """
        if not getattr(node, "children", None):
            return None
        for i in range(resume_child_index, len(node.children)):
            child = node.children[i]
            paused_depth_before = len(self._paused_stack)
            self._exec(child)
            if self.waiting_for_input:
                # Preserve both parent and child continuation; place parent below
                # child frames to maintain correct resume order.
                if len(self._paused_stack) == paused_depth_before:
                    self._push_pause_frame(node, i + 1)
                else:
                    self._insert_pause_frame(paused_depth_before, node, i + 1)
                return None
        return None

    def _exec_ctrl_flow(self, node, resume_child_index: int = 0) -> Any:
        v = getattr(node, "value", None)
        if v == "resist":
            raise BreakSignal()
        if v == "flow":
            raise ContinueSignal()
        # return_stat is nested in ctrl_flow via statement production
        return self._exec_generic(node, resume_child_index=resume_child_index)

    def _exec_return_stat_node(self, node, resume_child_index: int = 0) -> Any:
        """Parser wraps gasp in return_stat_node; single child is return_stat. Ensure we run it."""
        if getattr(node, "children", None) and len(node.children) > 0:
            return self._exec(node.children[0], resume_child_index=resume_child_index)
        return None

    def _exec_return_stat(self, node, resume_child_index: int = 0) -> Any:
        val = self._eval_expr(node.children[0])
        # Force naur/yuh to Python bool (handles string, token, or any wrapper)
        if val == "naur" or getattr(val, "value", None) == "naur":
            val = False
        elif val == "yuh" or getattr(val, "value", None) == "yuh":
            val = True
        # Normalize by declared return type so ReturnSignal always carries the right type
        if self._current_function_name:
            declared = self.function_return_type.get(self._current_function_name)
            if declared == "bool":
                val = self._coerce_to("bool", val)
        raise ReturnSignal(val)

    # -------------------- Function calls --------------------

    def _call_user_function(self, func_id_token_type: str, param_opts_node) -> Any:
        func_name = self.semantic.get_actual_name(func_id_token_type)
        if func_name not in self.functions:
            raise InterpreterError(f"Undefined function '{func_name}'")
        if self._pending_call is not None:
            self._finalize_pending_call_if_ready()
            if self._pending_call is not None:
                raise InterpreterError("Internal error: nested pending function calls are not supported")

        args = self._eval_param_opts(param_opts_node)
        params = self.function_params.get(func_name, [])

        if len(args) != len(params):
            raise InterpreterError(
                f"Function '{func_name}' expects {len(params)} args, got {len(args)}"
            )

        func_node = self.functions[func_name]
        body = func_node.children[3]
        return_stat = func_node.children[4]

        self._current_function_name = func_name
        self.push_scope()
        for (pid, ptype, is_array), aval in zip(params, args):
            key = self._scope_key(pid)
            if is_array:
                if not isinstance(aval, list):
                    self._current_function_name = None
                    self.pop_scope()
                    raise InterpreterError(
                        f"Argument for array parameter '{self.semantic.get_actual_name(pid)}' must be an array"
                    )
                # Arrays are passed by reference: store the list as-is
                self.scopes[-1][key] = aval
            else:
                self.scopes[-1][key] = self._coerce_to(ptype, aval)
        try:
            self._exec(body)
            if self.waiting_for_input:
                # Keep function scope alive until input-driven resume completes.
                self._pending_call = {
                    "func_name": func_name,
                    "return_stat": return_stat,
                    "scope_depth": len(self.scopes),
                }
                return None
            # explicit return statement node exists; execute it to return value or nothing
            rt = getattr(return_stat, "type", None)
            if rt == "return_stat" or (
                getattr(return_stat, "children", None) and len(return_stat.children) > 0
            ):
                try:
                    self._exec(return_stat)
                except ReturnSignal as r:
                    result = self._normalize_return(r.value, func_name)
                    self._current_function_name = None
                    self.pop_scope()
                    return result
            self._current_function_name = None
            self.pop_scope()
            return None
        except ReturnSignal as r:
            result = self._normalize_return(r.value, func_name)
            self._current_function_name = None
            self.pop_scope()
            return result
        except Exception:
            self._current_function_name = None
            self.pop_scope()
            raise

    def _normalize_return(self, value: Any, func_name: str) -> Any:
        """Ensure function return value matches declared type (e.g. bool -> Python True/False)."""
        declared = self.function_return_type.get(func_name)
        if declared == "bool":
            return self._coerce_to("bool", value)
        return value

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
        return self._to_bool(v)

    def _to_bool(self, v: Any) -> bool:
        """Convert OxC value to Python bool; treat 'naur' and token-with-naur as False."""
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            if v == "" or v == "naur":
                return False
            if v == "yuh":
                return True
        if hasattr(v, "value"):
            x = getattr(v, "value", None)
            if x == "naur":
                return False
            if x == "yuh":
                return True
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
        if bool(left):
            return left
        right = self._eval_and(node.children[0])
        return self._eval_or_tail(bool(left) or bool(right), node.children[1])

    def _eval_and(self, node) -> Any:
        if node.type == "and_expr":
            left = self._eval_rela(node.children[0])
            return self._eval_and_tail(left, node.children[1])
        return self._eval_generic_expr(node)

    def _eval_and_tail(self, left, node) -> Any:
        if node.type == "and_tail_empty":
            return left
        if not bool(left):
            return left
        right = self._eval_rela(node.children[0])
        return self._eval_and_tail(bool(left) and bool(right), node.children[1])

    def _eval_rela(self, node) -> Any:
        if node.type == "rela_expr":
            left = self._eval_arith(node.children[0])
            tail = node.children[1]
            if tail.type == "rela_tail_empty":
                return left
            op = tail.children[0].children[0].value
            right = self._eval_arith(tail.children[1])
            # For ordering operators, coerce to numbers so loop conditions like i <= height work
            # even if height was stored as a string (e.g. from input).
            if op in ("<", "<=", ">", ">="):
                try:
                    # Coerce both sides atomically; avoid partial conversion that
                    # can produce mixed-type comparisons (e.g., int >= str).
                    lnum = self._to_arith_value(left)
                    rnum = self._to_arith_value(right)
                    left = lnum
                    right = rnum
                except InterpreterError:
                    pass  # fall back to raw comparison
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

    def _to_arith_value(self, value: Any) -> Any:
        """Coerce value to int or float for arithmetic. Prevents + from doing string concatenation."""
        if value is None:
            return 0
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                if "." in value:
                    return float(value)
                return int(value)
            except (ValueError, TypeError):
                raise InterpreterError(f"Arithmetic requires numeric operands, got string: {value!r}")
        raise InterpreterError(f"Arithmetic requires numeric operands, got {type(value).__name__}")

    def _eval_arith_tail(self, left, node) -> Any:
        if node.type == "arith_tail_empty":
            return left
        op = node.children[0].children[0].value
        right = self._eval_term(node.children[1])
        left = self._to_arith_value(left)
        right = self._to_arith_value(right)
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
        left = self._to_arith_value(left)
        right = self._to_arith_value(right)
        if op == "*":
            left = left * right
        elif op == "/":
            if right == 0:
                raise InterpreterError("Division by zero")
            if isinstance(left, int) and isinstance(right, int):
                left = left // right
            else:
                left = left / right
        elif op == "%":
            if right == 0:
                raise InterpreterError("Modulo by zero")
            if isinstance(left, float) or isinstance(right, float):
                raise InterpreterError("Modulo operator requires integer operands")
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
            out_node = node.children[0]
            # In expression context we need the value (e.g. bool for conditions), not the string for exhale.
            if getattr(out_node, "children", None) and len(out_node.children) == 1:
                lit = out_node.children[0]
                if getattr(lit, "type", None) == "literal":
                    return self._eval_literal_as_value(lit)
            return self._eval_output(node.children[0])
        if node.children and getattr(node.children[0], "type", None) == "logic_expr":
            return not bool(self._eval_logic(node.children[0]))
        # In expression context, literal must yield value (int/float/bool), not string.
        if node.children and getattr(node.children[0], "type", None) == "literal":
            return self._eval_literal_as_value(node.children[0])
        return self._eval_generic_expr(node)

    def _eval_negate(self, node) -> Any:
        # negate -> (expr) | id id_access
        if node.children and getattr(node.children[0], "type", None) == "expr":
            return self._eval_expr(node.children[0])
        vid = node.children[0].value
        id_access = node.children[1] if len(node.children) > 1 else None
        if id_access and getattr(id_access, "type", None) == "id_access":
            return self._read_identifier_with_access(vid, id_access)
        return self._lookup(vid)

    def _eval_output(self, node) -> Any:
        # output -> literal (parser wraps in literal); literal may contain value or output_concat+output_tail
        if not getattr(node, "children", None) or len(node.children) == 0:
            return ""
        child = node.children[0]
        ctype = getattr(child, "type", None)
        if ctype == "identifier":
            return self._eval_identifier(child)
        if ctype == "function_call":
            return self._eval_function_call(child)
        if ctype == "literal":
            return self._eval_literal(child)
        # Nested "output" from parse_output_concat (e.g. exhale(x) produces output->literal with concat = output->identifier)
        if ctype == "output" and getattr(child, "children", None) and len(child.children) == 1:
            return self._eval_output(child)
        return ""

    def _eval_literal(self, node) -> Any:
        # literal -> value OR output_concat + output_tail (string/char concatenation)
        if not getattr(node, "children", None) or len(node.children) == 0:
            return ""
        c0 = node.children[0]
        if getattr(c0, "type", None) == "value":
            return self._literal_to_value(getattr(c0, "value", None))
        # concat form: treat everything as string and join
        parts: List[str] = []
        self._collect_output_concat(node, parts)
        return "".join(parts)

    def _eval_literal_as_value(self, node) -> Any:
        """Evaluate literal in expression context: return actual value (int/float/bool), not string."""
        if not getattr(node, "children", None) or len(node.children) == 0:
            return None
        c0 = node.children[0]
        if getattr(c0, "type", None) == "value":
            return self._literal_to_value(c0.value)
        if len(node.children) >= 2:
            concat_node, tail_node = node.children[0], node.children[1]
            tail_empty = getattr(tail_node, "type", None) in ("output_tail_empty", None) or not getattr(tail_node, "children", None)
            if tail_empty and getattr(concat_node, "type", None) == "output" and getattr(concat_node, "children", None) and len(concat_node.children) == 1:
                single = concat_node.children[0]
                if getattr(single, "type", None) == "identifier":
                    return self._eval_identifier(single)
                if getattr(single, "type", None) == "function_call":
                    return self._eval_function_call(single)
        return self._eval_literal(node)

    def _collect_output_concat(self, node, parts: List[str]) -> None:
        if node is None:
            return
        # Nested "output" from parse_output_concat (e.g. id or function_call in exhale)
        if getattr(node, "type", None) == "output" and getattr(node, "children", None) and len(node.children) == 1:
            self._collect_output_concat(node.children[0], parts)
            return
        # Single identifier or function_call wrapped in "output" (from parse_output_concat for id)
        if getattr(node, "type", None) == "identifier":
            v = self._eval_identifier(node)
            parts.append(self._to_oxc_text(v))
            return
        if getattr(node, "type", None) == "function_call":
            v = self._eval_function_call(node)
            parts.append(self._to_oxc_text(v))
            return
        if not getattr(node, "children", None):
            return
        # literal has [output_concat, output_tail]
        if getattr(node, "type", None) == "literal" and len(node.children) >= 2:
            concat_node = node.children[0]
            tail_node = node.children[1]
            if getattr(concat_node, "type", None) == "output_content":
                parts.append(self._to_oxc_text(self._literal_to_value(concat_node.value)))
            elif getattr(concat_node, "type", None) == "value":
                parts.append(self._to_oxc_text(self._literal_to_value(concat_node.value)))
            elif getattr(concat_node, "type", None) == "identifier":
                v = self._eval_identifier(concat_node)
                parts.append(self._to_oxc_text(v))
            elif getattr(concat_node, "type", None) == "function_call":
                v = self._eval_function_call(concat_node)
                parts.append(self._to_oxc_text(v))
            else:
                self._collect_output_concat(concat_node, parts)
            if getattr(tail_node, "type", None) == "output_tail" and getattr(tail_node, "children", None) and len(tail_node.children) >= 2:
                self._collect_output_concat(tail_node, parts)
            return
        if getattr(node, "type", None) == "output_tail" and getattr(node, "children", None) and len(node.children) >= 2:
            concat_node = node.children[0]
            tail_node = node.children[1]
            if getattr(concat_node, "type", None) == "output_content":
                parts.append(self._to_oxc_text(self._literal_to_value(concat_node.value)))
            elif getattr(concat_node, "type", None) == "value":
                parts.append(self._to_oxc_text(self._literal_to_value(concat_node.value)))
            elif getattr(concat_node, "type", None) == "identifier":
                v = self._eval_identifier(concat_node)
                parts.append(self._to_oxc_text(v))
            elif getattr(concat_node, "type", None) == "function_call":
                v = self._eval_function_call(concat_node)
                parts.append(self._to_oxc_text(v))
            else:
                self._collect_output_concat(concat_node, parts)
            self._collect_output_concat(tail_node, parts)
            return
        for ch in node.children:
            if getattr(ch, "type", None) == "output_content":
                parts.append(self._to_oxc_text(self._literal_to_value(ch.value)))
            elif getattr(ch, "type", None) == "value":
                parts.append(self._to_oxc_text(self._literal_to_value(ch.value)))
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
        # id_tail may contain id_access (dimension or member). Support array indexing.
        if getattr(tail, "type", None) == "id_tail" and getattr(tail, "children", None):
            id_access = tail.children[0]
            if getattr(id_access, "type", None) == "id_access":
                return self._read_identifier_with_access(id_no, id_access)
        # plain variable reference
        return self._lookup(id_no)

    def _read_identifier_with_access(self, id_no: str, id_access) -> Any:
        if not getattr(id_access, "children", None):
            return self._lookup(id_no)
        dim_node = id_access.children[0] if len(id_access.children) > 0 else None
        member_node = id_access.children[1] if len(id_access.children) > 1 else None
        member_id = None
        if getattr(member_node, "type", None) == "id_member" and getattr(member_node, "children", None):
            member_id = member_node.children[1].value

        if member_id is not None:
            return self._read_struct_member_with_access(id_no, dim_node, member_id)

        if getattr(dim_node, "type", None) == "dimension":
            return self.read_indexed_value(id_no, dim_node)
        return self._lookup(id_no)

    def _read_struct_member_with_access(self, id_token_type: str, dim_node, member_id: str) -> Any:
        member_key = self._scope_key(member_id)
        base = self._lookup(id_token_type)
        target = base
        if getattr(dim_node, "type", None) == "dimension":
            indices = self._eval_dimension_indices(dim_node)
            if len(indices) != 1:
                raise InterpreterError("Array out of bounds")
            i = indices[0]
            if not isinstance(base, list):
                raise InterpreterError(f"'{self.semantic.get_actual_name(id_token_type)}' is not an array")
            if i < 0 or i >= len(base):
                raise InterpreterError("Array out of bounds")
            target = base[i]
        if not isinstance(target, dict):
            raise InterpreterError(f"'{self.semantic.get_actual_name(id_token_type)}' is not a structure")
        if member_key not in target:
            raise InterpreterError(
                f"'{self.semantic.get_actual_name(member_id)}' is not a member of structure '{self.semantic.get_actual_name(id_token_type)}'"
            )
        return target[member_key]

    def _assign_struct_member_with_access(self, id_token_type: str, dim_node, member_id: str, op: str, rhs: Any) -> None:
        base = self._lookup(id_token_type)
        target = base
        if getattr(dim_node, "type", None) == "dimension":
            indices = self._eval_dimension_indices(dim_node)
            if len(indices) != 1:
                raise InterpreterError("Array out of bounds")
            i = indices[0]
            if not isinstance(base, list):
                raise InterpreterError(f"'{self.semantic.get_actual_name(id_token_type)}' is not an array")
            if i < 0 or i >= len(base):
                raise InterpreterError("Array out of bounds")
            target = base[i]
        if not isinstance(target, dict):
            raise InterpreterError(f"'{self.semantic.get_actual_name(id_token_type)}' is not a structure")

        member_key = self._scope_key(member_id)
        if member_key not in target:
            raise InterpreterError(
                f"'{self.semantic.get_actual_name(member_id)}' is not a member of structure '{self.semantic.get_actual_name(id_token_type)}'"
            )

        # Resolve declared member type when possible; fall back to current runtime type.
        member_type = None
        symbol = self.semantic.lookup(id_token_type) if self.semantic else None
        struct_type = symbol.get("struct_type") if isinstance(symbol, dict) else None
        members = self.semantic.get_structure(struct_type) if (self.semantic and struct_type) else None
        if members:
            for mk, mt in members.items():
                if self._scope_key(mk) == member_key:
                    member_type = mt
                    break

        def coerce_member(v: Any) -> Any:
            if member_type is not None:
                return self._coerce_to(member_type, v)
            curv = target.get(member_key)
            if isinstance(curv, bool):
                return self._coerce_to("bool", v)
            if isinstance(curv, int):
                return self._coerce_to("int", v)
            if isinstance(curv, float):
                return self._coerce_to("float", v)
            if isinstance(curv, str):
                # Keep single-char as char-like, otherwise string.
                return self._coerce_to("char" if len(curv) == 1 else "string", v)
            return v

        cur = target[member_key]
        if op == "=":
            target[member_key] = coerce_member(rhs)
            return
        if op == "+=":
            target[member_key] = coerce_member((cur if cur is not None else 0) + rhs)
            return
        if op == "-=":
            target[member_key] = coerce_member((cur if cur is not None else 0) - rhs)
            return
        if op == "*=":
            target[member_key] = coerce_member((cur if cur is not None else 0) * rhs)
            return
        if op == "/=":
            if rhs == 0:
                raise InterpreterError("Division by zero")
            cur_val = cur if cur is not None else 0
            if isinstance(cur_val, int) and isinstance(rhs, int):
                target[member_key] = coerce_member(cur_val // rhs)
            else:
                target[member_key] = coerce_member(cur_val / rhs)
            return
        if op == "%=":
            if rhs == 0:
                raise InterpreterError("Modulo by zero")
            cur_val = cur if cur is not None else 0
            target[member_key] = coerce_member(cur_val % rhs)

    def _eval_size_to_int(self, size_node) -> Optional[int]:
        # size -> arith_expr | empty
        if size_node is None:
            return None
        if getattr(size_node, "type", None) == "size" and getattr(size_node, "children", None):
            try:
                return int(self._to_arith_value(self._eval_arith(size_node.children[0])))
            except Exception:
                return None
        return None

    def _collect_1d_init_values(self, node) -> List[Any]:
        out: List[Any] = []
        self._collect_array_init_1d(node, out)
        return out

    def _collect_2d_init_rows(self, node) -> List[List[Any]]:
        rows: List[List[Any]] = []
        self._collect_array_init_2d(node, rows)
        return rows

    def read_indexed_value(self, id_token_type: str, dimension_node) -> Any:
        """
        Read an array element for TAC INDEX_LOAD. dimension_node is the AST 'dimension'
        inside id_access (same as _eval_identifier).
        """
        if dimension_node is None or getattr(dimension_node, "type", None) != "dimension":
            return self._lookup(id_token_type)
        indices = self._eval_dimension_indices(dimension_node)
        val = self._lookup(id_token_type)
        if not indices:
            return val
        if not isinstance(val, list):
            if isinstance(val, str):
                if len(indices) != 1:
                    raise InterpreterError(
                        f"String indexing on '{self.semantic.get_actual_name(id_token_type)}' supports only one index"
                    )
                i = indices[0]
                if i < 0 or i >= len(val):
                    raise InterpreterError("String index out of bounds")
                return val[i]
            if val is None and self._lookup_declared_type(id_token_type) == "string":
                raise InterpreterError(
                    f"Cannot index uninitialized string '{self.semantic.get_actual_name(id_token_type)}'"
                )
            raise InterpreterError(f"'{self.semantic.get_actual_name(id_token_type)}' is not an array")
        if len(indices) == 1:
            i = indices[0]
            if i < 0 or i >= len(val):
                raise InterpreterError("Array out of bounds")
            return val[i]
        if len(indices) == 2:
            r, c = indices
            if r < 0 or r >= len(val) or not isinstance(val[r], list):
                raise InterpreterError("Array out of bounds")
            if c < 0 or c >= len(val[r]):
                raise InterpreterError("Array out of bounds")
            return val[r][c]
        return val

    def assign_indexed_value(self, id_token_type: str, dimension_node, value: Any) -> None:
        """
        Write an array element for TAC STORE_INDEX. dimension_node is AST 'dimension'.
        """
        if dimension_node is None or getattr(dimension_node, "type", None) != "dimension":
            self._assign(id_token_type, value)
            return
        indices = self._eval_dimension_indices(dimension_node)
        if not indices:
            self._assign(id_token_type, value)
            return
        arr = self._lookup(id_token_type)
        if not isinstance(arr, list):
            raise InterpreterError(f"'{self.semantic.get_actual_name(id_token_type)}' is not an array")
        dtype = self._lookup_declared_type(id_token_type)
        coerced = self._coerce_to(dtype, value)
        if len(indices) == 1:
            i = indices[0]
            if i < 0 or i >= len(arr):
                raise InterpreterError("Array out of bounds")
            arr[i] = coerced
            return
        if len(indices) == 2:
            r, c = indices
            if r < 0 or r >= len(arr) or not isinstance(arr[r], list):
                raise InterpreterError("Array out of bounds")
            if c < 0 or c >= len(arr[r]):
                raise InterpreterError("Array out of bounds")
            arr[r][c] = coerced
            return

    def assign_input_target(self, id_token_type: str, value: Any, id_access_node=None, dimension_node=None) -> None:
        """
        Assign inhale input target:
        - plain variable: inhale(x)
        - indexed array: inhale(arr[i])
        - struct member / indexed struct member: inhale(obj.member), inhale(arr[i].member)
        """
        # Prefer full id_access when available (supports struct members).
        if id_access_node is not None and getattr(id_access_node, "children", None):
            member_node = id_access_node.children[1] if len(id_access_node.children) > 1 else None
            member_id = None
            if getattr(member_node, "type", None) == "id_member" and getattr(member_node, "children", None):
                member_id_node = member_node.children[1] if len(member_node.children) > 1 else None
                member_id = getattr(member_id_node, "value", None) if member_id_node is not None else None
            if member_id is not None:
                dim_node = id_access_node.children[0] if len(id_access_node.children) > 0 else None
                self._assign_struct_member_with_access(id_token_type, dim_node, member_id, "=", value)
                return
            first = id_access_node.children[0] if len(id_access_node.children) > 0 else None
            if getattr(first, "type", None) == "dimension":
                self.assign_indexed_value(id_token_type, first, value)
                return

        # Backward-compatible path (dimension only).
        if dimension_node is not None:
            self.assign_indexed_value(id_token_type, dimension_node, value)
            return

        self._assign(id_token_type, value)

    def _eval_function_call(self, node) -> Any:
        """Evaluate predefined built-in: toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft."""
        name = getattr(node, "value", None)
        if not name or not getattr(node, "children", None):
            return None
        children = node.children

        def get_arg(i: int):
            # param_item -> expr wrapper; param_item has one child which is the expr node
            item = children[i]
            if getattr(item, "type", None) == "param_item" and getattr(item, "children", None):
                return self._eval_expr(item.children[0])
            return self._eval_expr(item) if item else None

        if name == "toRise":
            v = get_arg(0)
            if v is None:
                return None
            s = str(v) if not isinstance(v, str) else v
            return s.upper()
        if name == "toFall":
            v = get_arg(0)
            if v is None:
                return None
            s = str(v) if not isinstance(v, str) else v
            return s.lower()
        if name == "horizon":
            v = get_arg(0)
            if v is None:
                return 0
            if isinstance(v, str):
                return len(v)
            if isinstance(v, (int, float)):
                # float: exclude decimal point (e.g. 234.34 -> 5)
                s = str(v).replace(".", "")
                return len(s)
            return 0
        if name == "sizeOf":
            v = get_arg(0)
            if v is None:
                return 0
            if isinstance(v, list):
                return len(v)
            if isinstance(v, dict):
                return len(v)
            return 0
        if name == "toInt":
            v = get_arg(0)
            if v is None or v == "":
                return 0
            try:
                return int(float(str(v)))
            except (ValueError, TypeError):
                raise InterpreterError(f"toInt: cannot convert '{v}' to int")
        if name == "toFloat":
            v = get_arg(0)
            if v is None or v == "":
                return 0.0
            try:
                return float(str(v))
            except (ValueError, TypeError):
                raise InterpreterError(f"toFloat: cannot convert '{v}' to float")
        if name == "toString":
            v = get_arg(0)
            if v is None:
                return ""
            if isinstance(v, bool):
                return "yuh" if v else "naur"
            return str(v)
        if name == "toChar":
            v = get_arg(0)
            if v is None:
                return None
            if isinstance(v, str) and len(v) == 1:
                return v
            if isinstance(v, str) and len(v) > 0:
                return v[0]
            try:
                return chr(int(v))
            except (ValueError, TypeError):
                raise InterpreterError(f"toChar: cannot convert '{v}' to char")
        if name == "toBool":
            v = get_arg(0)
            if v is None:
                return False
            if v == "" or v == 0 or v == 0.0:
                return False
            return True
        if name == "waft":
            v1, v2 = get_arg(0), get_arg(1)
            try:
                f = float(v1)
                n = int(v2)
            except (ValueError, TypeError):
                raise InterpreterError(f"waft: expected (float, int), got ({v1}, {v2})")
            return round(f, n)
        return None

    def _eval_generic_expr(self, node) -> Any:
        # wrapper nodes: delegate to first meaningful child
        if node.type == "function_call":
            return self._eval_function_call(node)
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

    def _unescape_string(self, s: str) -> str:
        """Expand common escape sequences so \\n, \\t, etc. work reliably (including leading \\n)."""
        if not s:
            return s
        out = []
        i = 0
        while i < len(s):
            if s[i] == "\\" and i + 1 < len(s):
                c = s[i + 1]
                if c == "n":
                    out.append("\n")
                elif c == "t":
                    out.append("\t")
                elif c == "r":
                    out.append("\r")
                elif c == "\\":
                    out.append("\\")
                elif c == "@":
                    out.append("@")
                elif c == '"':
                    out.append('"')
                elif c == "'":
                    out.append("'")
                else:
                    out.append(s[i : i + 2])
                i += 2
            else:
                out.append(s[i])
                i += 1
        return "".join(out)

    def _literal_to_value(self, raw) -> Any:
        # If a token object slipped in (e.g. from AST), use its .value
        if raw is not None and hasattr(raw, "value") and not isinstance(raw, str):
            raw = getattr(raw, "value", raw)
        if raw == "yuh":
            return True
        if raw == "naur":
            return False
        if isinstance(raw, str):
            if raw.startswith('"') and raw.endswith('"'):
                # IMPORTANT: interpolate BEFORE unescaping so \@{x} stays literal.
                # Interpolation itself ignores escaped @ via regex negative lookbehind.
                inner_raw = raw[1:-1]
                interpolated = self._interpolate_string(inner_raw)
                return self._unescape_string(interpolated)
            if raw.startswith("'") and raw.endswith("'"):
                inner = raw[1:-1]
                return inner if inner != "" else None
        # number parsing
        if raw is None or (isinstance(raw, str) and raw == ""):
            return 0
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

        def _lookup_by_actual_name(name: str):
            # Scopes store keys by actual/lexeme name via _scope_key.
            for scope in reversed(self.scopes):
                if name in scope:
                    return scope[name]
            return None

        def _resolve_index_atom(atom: str) -> int:
            atom = atom.strip()
            if atom == "":
                raise InterpreterError("Empty array index in string interpolation")
            # int literal
            if re.fullmatch(r"-?\d+", atom):
                return int(atom)
            # identifier as index
            tok = reverse_ids.get(atom)
            if tok:
                v = self._lookup(tok)
                return int(v)
            # Fallback for variables that may not map cleanly in identifier_map
            v = _lookup_by_actual_name(atom)
            if v is None:
                raise InterpreterError(f"Undefined variable '{atom}' in string interpolation")
            return int(v)

        def repl(match: re.Match) -> str:
            inner = match.group(1).strip()
            if not inner:
                return match.group(0)

            # struct member forms:
            #   name.member
            #   name[i].member
            m_struct = re.fullmatch(
                r"([A-Za-z][A-Za-z0-9_]*)\s*(\[(.*?)\])?\s*\.\s*([A-Za-z][A-Za-z0-9_]*)\s*",
                inner,
            )
            if m_struct:
                base_name = m_struct.group(1)
                idx_raw = m_struct.group(3)
                member_name = m_struct.group(4)
                base_tok = reverse_ids.get(base_name)
                if base_tok:
                    base_val = self._lookup(base_tok)
                else:
                    base_val = _lookup_by_actual_name(base_name)
                    if base_val is None:
                        return match.group(0)
                target = base_val
                if idx_raw is not None:
                    if not isinstance(base_val, list):
                        return match.group(0)
                    idx = _resolve_index_atom(idx_raw)
                    if idx < 0 or idx >= len(base_val):
                        raise InterpreterError("Array out of bounds")
                    target = base_val[idx]
                if not isinstance(target, dict):
                    return match.group(0)

                # Struct runtime objects are keyed by actual member name via _scope_key.
                member_key = member_name
                if member_key not in target:
                    return match.group(0)
                v = target[member_key]
                return self._to_oxc_text(v)

            # Support name, name[i], name[i][j] (no function calls in v3)
            m = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)\s*(\[(.*?)\])?\s*(\[(.*?)\])?\s*", inner)
            if not m:
                return match.group(0)
            name = m.group(1)
            i1 = m.group(3)
            i2 = m.group(5)

            token_type = reverse_ids.get(name)
            if token_type:
                val = self._lookup(token_type)
            else:
                val = _lookup_by_actual_name(name)
                if val is None:
                    return match.group(0)

            if i1 is None and i2 is None:
                return self._to_oxc_text(val)

            # Must be array if indexed
            if not isinstance(val, list):
                return match.group(0)

            idx1 = _resolve_index_atom(i1)
            if idx1 < 0 or idx1 >= len(val):
                raise InterpreterError("Array out of bounds")

            if i2 is None:
                v = val[idx1]
                return self._to_oxc_text(v)

            row = val[idx1]
            if not isinstance(row, list):
                raise InterpreterError("Array out of bounds")
            idx2 = _resolve_index_atom(i2)
            if idx2 < 0 or idx2 >= len(row):
                raise InterpreterError("Array out of bounds")
            v = row[idx2]
            return self._to_oxc_text(v)

        # Only interpolate when '@' is NOT escaped (i.e., not preceded by backslash).
        return re.sub(r"(?<!\\)@\{([^}]+)\}", repl, s)

    def _eval_dimension_indices(self, dimension_node) -> List[int]:
        """
        dimension -> row_size | empty
        row_size -> [ size ] col_size
        size -> arith_expr
        col_size -> [ pdim_size ] | empty
        """
        if not dimension_node or getattr(dimension_node, "type", None) == "dimension_empty":
            return []
        if not getattr(dimension_node, "children", None):
            return []
        row_size = dimension_node.children[0]
        if getattr(row_size, "type", None) != "row_size":
            return []
        indices: List[int] = []
        # row index
        if row_size.children and len(row_size.children) >= 1:
            size_node = row_size.children[0]
            if getattr(size_node, "type", None) == "size" and getattr(size_node, "children", None):
                v = self._eval_arith(size_node.children[0])
                indices.append(int(self._to_arith_value(v)))
        # col index (optional)
        if row_size.children and len(row_size.children) >= 2:
            col_size = row_size.children[1]
            if getattr(col_size, "type", None) == "col_size" and getattr(col_size, "children", None):
                pd = col_size.children[0]
                if getattr(pd, "type", None) == "pdim_size" and getattr(pd, "children", None):
                    v = self._eval_arith(pd.children[0])
                    indices.append(int(self._to_arith_value(v)))
        return indices

    def _build_array_value(self, data_type: str, row_size_node, init_array_node) -> Any:
        """
        Build a 1D or 2D Python list from an optional initializer list.
        If initializer is missing, create a list with declared size filled with defaults (when constant),
        else default to empty list for VLA.
        """
        # Determine if 2D by presence of col_size with pdim_size
        rows = None
        cols = None
        # row_size children: [size_node, col_size_node]
        if getattr(row_size_node, "children", None) and len(row_size_node.children) >= 1:
            size_node = row_size_node.children[0]
            if getattr(size_node, "type", None) == "size" and getattr(size_node, "children", None):
                try:
                    rows = int(self._to_arith_value(self._eval_arith(size_node.children[0])))
                except Exception:
                    rows = None
        if getattr(row_size_node, "children", None) and len(row_size_node.children) >= 2:
            col_size = row_size_node.children[1]
            if getattr(col_size, "type", None) == "col_size" and getattr(col_size, "children", None):
                pd = col_size.children[0]
                if getattr(pd, "type", None) == "pdim_size" and getattr(pd, "children", None):
                    try:
                        cols = int(self._to_arith_value(self._eval_arith(pd.children[0])))
                    except Exception:
                        cols = None

        def dv():
            return self._default_value(data_type)

        # No init: allocate if constant sizes exist; else empty (VLA)
        if not init_array_node or not getattr(init_array_node, "children", None):
            if rows is None:
                return []
            if cols is None:
                return [dv() for _ in range(rows)]
            return [[dv() for _ in range(cols)] for _ in range(rows)]

        # init_array_node: children = [operator '=', arr_element_node]
        arr_element = init_array_node.children[1] if len(init_array_node.children) > 1 else None
        if not arr_element:
            return []

        # Flatten initializer depending on 1D vs 2D
        if cols is None:
            flat: List[Any] = []
            self._collect_array_init_1d(arr_element, flat)
            coerced = [self._coerce_to(data_type, v) for v in flat]
            if rows is None:
                return coerced
            out = [dv() for _ in range(rows)]
            for i, v in enumerate(coerced[:rows]):
                out[i] = v
            return out

        rows_list: List[List[Any]] = []
        self._collect_array_init_2d(arr_element, rows_list)
        # Coerce and pad
        if rows is None:
            rows = len(rows_list)
        if cols is None:
            cols = max((len(r) for r in rows_list), default=0)
        out2 = [[dv() for _ in range(cols)] for _ in range(rows)]
        for r in range(min(rows, len(rows_list))):
            for c in range(min(cols, len(rows_list[r]))):
                out2[r][c] = self._coerce_to(data_type, rows_list[r][c])
        return out2

    def _collect_array_init_1d(self, node, out: List[Any]) -> None:
        """Collect 1D initializer elements from parser's arr_element tree."""
        if node is None:
            return
        t = getattr(node, "type", None)
        if t in ("value", "output_content"):
            out.append(self._literal_to_value(getattr(node, "value", None)))
            return
        if t == "identifier":
            out.append(self._eval_identifier(node))
            return
        if t == "function_call":
            out.append(self._eval_function_call(node))
            return
        if getattr(node, "children", None):
            for ch in node.children:
                if hasattr(ch, "type"):
                    self._collect_array_init_1d(ch, out)

    def _collect_array_init_2d(self, node, rows: List[List[Any]]) -> None:
        """Collect 2D initializer rows from parser's 2d_element tree."""
        if node is None:
            return
        t = getattr(node, "type", None)
        if t == "2d_element":
            # children: [1d_element, 2d_tail]
            row: List[Any] = []
            self._collect_array_init_1d(node.children[0], row)
            rows.append(row)
            # tail may contain more 1d_element rows
            self._collect_array_init_2d(node.children[1], rows)
            return
        if t == "2d_tail":
            # children: [1d_element, 2d_tail] or empty
            if getattr(node, "children", None) and len(node.children) >= 1:
                row: List[Any] = []
                self._collect_array_init_1d(node.children[0], row)
                rows.append(row)
                if len(node.children) > 1:
                    self._collect_array_init_2d(node.children[1], rows)
            return
        # arr_element wrapper
        if getattr(node, "children", None):
            for ch in node.children:
                if hasattr(ch, "type"):
                    self._collect_array_init_2d(ch, rows)

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
            if value is None or value == "":
                return 0
            if isinstance(value, str):
                # Spec: char -> int uses ASCII (single character).
                if len(value) == 1:
                    return ord(value)
                try:
                    return int(float(value))
                except (ValueError, TypeError):
                    raise InterpreterError(f"Cannot convert '{value}' to int")
            return int(value)
        if data_type == "float":
            if isinstance(value, bool):
                return 1.0 if value else 0.0
            if value is None:
                return 0.0
            if isinstance(value, str):
                # Spec: char -> float uses ASCII (single character), then cast to float.
                if len(value) == 1:
                    return float(ord(value))
                try:
                    return float(value)
                except (ValueError, TypeError):
                    raise InterpreterError(f"Cannot convert '{value}' to float")
            return float(value)
        if data_type == "bool":
            if isinstance(value, (int, float)):
                return False if value == 0 else True
            if value is None:
                return False
            if isinstance(value, str):
                if value == "" or value == "naur":
                    return False
                if value == "yuh":
                    return True
                return False if value == "" else True
            # Token or other object with .value (e.g. "naur"/"yuh" from AST)
            if hasattr(value, "value"):
                v = getattr(value, "value", None)
                if v == "naur":
                    return False
                if v == "yuh":
                    return True
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
        # Gust instance: declared type is the struct name token; value is a member dict.
        # Shallow-copy so `B = A~` does not alias the same object (OxC struct assignment semantics).
        sem = getattr(self, "semantic", None)
        if sem is not None and isinstance(value, dict):
            struct_def = sem.get_structure(data_type)
            if struct_def is not None:
                return dict(value)
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
        INT_MAX = 9999999999
        FLOAT_MAX = 9999999999.999999

        txt = user_text.rstrip("\r\n")
        if expected_type == "string":
            return txt
        if expected_type == "char":
            # For char input, accept any non-empty input and take the first
            # character. Multi-character entries (e.g. "abcde") become 'a'
            # and will be handled by diffuse in stream if no case matches.
            return txt[0] if txt else None
        if expected_type == "int":
            try:
                # Allow numeric strings like "3" or "3.0" but reject non-numeric.
                val = int(float(txt))
            except Exception:
                raise InterpreterError(f"Invalid int input: '{txt}'")
            if val > INT_MAX or val < -INT_MAX:
                raise InterpreterError(f"Integer input out of range: '{txt}' (max ±{INT_MAX})")
            return val
        if expected_type == "float":
            try:
                val = float(txt)
            except Exception:
                raise InterpreterError(f"Invalid float input: '{txt}'")
            if val > FLOAT_MAX or val < -FLOAT_MAX:
                raise InterpreterError(f"Float input out of range: '{txt}' (max ±{FLOAT_MAX})")
            return val
            
        if expected_type == "bool":
            if txt.lower() in ("yuh", "true", "1"):
                return True
            if txt.lower() in ("naur", "false", "0", ""):
                return False
            return True
        return txt


def coerce_switch_case_literal(semantic_analyzer: Any, literal_raw: Any, switch_dtype: str) -> Any:
    """
    Coerce a stream case literal to the switch variable's declared type (same rules as assignment).
    Shared by TAC generator; must match Interpreter._exec_switch_stat.
    """
    interp = Interpreter(semantic_analyzer, tokens=[])
    base = interp._literal_to_value(literal_raw)
    return interp._coerce_to(switch_dtype, base)
