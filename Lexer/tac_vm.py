from __future__ import annotations

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
                self.runtime._declare_in_current_scope(instr.result, obj)
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
                self.runtime._declare_in_current_scope(instr.result, obj)
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
                self.runtime._declare_in_current_scope(instr.result, arr)
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
                self.runtime._declare_in_current_scope(instr.result, arr)
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


def _run_continuation_self_test() -> int:
    """
    Debug regression runner for TAC continuation behavior.
    Kept in tac_vm.py so TAC checks live in an existing TAC file.
    """
    from pathlib import Path

    from lexer import Lexer
    from parser import Parser
    from semantic import SemanticAnalyzer
    from tac import TACGenerator

    def _compile(source: str):
        lexer = Lexer(source)
        tokens = lexer.tokenize()
        valid_tokens = [t for t in tokens if not t.is_error]
        parser = Parser(valid_tokens)
        ast, syntax_errors = parser.parse()
        if syntax_errors:
            raise RuntimeError(f"syntax errors: {[e.message for e in syntax_errors]}")
        analyzer = SemanticAnalyzer(ast, valid_tokens)
        sem_errors = analyzer.analyze()
        if sem_errors:
            raise RuntimeError(f"semantic errors: {[e.message for e in sem_errors]}")
        return analyzer, valid_tokens, ast

    def _run_tac(source: str, inputs: List[str]):
        analyzer, valid_tokens, ast = _compile(source)
        vm = TACVM(analyzer, valid_tokens, ast_root=ast)
        tac_code = TACGenerator(ast, analyzer).generate()
        vm.run_tac(tac_code)
        input_i = 0
        while vm.waiting_for_input:
            if input_i >= len(inputs):
                raise RuntimeError("TAC runtime requested more input than provided")
            vm.provide_input(inputs[input_i])
            input_i += 1
        if input_i != len(inputs):
            raise RuntimeError("TAC runtime consumed fewer inputs than provided")
        return "".join(vm.output), vm.waiting_for_input

    def _load_test_program(filename: str) -> str:
        root = Path(__file__).resolve().parents[1]
        return (root / "Test Programs" / filename).read_text(encoding="utf-8")

    cases = [
        (
            "matrix_input_loops",
            _load_test_program("27 - Matrix Addition.oxc"),
            ["1", "2", "3", "4", "5", "10", "20", "30", "40", "50"],
            [
                "Input 5 elements for Array A:\nElement 1: 1\nElement 2: 2\nElement 3: 3\nElement 4: 4\nElement 5: 5\n",
                "Input 5 elements for Array B:\nElement 1: 10\nElement 2: 20\nElement 3: 30\nElement 4: 40\nElement 5: 50\n",
                "Sum in Array C: 11 22 33 44 55 ",
            ],
        ),
        (
            "menu_branch_after_inhale",
            _load_test_program("17. Areas - Function.oxc"),
            ["2", "5", "4"],
            ["Enter base: 5\n", "Enter height:4\n", "Area: 10.0"],
        ),
        (
            "function_inhale_resume",
            (
                "air int ask(){\n"
                "    int x~\n"
                "    inhale(x)~\n"
                "    gasp x~\n"
                "}\n"
                "atmosphere(){\n"
                "    int v~\n"
                "    v = ask()~\n"
                "    if (v > 10){\n"
                "        exhale(\"HIGH\")~\n"
                "    } else {\n"
                "        exhale(\"LOW\")~\n"
                "    }\n"
                "}\n"
            ),
            ["11"],
            ["11\nHIGH"],
        ),
        (
            "struct_search_flow",
            (
                "atmosphere(){\n"
                "    gust Pair { int key~ int value~ }~\n"
                "    gust Pair arr[3]~\n"
                "    arr[0].key = 1~ arr[0].value = 10~\n"
                "    arr[1].key = 2~ arr[1].value = 20~\n"
                "    arr[2].key = 3~ arr[2].value = 30~\n"
                "    int needle~\n"
                "    inhale(needle)~\n"
                "    int found = -1~\n"
                "    echo(int i = 0~ i < 3~ i = i + 1~){\n"
                "        if (arr[i].key == needle){\n"
                "            found = arr[i].value~\n"
                "            resist~\n"
                "        }\n"
                "    }\n"
                "    exhale(\"Found=@{found}\")~\n"
                "}\n"
            ),
            ["2"],
            ["2\nFound=20"],
        ),
        (
            "string_concat_assignment",
            (
                "atmosphere(){\n"
                "    string a = \"A\"~\n"
                "    a += \"B\"~\n"
                "    exhale(a)~\n"
                "}\n"
            ),
            [],
            ["AB"],
        ),
        (
            "menu_multi_function_resume",
            (
                "air vacuum a(){\n"
                "    int x~\n"
                "    exhale(\"A?\")~\n"
                "    inhale(x)~\n"
                "    exhale(\"A=@{x}\\n\")~\n"
                "}\n"
                "air vacuum b(){\n"
                "    int y~\n"
                "    exhale(\"B?\")~\n"
                "    inhale(y)~\n"
                "    exhale(\"B=@{y}\\n\")~\n"
                "}\n"
                "atmosphere(){\n"
                "    int c~\n"
                "    cycle(yuh){\n"
                "        exhale(\"[1]A [2]B [3]X: \")~\n"
                "        inhale(c)~\n"
                "        if (c == 1){\n"
                "            a()~\n"
                "        } elseif (c == 2){\n"
                "            b()~\n"
                "        } elseif (c == 3){\n"
                "            resist~\n"
                "        }\n"
                "    }\n"
                "}\n"
            ),
            ["1", "11", "2", "22", "3"],
            ["A?11\nA=11\n", "B?22\nB=22\n"],
        ),
        (
            "menu_function_validation_loop_then_more_input",
            (
                "air vacuum matrixMini(){\n"
                "    int n~\n"
                "    string s~\n"
                "    cycle(yuh){\n"
                "        exhale(\"N?\")~\n"
                "        inhale(s)~\n"
                "        if (toInt(s) > 0){\n"
                "            n = toInt(s)~\n"
                "            resist~\n"
                "        }\n"
                "    }\n"
                "    int a[n]~\n"
                "    exhale(\"A0?\")~\n"
                "    inhale(a[0])~\n"
                "    exhale(\"A0=@{a[0]}\\n\")~\n"
                "}\n"
                "atmosphere(){\n"
                "    int c~\n"
                "    cycle(yuh){\n"
                "        exhale(\"[1]M [2]X: \")~\n"
                "        inhale(c)~\n"
                "        if (c == 1){\n"
                "            matrixMini()~\n"
                "        } elseif (c == 2){\n"
                "            resist~\n"
                "        }\n"
                "    }\n"
                "}\n"
            ),
            ["1", "1", "9", "2"],
            ["N?1\n", "A0?9\nA0=9\n"],
        ),
    ]

    failures: List[str] = []
    for case_name, source, inputs, required_fragments in cases:
        try:
            out_a, waiting_a = _run_tac(source, inputs)
            out_b, waiting_b = _run_tac(source, inputs)
            if waiting_a or waiting_b:
                failures.append(f"{case_name}: runtime still waiting for input")
                continue
            if out_a != out_b:
                failures.append(f"{case_name}: non-deterministic output across repeated runs")
                continue
            for fragment in required_fragments:
                if fragment not in out_a:
                    failures.append(
                        f"{case_name}: missing expected fragment {fragment!r}\n"
                        f"  output={out_a!r}"
                    )
                    break
        except Exception as exc:
            failures.append(f"{case_name}: {exc}")

    if failures:
        print("FAIL: TAC continuation self-test")
        for item in failures:
            print(f"- {item}")
        return 1

    print("PASS: TAC continuation self-test")
    for case_name, _source, _inputs, _fragments in cases:
        print(f"- {case_name}")
    return 0


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv:
        raise SystemExit(_run_continuation_self_test())

