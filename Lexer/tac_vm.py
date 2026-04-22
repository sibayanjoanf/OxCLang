from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from interpreter import Interpreter, InterpreterError, InputRequest

from tac import TACInstr


class TACExecutionError(InterpreterError):
    pass


class TACVM:
    """
    A minimal TAC "backend" executor.

    Design goal for this first iteration:
    - Execute the generated TAC to produce the same visible output as your
      existing AST interpreter for the subset used in your test programs.
    - Reuse your Interpreter runtime helpers for:
      - type coercion on assignment
      - output evaluation (string interpolation) for exhale
      - input conversion for inhale
    """

    def __init__(self, semantic_analyzer: Any, tokens: Optional[list] = None, ast_root: Optional[Any] = None):
        self.semantic = semantic_analyzer
        self.tokens = tokens or []
        self.ast_root = ast_root
        
        # Reuse existing runtime semantics for expression fragments we don't
        # re-implement here (notably output strings).
        self.runtime = Interpreter(semantic_analyzer, tokens=self.tokens)
        
        # Public fields consumed by your Flask UI/backend (app.py).
        self.output: List[str] = self.runtime.output
        self.waiting_for_input: bool = False
        self.input_request: Optional[InputRequest] = None
        self._target_identifier: Optional[str] = None
        self._waiting_from_call: bool = False
        self._pending_call_dst: Any = None

        # Execution state
        self._tac: List[TACInstr] = []
        self._label_to_pc: Dict[str, int] = {}
        self._pc: int = 0
        self._temps: Dict[str, Any] = {}

    def run(self, ast: Any) -> None:
        """
        This VM expects the TAC to be already loaded via load_tac() / run_tac().
        Kept for compatibility; prefer run_tac().
        """
        raise NotImplementedError("Use TACVM.run_tac(tac_code) for this iteration")

    def run_tac(self, tac_code: List[TACInstr]) -> None:
        self._tac = list(tac_code)
        self._build_labels()
        self._pc = 0
        self._temps = {}
        self.waiting_for_input = False
        self.input_request = None
        self._target_identifier = None
        self._waiting_from_call = False
        self._pending_call_dst = None
        self.runtime.waiting_for_input = False
        self.runtime.input_request = None
        self.runtime.output = []
        self.output = self.runtime.output

        # Index user-defined functions so runtime._call_user_function works.
        if self.ast_root is not None:
            self.runtime._index_functions(self.ast_root)
            # Initialize global declarations before TAC execution so universal
            # variables/constants are available to CALLed functions.
            if getattr(self.ast_root, "children", None) and len(self.ast_root.children) > 0:
                global_dec_node = self.ast_root.children[0]
                if getattr(global_dec_node, "type", None) == "global_dec":
                    self.runtime._exec(global_dec_node)

        self._execute_until_pause_or_end()

    def provide_input(self, user_text: str) -> None:
        if not self.waiting_for_input or not self.input_request or not self._target_identifier:
            return

        if self._waiting_from_call:
            # Input pause originated inside runtime user-function CALL.
            self.runtime.provide_input(user_text)
            self.waiting_for_input = self.runtime.waiting_for_input
            self.input_request = self.runtime.input_request
            self._target_identifier = (
                self.runtime.input_request.target_identifier if self.runtime.input_request else None
            )
            if self.waiting_for_input:
                return
            # Function call finished; commit pending CALL destination now.
            value = self.runtime.consume_pending_call_result()
            dst = self._pending_call_dst
            if self._is_temp(dst):
                self._set_temp(dst, value)
            else:
                self._set_var(dst, value)
            self._waiting_from_call = False
            self._pending_call_dst = None
            self._target_identifier = None
            self._execute_until_pause_or_end()
            return

        vid = self._target_identifier
        expected_type = self.runtime._lookup_declared_type(vid)
        value = self.runtime._convert_input(user_text, expected_type)

        # Echo raw input (matches interpreter: transcript line before store)
        self.runtime.emit(str(user_text) + "\n")

        dim = self.input_request.dimension_node if self.input_request else None
        id_access = self.input_request.id_access_node if self.input_request else None
        self.runtime.assign_input_target(vid, value, id_access_node=id_access, dimension_node=dim)

        self.waiting_for_input = False
        self.input_request = None
        self._target_identifier = None

        self._execute_until_pause_or_end()

    # ---------------- Internal ----------------

    def _build_labels(self) -> None:
        self._label_to_pc = {}
        for i, instr in enumerate(self._tac):
            if instr.op == "LABEL":
                self._label_to_pc[instr.result] = i

    def _is_temp(self, x: Any) -> bool:
        return isinstance(x, str) and x.startswith("t")

    def _normalize_literal(self, x: Any) -> Any:
        # Your interpreter already supports these, but for numeric-only temps
        # we normalize here to keep binop evaluation consistent.
        if x == "yuh":
            return True
        if x == "naur":
            return False
        if isinstance(x, str):
            # numeric strings
            if x.startswith('"') or x.startswith("'"):
                return x
            try:
                if "." in x:
                    return float(x)
                return int(x)
            except Exception:
                return x
        return x

    def _get_value(self, operand: Any) -> Any:
        if operand is None:
            return None
        if self._is_temp(operand):
            return self._temps.get(operand)
        if isinstance(operand, str) and operand.startswith("id"):
            return self.runtime._lookup(operand)
        # Lexer token text for char_lit / string_lit (e.g. "'B'" vs runtime "B" from INDEX_LOAD).
        # Must use Interpreter._literal_to_value or stream cases never match.
        if isinstance(operand, str) and len(operand) >= 2:
            if operand[0] == "'" and operand[-1] == "'":
                return self.runtime._literal_to_value(operand)
            if operand[0] == '"' and operand[-1] == '"':
                return self.runtime._literal_to_value(operand)
        return self._normalize_literal(operand)

    def _set_var(self, vid: str, value: Any) -> None:
        # Coerce based on declared type, then assign.
        dtype = self.runtime._lookup_declared_type(vid)
        coerced = self.runtime._coerce_to(dtype, value)
        self.runtime._assign(vid, coerced)

    def _set_temp(self, name: str, value: Any) -> None:
        self._temps[name] = value

    def _to_numeric_value(self, value: Any) -> Any:
        """
        Coerce runtime values for arithmetic/ordering operations.
        Mirrors OxC runtime semantics used by the interpreter:
        - bool -> 1/0
        - char (single-character string) -> ASCII via ord()
        - numeric strings -> int/float
        """
        if value is None:
            return 0
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            if len(value) == 1:
                return ord(value)
            try:
                if "." in value:
                    return float(value)
                return int(value)
            except Exception:
                raise TACExecutionError(
                    f"Operation requires numeric/char operands, got string: {value!r}"
                )
        raise TACExecutionError(
            f"Operation requires numeric/char operands, got {type(value).__name__}"
        )

    def _execute_until_pause_or_end(self) -> None:
        while self._pc < len(self._tac):
            if self.waiting_for_input:
                return

            instr = self._tac[self._pc]
            self._pc += 1

            op = instr.op
            if op == "LABEL":
                continue
            if op == "GOTO":
                self._pc = self._label_to_pc[instr.result]
                continue
            if op == "IF_TRUE_GOTO":
                cond_val = self._get_value(instr.arg1)
                if self.runtime._to_bool(cond_val):
                    self._pc = self._label_to_pc[instr.result]
                continue

            if op == "ASSIGN":
                dst = instr.result
                value = self._get_value(instr.arg1)
                if self._is_temp(dst):
                    self._set_temp(dst, value)
                else:
                    self._set_var(dst, value)
                continue

            if op == "INHALE":
                vid = instr.result
                id_access = instr.arg1  # AST 'id_access' or None
                dim = None
                if id_access is not None and getattr(id_access, "type", None) == "dimension":
                    # Backward compatibility if older TAC passes dimension directly.
                    dim = id_access
                    id_access = None
                elif id_access is not None and getattr(id_access, "children", None):
                    first = id_access.children[0]
                    if getattr(first, "type", None) == "dimension":
                        dim = first
                self.waiting_for_input = True
                self._target_identifier = vid
                self.input_request = InputRequest(
                    target_identifier=vid,
                    prompt="",
                    dimension_node=dim,
                    id_access_node=id_access,
                )
                return

            if op == "EXHALE":
                out_node = instr.arg1
                try:
                    text = self.runtime._eval_output(out_node)
                except Exception as e:
                    raise TACExecutionError(f"exhale output evaluation error: {str(e)}")
                self.runtime.emit("" if text is None else str(text))
                continue

            if op == "INCDEC":
                # arg1 is '++'/'--', result is identifier token type
                inc_op = instr.arg1
                vid = instr.result
                cur = self.runtime._lookup(vid)
                if inc_op == "++":
                    new_val = (cur if cur is not None else 0) + 1
                elif inc_op == "--":
                    new_val = (cur if cur is not None else 0) - 1
                else:
                    raise TACExecutionError(f"Unsupported INCDEC op: {inc_op}")
                self._set_var(vid, new_val)
                continue

            if op == "CALL":
                # CALL: arg1=function_id_token_type, arg2=param_opts node, result=temp/var
                func_id_token_type = instr.arg1
                param_opts_node = instr.arg2
                value = self.runtime._call_user_function(func_id_token_type, param_opts_node)
                dst = instr.result
                if self.runtime.waiting_for_input and self.runtime.input_request:
                    self.waiting_for_input = True
                    self.input_request = self.runtime.input_request
                    self._target_identifier = self.input_request.target_identifier
                    self._waiting_from_call = True
                    self._pending_call_dst = dst
                    return
                if self._is_temp(dst):
                    self._set_temp(dst, value)
                else:
                    self._set_var(dst, value)
                continue

            if op == "BUILTIN_CALL":
                # arg1 = function_call AST (toRise, waft, horizon, sizeOf, ...)
                value = self.runtime._eval_function_call(instr.arg1)
                dst = instr.result
                if self._is_temp(dst):
                    self._set_temp(dst, value)
                else:
                    self._set_var(dst, value)
                continue

            if op == "DECL_NORM":
                # arg1=data_type, arg2=norm_dec AST, result=identifier token type
                self.runtime._declare_one(instr.result, instr.arg1, instr.arg2)
                continue

            if op == "DECL_STRUCT":
                # arg1=struct_type token-id, result=var id
                struct_type = instr.arg1
                members = self.semantic.get_structure(struct_type) if self.semantic else {}
                obj: Dict[str, Any] = {}
                for m_name, m_type in (members or {}).items():
                    obj[self.runtime._scope_key(m_name)] = self.runtime._default_value(m_type)
                self.runtime._assign(instr.result, obj)
                continue

            if op == "DECL_STRUCT_INIT":
                # arg1=struct_type token-id, arg2=1d initializer AST, result=var id
                struct_type = instr.arg1
                init_1d_node = instr.arg2
                members = self.semantic.get_structure(struct_type) if self.semantic else {}
                obj: Dict[str, Any] = {}
                member_names = list((members or {}).keys())
                for m_name, m_type in (members or {}).items():
                    obj[self.runtime._scope_key(m_name)] = self.runtime._default_value(m_type)
                vals = self.runtime._collect_1d_init_values(init_1d_node)
                for i, raw in enumerate(vals):
                    if i >= len(member_names):
                        break
                    mk = member_names[i]
                    mt = members[mk]
                    obj[self.runtime._scope_key(mk)] = self.runtime._coerce_to(mt, raw)
                self.runtime._assign(instr.result, obj)
                continue

            if op == "DECL_STRUCT_ARRAY":
                # arg1=struct_type token-id, arg2=size AST, result=array var id
                struct_type = instr.arg1
                size_node = instr.arg2
                members = self.semantic.get_structure(struct_type) if self.semantic else {}
                size = self.runtime._eval_size_to_int(size_node)
                if size is None or size < 0:
                    size = 0
                arr: List[Dict[str, Any]] = []
                for _ in range(size):
                    obj: Dict[str, Any] = {}
                    for m_name, m_type in (members or {}).items():
                        obj[self.runtime._scope_key(m_name)] = self.runtime._default_value(m_type)
                    arr.append(obj)
                self.runtime._assign(instr.result, arr)
                continue

            if op == "DECL_STRUCT_ARRAY_INIT":
                # arg1=struct_type token-id, arg2=(size AST, 2d-init AST), result=array var id
                struct_type = instr.arg1
                size_node, init_2d_node = instr.arg2
                members = self.semantic.get_structure(struct_type) if self.semantic else {}
                member_names = list((members or {}).keys())
                size = self.runtime._eval_size_to_int(size_node)
                if size is None or size < 0:
                    size = 0
                arr: List[Dict[str, Any]] = []
                for _ in range(size):
                    obj: Dict[str, Any] = {}
                    for m_name, m_type in (members or {}).items():
                        obj[self.runtime._scope_key(m_name)] = self.runtime._default_value(m_type)
                    arr.append(obj)
                rows = self.runtime._collect_2d_init_rows(init_2d_node)
                for r, row in enumerate(rows):
                    if r >= len(arr):
                        break
                    for i, raw in enumerate(row):
                        if i >= len(member_names):
                            break
                        mk = member_names[i]
                        mt = members[mk]
                        arr[r][self.runtime._scope_key(mk)] = self.runtime._coerce_to(mt, raw)
                self.runtime._assign(instr.result, arr)
                continue

            if op == "ASSIGN_WITH_ACCESS":
                # arg1=vid, arg2=id_access node, result=assignment AST
                self.runtime._exec_assignment_with_access(instr.arg1, instr.arg2, instr.result)
                continue

            if op == "INDEX_LOAD":
                # arg1=vid, arg2=dimension AST, result=temp
                val = self.runtime.read_indexed_value(instr.arg1, instr.arg2)
                self._set_temp(instr.result, val)
                continue

            if op == "MEMBER_LOAD":
                # arg1=vid, arg2=id_access AST, result=temp
                val = self.runtime._read_identifier_with_access(instr.arg1, instr.arg2)
                self._set_temp(instr.result, val)
                continue

            if op == "STORE_INDEX":
                # arg1=vid, arg2=dimension AST, result=value operand (usually temp)
                val = self._get_value(instr.result)
                self.runtime.assign_indexed_value(instr.arg1, instr.arg2, val)
                continue

            if op == "UMINUS":
                value = self._get_value(instr.arg1)
                res = -self._to_numeric_value(value)
                if self._is_temp(instr.result):
                    self._set_temp(instr.result, res)
                else:
                    self._set_var(instr.result, res)
                continue

            if op == "LNOT":
                # Logical !: match Interpreter._eval_primary for !(logic_expr) -> not bool(inner)
                value = self._get_value(instr.arg1)
                res = not self.runtime._to_bool(value)
                if self._is_temp(instr.result):
                    self._set_temp(instr.result, res)
                else:
                    self._set_var(instr.result, res)
                continue

            # Binary operations: arithmetic + relational + logical
            # op is one of: + - * / % > < >= <= == != || &&
            if op in {"+", "-", "*", "/", "%", ">", "<", ">=", "<=", "==", "!=", "||", "&&"}:
                a = self._get_value(instr.arg1)
                b = self._get_value(instr.arg2)

                if op == "+":
                    # String concatenation path (lowered from OxC '&' in TAC).
                    # Keep arithmetic '+' behavior for pure numeric operands.
                    if instr.value_type == "string" or isinstance(a, str) or isinstance(b, str):
                        def _to_oxc_text(v: Any) -> str:
                            if v is None:
                                return ""
                            if isinstance(v, bool):
                                return "yuh" if v else "naur"
                            return str(v)

                        res = _to_oxc_text(a) + _to_oxc_text(b)
                    else:
                        a = self._to_numeric_value(a)
                        b = self._to_numeric_value(b)
                        res = a + b
                elif op == "-":
                    a = self._to_numeric_value(a)
                    b = self._to_numeric_value(b)
                    res = a - b
                elif op == "*":
                    a = self._to_numeric_value(a)
                    b = self._to_numeric_value(b)
                    res = a * b
                elif op == "/":
                    a = self._to_numeric_value(a)
                    b = self._to_numeric_value(b)
                    if b == 0:
                        raise TACExecutionError("Division by zero")
                    # Match interpreter: int/int -> integer division.
                    if isinstance(a, int) and isinstance(b, int):
                        res = a // b
                    else:
                        res = a / b
                elif op == "%":
                    a = self._to_numeric_value(a)
                    b = self._to_numeric_value(b)
                    if b == 0:
                        raise TACExecutionError("Modulo by zero")
                    if isinstance(b, float):
                        raise TACExecutionError("Modulo operator requires integer operands")
                    if isinstance(a, float):
                        raise TACExecutionError("Modulo operator requires integer operands")
                    res = a % b
                elif op == ">":
                    a = self._to_numeric_value(a)
                    b = self._to_numeric_value(b)
                    res = a > b
                elif op == "<":
                    a = self._to_numeric_value(a)
                    b = self._to_numeric_value(b)
                    res = a < b
                elif op == ">=":
                    a = self._to_numeric_value(a)
                    b = self._to_numeric_value(b)
                    res = a >= b
                elif op == "<=":
                    a = self._to_numeric_value(a)
                    b = self._to_numeric_value(b)
                    res = a <= b
                elif op == "==":
                    res = a == b
                elif op == "!=":
                    res = a != b
                elif op == "||":
                    res = self.runtime._to_bool(a) or self.runtime._to_bool(b)
                elif op == "&&":
                    res = self.runtime._to_bool(a) and self.runtime._to_bool(b)
                else:
                    raise TACExecutionError(f"Unhandled binary op: {op}")

                dst = instr.result
                if self._is_temp(dst):
                    self._set_temp(dst, res)
                else:
                    self._set_var(dst, res)
                continue

            raise TACExecutionError(f"Unsupported TAC instruction op: {op}")

