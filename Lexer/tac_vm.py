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

    def __init__(self, semantic_analyzer: Any, tokens: Optional[list] = None):
        self.semantic = semantic_analyzer
        self.tokens = tokens or []

        # Reuse existing runtime semantics for expression fragments we don't
        # re-implement here (notably output strings).
        self.runtime = Interpreter(semantic_analyzer, tokens=self.tokens)

        # Public fields consumed by your Flask UI/backend (app.py).
        self.output: List[str] = self.runtime.output
        self.waiting_for_input: bool = False
        self.input_request: Optional[InputRequest] = None
        self._target_identifier: Optional[str] = None

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
        self.runtime.waiting_for_input = False
        self.runtime.input_request = None
        self.runtime.output = []
        self.output = self.runtime.output

        self._execute_until_pause_or_end()

    def provide_input(self, user_text: str) -> None:
        if not self.waiting_for_input or not self.input_request or not self._target_identifier:
            return

        vid = self._target_identifier
        expected_type = self.runtime._lookup_declared_type(vid)
        value = self.runtime._convert_input(user_text, expected_type)

        self.runtime._assign(vid, value)

        # Echo raw input (matches your interpreter transcript behavior)
        self.runtime.emit(str(user_text) + "\n")

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
        return self._normalize_literal(operand)

    def _set_var(self, vid: str, value: Any) -> None:
        # Coerce based on declared type, then assign.
        dtype = self.runtime._lookup_declared_type(vid)
        coerced = self.runtime._coerce_to(dtype, value)
        self.runtime._assign(vid, coerced)

    def _set_temp(self, name: str, value: Any) -> None:
        self._temps[name] = value

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
                if bool(cond_val):
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
                self.waiting_for_input = True
                self._target_identifier = vid
                self.input_request = InputRequest(target_identifier=vid, prompt="")
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

            if op == "UMINUS":
                value = self._get_value(instr.arg1)
                res = -value
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
                    res = a + b
                elif op == "-":
                    res = a - b
                elif op == "*":
                    res = a * b
                elif op == "/":
                    if b == 0:
                        raise TACExecutionError("Division by zero")
                    # Match interpreter: int/int -> integer division.
                    if isinstance(a, int) and isinstance(b, int):
                        res = a // b
                    else:
                        res = a / b
                elif op == "%":
                    if b == 0:
                        raise TACExecutionError("Modulo by zero")
                    if isinstance(b, float):
                        raise TACExecutionError("Modulo operator requires integer operands")
                    if isinstance(a, float):
                        raise TACExecutionError("Modulo operator requires integer operands")
                    res = a % b
                elif op == ">":
                    res = a > b
                elif op == "<":
                    res = a < b
                elif op == ">=":
                    res = a >= b
                elif op == "<=":
                    res = a <= b
                elif op == "==":
                    res = a == b
                elif op == "!=":
                    res = a != b
                elif op == "||":
                    res = bool(a) or bool(b)
                elif op == "&&":
                    res = bool(a) and bool(b)
                else:
                    raise TACExecutionError(f"Unhandled binary op: {op}")

                dst = instr.result
                if self._is_temp(dst):
                    self._set_temp(dst, res)
                else:
                    self._set_var(dst, res)
                continue

            raise TACExecutionError(f"Unsupported TAC instruction op: {op}")

