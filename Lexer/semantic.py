import re


class SemanticError:
    def __init__(self, message, line=0, column=0):
        self.message = message
        self.line = line
        self.column = column
    
    def to_dict(self):
        return {
            'message': self.message,
            'line': self.line,
            'column': self.column
        }


class SemanticAnalyzer:
    """Performs semantic analysis on the AST based on OxC Lang specification."""
    
    NUMERIC_TYPES = {'int', 'float'}
    PRIMITIVE_TYPES = {'int', 'float', 'char', 'string', 'bool'}
    UNARY_TYPES = {'int', 'char'}
    ARITHMETIC_TYPES = {'int', 'float', 'char'}
    RELATIONAL_TYPES = {'int', 'float', 'char'}
    CONCAT_TYPES = {'string', 'char'}

    # Implicit conversions for declarations and assignments (source -> set of valid targets)
    # Per spec v11: int<->float, int/float<->char(ASCII), int/float<->bool, char->string,
    #               char->float, bool->float
    IMPLICIT_CONVERSIONS = {
        'int': {'float', 'bool', 'char'},
        'float': {'int', 'char', 'bool'},
        'char': {'string', 'int', 'float'},
        'bool': {'int', 'float'},
    }

    # Stricter conversions for function parameters — only int<->float
    PARAM_CONVERSIONS = {
        'int': {'float'},
        'float': {'int'},
    }

    BUILTIN_RETURN_TYPES = {
        'toRise': 'string',
        'toFall': 'string',
        'horizon': 'int',
        'sizeOf': 'int',
        'toInt': 'int',
        'toFloat': 'float',
        'toString': 'string',
        'toChar': 'char',
        'toBool': 'bool',
        'waft': 'float',
    }

    BUILTIN_NAMES = frozenset(BUILTIN_RETURN_TYPES.keys())

    def __init__(self, ast, tokens=None):
        self.ast = ast
        self.tokens = tokens or []
        self.errors = []
        self.warnings = []
        
        self.scopes = [{}]
        self.scope_stack = ['global']
        
        self.structures = {}
        
        self.current_function = None
        self.current_function_return_type = None
        self.in_loop = False
        self.in_switch = False
        self.in_atmosphere = False
        self.has_return_in_current_func = False
        self._in_struct_member_access = False  # True when visiting member id in id_access (e.g. Doe.studentName)

        self.token_map = self._build_token_map()
        self.identifier_map = self._build_identifier_map()

    # ====================== Token / Identifier Maps ======================

    def _build_token_map(self):
        token_map = {}
        keyword_positions = {}
        literal_positions = {}
        
        for token in self.tokens:
            if token.type.startswith('id'):
                token_map[token.value] = (token.line, token.column)
                token_map[token.type] = (token.line, token.column)
            elif token.type in (
                'gust', 'air', 'wind', 'stream', 'resist', 'flow', 'gasp',
                'cycle', 'if', 'elseif', 'else', 'inhale', 'exhale',
                'atmosphere', 'echo', 'do',
            ):
                if token.type not in keyword_positions:
                    keyword_positions[token.type] = []
                keyword_positions[token.type].append((token.line, token.column))
                if token.type not in token_map:
                    token_map[token.type] = (token.line, token.column)
            elif token.type in ('int_lit', 'float_lit', 'string_lit', 'char_lit'):
                if token.value not in literal_positions:
                    literal_positions[token.value] = []
                literal_positions[token.value].append((token.line, token.column))
                token_map[token.type] = (token.line, token.column)
        
        self.keyword_positions = keyword_positions
        self.literal_positions = literal_positions
        return token_map
    
    def _build_identifier_map(self):
        id_map = {}
        for token in self.tokens:
            if token.type.startswith('id'):
                id_map[token.type] = token.value
        return id_map
    
    def get_actual_name(self, identifier):
        if identifier in self.identifier_map:
            return self.identifier_map[identifier]
        return identifier

    # ====================== Symbol Table ======================

    def enter_scope(self, scope_name):
        self.scopes.append({})
        self.scope_stack.append(scope_name)
    
    def exit_scope(self):
        if len(self.scopes) > 1:
            self.scopes.pop()
            self.scope_stack.pop()
    
    def declare_symbol(self, name, symbol_type, data_type, **kwargs):
        current_scope = self.scopes[-1]
        if name in current_scope:
            return False
        current_scope[name] = {
            'name': name,
            'symbol_type': symbol_type,
            'data_type': data_type,
            'scope': self.scope_stack[-1],
            'is_constant': kwargs.get('is_constant', False),
            'is_array': kwargs.get('is_array', False),
            'array_dimensions': kwargs.get('array_dimensions', []),
            'is_function': kwargs.get('is_function', False),
            'params': kwargs.get('params', []),
            'return_type': kwargs.get('return_type', None),
            'is_structure': kwargs.get('is_structure', False),
            'struct_members': kwargs.get('struct_members', {}),
            'struct_type': kwargs.get('struct_type', None),
        }
        return True
    
    def declare_global(self, name, symbol_type, data_type, **kwargs):
        if name in self.scopes[0]:
            return False
        self.scopes[0][name] = {
            'name': name,
            'symbol_type': symbol_type,
            'data_type': data_type,
            'scope': 'global',
            'is_constant': kwargs.get('is_constant', False),
            'is_array': kwargs.get('is_array', False),
            'array_dimensions': kwargs.get('array_dimensions', []),
            'is_function': kwargs.get('is_function', False),
            'params': kwargs.get('params', []),
            'return_type': kwargs.get('return_type', None),
            'is_structure': kwargs.get('is_structure', False),
            'struct_members': kwargs.get('struct_members', {}),
            'struct_type': kwargs.get('struct_type', None),
        }
        return True
    
    def lookup(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None
    
    def is_global_scope(self):
        return len(self.scopes) == 1
    
    def define_structure(self, name, members):
        if name in self.structures:
            return False
        self.structures[name] = members
        return True
    
    def get_structure(self, name):
        return self.structures.get(name)

    # ====================== Error Helpers ======================

    def error(self, message, line=0, column=0):
        self.errors.append(SemanticError(message, line, column))
    
    def get_location(self, identifier):
        if identifier in self.token_map:
            return self.token_map[identifier]
        return (0, 0)
    
    def get_node_location(self, node):
        if node is None:
            return (0, 0)
        if hasattr(node, 'line') and hasattr(node, 'column'):
            return (node.line, node.column)
        if hasattr(node, 'type') and node.type == 'identifier' and hasattr(node, 'value'):
            return self.get_location(node.value)
        if hasattr(node, 'type') and node.type == 'value' and hasattr(node, 'value'):
            return self.get_literal_location(node.value)
        if hasattr(node, 'children') and node.children:
            for child in node.children:
                if hasattr(child, 'type'):
                    if child.type == 'identifier' and hasattr(child, 'value'):
                        return self.get_location(child.value)
                    if child.type == 'value' and hasattr(child, 'value'):
                        return self.get_literal_location(child.value)
                    loc = self.get_node_location(child)
                    if loc != (0, 0):
                        return loc
        return (0, 0)
    
    def get_literal_location(self, value):
        if hasattr(self, 'literal_positions') and value in self.literal_positions:
            positions = self.literal_positions[value]
            if positions:
                return positions[0]
        str_value = str(value)
        if hasattr(self, 'literal_positions') and str_value in self.literal_positions:
            positions = self.literal_positions[str_value]
            if positions:
                return positions[0]
        return (0, 0)
    
    def _find_value_location(self, node):
        if node is None:
            return (0, 0)
        if hasattr(node, 'type') and node.type == 'value' and hasattr(node, 'value'):
            return self.get_literal_location(node.value)
        if hasattr(node, 'type') and node.type == 'output_content' and hasattr(node, 'value'):
            return self.get_literal_location(node.value)
        if hasattr(node, 'type') and node.type == 'identifier' and hasattr(node, 'value'):
            return self.get_location(node.value)
        if hasattr(node, 'children') and node.children:
            for child in node.children:
                loc = self._find_value_location(child)
                if loc != (0, 0):
                    return loc
        return (0, 0)

    def _validate_string_interpolation(self, string_value, node_for_location=None):
        """Case 2: Validate that every @{identifier} in a string is declared in scope."""
        if not string_value or not isinstance(string_value, str) or '@{' not in string_value:
            return
        for id_str in re.findall(r'@\{([^}]+)\}', string_value):
            id_str = id_str.strip()
            if not id_str:
                continue
            # Resolve: symbol table may use token (id1); identifier_map gives token->value
            found = False
            for scope in reversed(self.scopes):
                for key in scope:
                    if self.get_actual_name(key) == id_str:
                        found = True
                        break
                if found:
                    break
            if not found:
                line, col = (0, 0)
                if node_for_location is not None:
                    line, col = self._find_value_location(node_for_location)
                if line == 0 and col == 0:
                    line, col = self.get_literal_location(string_value)
                self.error(
                    f"Undeclared identifier '{id_str}' in string interpolation",
                    line, col,
                )

    # ====================== Main Entry ======================

    def analyze(self):
        if self.ast is None:
            self.error("No AST to analyze")
            return self.errors
        try:
            self.visit(self.ast)
        except Exception as e:
            self.error(f"Semantic analysis error: {str(e)}")
        return self.errors + self.warnings
    
    def visit(self, node):
        if node is None:
            return None
        method_name = f'visit_{node.type}'
        visitor = getattr(self, method_name, self.generic_visit)
        return visitor(node)
    
    def generic_visit(self, node):
        if hasattr(node, 'children') and node.children:
            for child in node.children:
                if hasattr(child, 'type'):
                    self.visit(child)
        return None

    # ====================== Program Structure ======================

    def visit_program(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                if child.type == 'body':
                    self.in_atmosphere = True
                    self.enter_scope('atmosphere')
                    self.visit(child)
                    self.exit_scope()
                    self.in_atmosphere = False
                    # Allow empty main (atmosphere) body; no warning
                else:
                    self.visit(child)
    
    def visit_global_dec(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_global_dec_empty(self, node):
        pass
    
    def visit_sub_functions(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_sub_functions_empty(self, node):
        pass

    # ====================== Declarations ======================

    def visit_declaration(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)

    def visit_normal(self, node):
        if not node.children:
            return
        
        data_type = None
        if node.children[0].type == 'data_type':
            data_type = node.children[0].value
        
        identifier = None
        if len(node.children) > 1 and node.children[1].type == 'identifier':
            identifier = node.children[1].value
        
        if data_type and identifier:
            is_array = False
            dimensions = []
            
            if len(node.children) > 2:
                norm_dec = node.children[2]
                if norm_dec.type == 'norm_dec' and norm_dec.children:
                    first_child = norm_dec.children[0]
                    if first_child.type == 'row_size':
                        is_array = True
                        dimensions = self._get_array_dimensions(first_child)
                        self._validate_array_size(first_child, identifier)
                        if len(norm_dec.children) > 1:
                            array_node = norm_dec.children[1]
                            if array_node.type == 'array' and array_node.children:
                                self._validate_array_elements(array_node, data_type, identifier)
                    elif first_child.type == 'operator' and first_child.value == '=':
                        if len(norm_dec.children) > 1:
                            init_expr = norm_dec.children[1]
                            self.generic_visit(init_expr)  # Traverse so identifiers (e.g. y in int x = y~) are validated
                            init_type = self._get_expression_type(init_expr)
                            if init_type and not self._types_compatible(data_type, init_type):
                                line, col = self.get_location(identifier)
                                actual_name = self.get_actual_name(identifier)
                                self.error(
                                    f"Type mismatch: cannot assign '{init_type}' to "
                                    f"'{data_type}' variable '{actual_name}'",
                                    line, col,
                                )
            
            actual_name = self.get_actual_name(identifier)
            if not self.declare_symbol(identifier, 'variable', data_type,
                                       is_array=is_array, array_dimensions=dimensions):
                line, col = self.get_location(identifier)
                self.error(f"Variable '{actual_name}' is already declared in this scope", line, col)
            
            if len(node.children) > 3:
                self._process_norm_tail(node.children[3], data_type)

    def _process_norm_tail(self, node, data_type):
        if node.type == 'norm_tail_empty' or not node.children:
            return
        
        for i, child in enumerate(node.children):
            if child.type == 'identifier':
                identifier = child.value
                is_array = False
                dimensions = []
                
                if i + 1 < len(node.children):
                    norm_dec = node.children[i + 1]
                    if norm_dec.type == 'norm_dec' and norm_dec.children:
                        first_child = norm_dec.children[0]
                        if first_child.type == 'row_size':
                            is_array = True
                            dimensions = self._get_array_dimensions(first_child)
                            self._validate_array_size(first_child, identifier)
                            if len(norm_dec.children) > 1:
                                array_node = norm_dec.children[1]
                                if array_node.type == 'array' and array_node.children:
                                    self._validate_array_elements(array_node, data_type, identifier)
                        elif first_child.type == 'operator' and first_child.value == '=':
                            if len(norm_dec.children) > 1:
                                init_expr = norm_dec.children[1]
                                self.generic_visit(init_expr)  # Traverse so identifiers in initializer are validated
                                init_type = self._get_expression_type(init_expr)
                                if init_type and not self._types_compatible(data_type, init_type):
                                    line, col = self.get_location(identifier)
                                    actual_name = self.get_actual_name(identifier)
                                    self.error(
                                        f"Type mismatch: cannot assign '{init_type}' to "
                                        f"'{data_type}' variable '{actual_name}'",
                                        line, col,
                                    )
                
                actual_name = self.get_actual_name(identifier)
                if not self.declare_symbol(identifier, 'variable', data_type,
                                           is_array=is_array, array_dimensions=dimensions):
                    line, col = self.get_location(identifier)
                    self.error(f"Variable '{actual_name}' is already declared in this scope", line, col)
            
            elif child.type == 'norm_tail':
                self._process_norm_tail(child, data_type)

    # -------------------- Constants --------------------

    def visit_constant(self, node):
        if not node.children:
            return
        
        first_child = node.children[0]
        
        if first_child.type == 'struct_const':
            self._visit_struct_const(first_child)
            return
        
        data_type = None
        if first_child.type == 'data_type':
            data_type = first_child.value
        
        identifier = None
        if len(node.children) > 1 and node.children[1].type == 'identifier':
            identifier = node.children[1].value
        
        if data_type and identifier:
            actual_name = self.get_actual_name(identifier)
            
            # Constants must be initialized at declaration
            has_init = False
            if len(node.children) > 2:
                const_dec = node.children[2]
                if const_dec.type == 'const_dec' and const_dec.children:
                    for c in const_dec.children:
                        if c.type == 'operator' and c.value == '=':
                            has_init = True
                            break
                        if c.type == 'expr':
                            has_init = True
                            break
                        if c.type == 'row_size':
                            has_init = True
                            break
            
            if not has_init:
                line, col = self.get_location(identifier)
                self.error(f"Constant '{actual_name}' must be initialized at declaration", line, col)
            
            if not self.declare_symbol(identifier, 'constant', data_type, is_constant=True):
                line, col = self.get_location(identifier)
                self.error(f"Constant '{actual_name}' is already declared in this scope", line, col)
            
            if len(node.children) > 2:
                self._check_const_initialization(node.children[2], data_type, identifier, actual_name)

    def _visit_struct_const(self, node):
        """Handle wind gust constant: declare (global or local) with is_constant=True and validate full init."""
        if not node.children or len(node.children) < 4:
            return
        struct_type = getattr(node.children[0], 'value', None)
        var_name = getattr(node.children[1], 'value', None)
        const_1d_node = node.children[3]
        if not struct_type or not var_name:
            return
        struct_def = self.get_structure(struct_type)
        if not struct_def:
            struct_symbol = self.lookup(struct_type)
            if not struct_symbol or not struct_symbol.get('is_structure'):
                line, col = self.get_location(struct_type)
                actual_name = self.get_actual_name(struct_type)
                self.error(f"Undefined structure type '{actual_name}'", line, col)
        init_values = []
        self._collect_const_1d_values(const_1d_node, init_values)
        actual_var = self.get_actual_name(var_name)
        if struct_def and len(struct_def) > 0 and len(init_values) != len(struct_def):
            line, col = self.get_location(var_name)
            self.error(
                f"Constant structure '{actual_var}' must be fully initialized: "
                f"expects {len(struct_def)} value(s), got {len(init_values)}",
                line, col,
            )
        elif init_values:
            self._validate_struct_init_values(struct_type, init_values, var_name)
        if self.is_global_scope():
            if not self.declare_global(var_name, 'struct_instance', struct_type,
                                      is_constant=True, struct_type=struct_type):
                line, col = self.get_location(var_name)
                self.error(f"Constant '{actual_var}' is already declared in this scope", line, col)
        else:
            if not self.declare_symbol(var_name, 'struct_instance', struct_type,
                                       is_constant=True, struct_type=struct_type):
                line, col = self.get_location(var_name)
                self.error(f"Constant '{actual_var}' is already declared in this scope", line, col)
    
    def _check_const_initialization(self, node, expected_type, const_name, actual_name=None):
        if not node.children:
            return
        display_name = actual_name if actual_name else self.get_actual_name(const_name)
        for child in node.children:
            if child.type == 'expr':
                if not self._is_literal_only(child):
                    line, col = self.get_location(const_name)
                    self.error(
                        f"Constant '{display_name}' must be initialized with a standalone literal, "
                        f"not an expression",
                        line, col,
                    )
                init_type = self._get_expression_type(child)
                if init_type and not self._types_compatible(expected_type, init_type):
                    line, col = self.get_location(const_name)
                    self.error(
                        f"Type mismatch: cannot initialize constant '{display_name}' "
                        f"of type '{expected_type}' with '{init_type}'",
                        line, col,
                    )

    def _is_literal_only(self, node):
        """Check whether an expression node contains only a standalone literal value."""
        if node is None:
            return False
        literal_types = {
            'int_literal', 'float_literal', 'char_literal', 'string_literal',
            'bool_literal', 'value', 'literal',
        }
        if node.type in literal_types:
            return True
        if node.type == 'identifier':
            return False
        if node.type == 'operator':
            return False
        if node.type in ('arith_expr', 'arith_tail', 'term', 'term_tail',
                         'logic_expr', 'and_expr', 'or_tail', 'and_tail',
                         'rela_expr', 'rela_tail'):
            if node.children and len(node.children) == 1:
                return self._is_literal_only(node.children[0])
            if node.children and len(node.children) > 1:
                has_operator = any(
                    c.type == 'operator' or c.type in (
                        'arith_op1', 'arith_op2', 'rela_sym',
                        'or_tail', 'and_tail', 'rela_tail',
                        'arith_tail', 'term_tail',
                    )
                    for c in node.children
                    if c.children
                )
                if has_operator:
                    return False
                return all(self._is_literal_only(c) for c in node.children)
            return False
        if node.type in ('expr', 'primary', 'factor', 'term', 'output', 'output_concat'):
            if node.children and len(node.children) == 1:
                return self._is_literal_only(node.children[0])
            if node.children and len(node.children) > 1:
                return False
            return not node.children
        if node.type == 'function_call':
            return False
        if node.children:
            if len(node.children) == 1:
                return self._is_literal_only(node.children[0])
            return False
        return True

    # -------------------- Structures --------------------

    def visit_structure(self, node):
        if not node.children:
            return
        
        identifier = None
        if node.children[0].type == 'identifier':
            identifier = node.children[0].value
        
        if identifier and len(node.children) > 1:
            struct_tail = node.children[1]
            
            if struct_tail.type == 'struct_tail' and struct_tail.children:
                first_child = struct_tail.children[0]
                
                if first_child.type == 'data_type':
                    members = self._extract_struct_members(struct_tail)
                    if not members:
                        line, col = self.get_location(identifier)
                        actual_name = self.get_actual_name(identifier)
                        self.error(f"Structure '{actual_name}' must have at least one member", line, col)
                    has_nesting = False
                    for member_name, member_type in members.items():
                        if member_type in self.structures:
                            line, col = self.get_location(identifier)
                            actual_name = self.get_actual_name(identifier)
                            actual_member = self.get_actual_name(member_name)
                            self.error(
                                f"Nesting prohibited: structure '{actual_name}' cannot have member "
                                f"'{actual_member}' of type gust (gust cannot be defined inside another gust)",
                                line, col,
                            )
                            has_nesting = True
                            break
                    if has_nesting:
                        return
                    if not self.define_structure(identifier, members):
                        line, col = self.get_location(identifier)
                        actual_name = self.get_actual_name(identifier)
                        self.error(f"Structure '{actual_name}' is already defined", line, col)
                    self.declare_global(identifier, 'structure', 'gust',
                                        is_structure=True, struct_members=members)
                
                elif first_child.type == 'identifier':
                    struct_type = identifier
                    var_name = first_child.value
                    
                    struct_def = self.get_structure(struct_type)
                    if not struct_def:
                        struct_symbol = self.lookup(struct_type)
                        if not struct_symbol or not struct_symbol.get('is_structure'):
                            line, col = self.get_location(identifier)
                            actual_name = self.get_actual_name(struct_type)
                            self.error(f"Undefined structure type '{actual_name}'", line, col)
                    
                    if not self.declare_symbol(var_name, 'struct_instance', struct_type,
                                               struct_type=struct_type):
                        line, col = self.get_location(var_name)
                        actual_var_name = self.get_actual_name(var_name)
                        self.error(f"Variable '{actual_var_name}' is already declared in this scope", line, col)
                    
                    # Validate initializer values match struct member types
                    self._validate_struct_init(struct_tail, struct_type, var_name)
    
    def _validate_struct_init(self, struct_tail, struct_type, var_name):
        """Validate structure initialization values against member types."""
        init_values = []
        self._collect_struct_init_values(struct_tail, init_values)
        if init_values:
            self._validate_struct_init_values(struct_type, init_values, var_name)

    def _validate_struct_init_values(self, struct_type, init_values, var_name):
        """Validate a list of initializer value nodes against struct member types (full init + positional types)."""
        struct_def = self.get_structure(struct_type)
        if not struct_def:
            return
        member_types = list(struct_def.values())
        member_names = list(struct_def.keys())
        actual_var = self.get_actual_name(var_name)
        if len(init_values) != len(member_types):
            line, col = self.get_location(var_name)
            self.error(
                f"Structure '{actual_var}' initialization expects {len(member_types)} "
                f"value(s), got {len(init_values)}",
                line, col,
            )
            return
        for i, (val_node, expected_type) in enumerate(zip(init_values, member_types)):
            val_type = self._get_expression_type(val_node)
            if val_type and not self._types_compatible(expected_type, val_type):
                line, col = self._find_value_location(val_node)
                if line == 0 and col == 0:
                    line, col = self.get_location(var_name)
                member_name = self.get_actual_name(member_names[i]) if i < len(member_names) else f"member {i+1}"
                self.error(
                    f"Type mismatch in structure '{actual_var}' initialization: "
                    f"member '{member_name}' expects '{expected_type}', got '{val_type}'",
                    line, col,
                )
    
    def _collect_struct_init_values(self, node, values):
        if not hasattr(node, 'children') or not node.children:
            return
        for child in node.children:
            if hasattr(child, 'type'):
                if child.type in ('value', 'output_content'):
                    values.append(child)
                elif child.type == 'expr':
                    values.append(child)
                elif child.type == 'identifier':
                    pass
                else:
                    self._collect_struct_init_values(child, values)

    def _collect_const_1d_values(self, node, values):
        """Collect initializer expression nodes from const_1d (output + element_tail) for wind gust."""
        if not hasattr(node, 'children') or not node.children:
            return
        if node.type == 'const_1d':
            if node.children[0].type == 'output' and node.children[0].children:
                values.append(node.children[0].children[0])
            if len(node.children) > 1:
                self._collect_const_1d_values(node.children[1], values)
        elif node.type == 'element_tail' and node.children:
            if node.children[0].type == 'output' and node.children[0].children:
                values.append(node.children[0].children[0])
            if len(node.children) > 1:
                self._collect_const_1d_values(node.children[1], values)
    
    def _extract_struct_members(self, struct_tail):
        members = {}
        
        def process_node(n):
            if not hasattr(n, 'children') or not n.children:
                return
            i = 0
            while i < len(n.children):
                child = n.children[i]
                if child.type == 'data_type':
                    data_type = child.value
                    if i + 1 < len(n.children) and n.children[i + 1].type == 'identifier':
                        member_name = n.children[i + 1].value
                        members[member_name] = data_type
                        i += 2
                        continue
                elif hasattr(child, 'type'):
                    process_node(child)
                i += 1
        
        process_node(struct_tail)
        return members

    # ====================== Functions ======================

    def visit_air_func(self, node):
        if not node.children:
            return
        
        return_type = None
        func_name = None
        params = []
        
        for child in node.children:
            if child.type == 'return_type':
                if child.value:
                    return_type = child.value
                elif child.children and child.children[0].type == 'data_type':
                    return_type = child.children[0].value
            elif child.type == 'identifier':
                func_name = child.value
            elif child.type == 'params':
                params = self._extract_params(child)
        
        if func_name:
            if not self.declare_global(func_name, 'function', return_type or 'vacuum',
                                        is_function=True, params=params,
                                        return_type=return_type or 'vacuum'):
                line, col = self.get_location(func_name)
                actual_name = self.get_actual_name(func_name)
                self.error(f"Function '{actual_name}' is already declared", line, col)
            
            prev_func = self.current_function
            prev_ret = self.current_function_return_type
            prev_has_ret = self.has_return_in_current_func
            
            self.current_function = func_name
            self.current_function_return_type = return_type or 'vacuum'
            self.has_return_in_current_func = False
            self.enter_scope(func_name)
            
            for param_name, param_type, is_array, dimensions in params:
                self.declare_symbol(param_name, 'variable', param_type,
                                    is_array=is_array, array_dimensions=dimensions)
            
            for child in node.children:
                if child.type == 'body':
                    self.visit(child)
                elif child.type == 'return_stat':
                    self.visit(child)
            
            # Non-vacuum functions must have at least one return
            if self.current_function_return_type != 'vacuum' and not self.has_return_in_current_func:
                line, col = self.get_location(func_name)
                actual_name = self.get_actual_name(func_name)
                self.error(
                    f"Function '{actual_name}' with return type '{self.current_function_return_type}' "
                    f"must return a value using 'gasp'",
                    line, col,
                )
            
            self.exit_scope()
            self.current_function = prev_func
            self.current_function_return_type = prev_ret
            self.has_return_in_current_func = prev_has_ret
    
    def _extract_params(self, params_node):
        params = []
        if params_node.type == 'params_empty' or not params_node.children:
            return params
        
        def process_params(node):
            if not hasattr(node, 'children') or not node.children:
                return
            data_type = None
            param_name = None
            is_array = False
            dimensions = []
            
            i = 0
            while i < len(node.children):
                child = node.children[i]
                if child.type == 'data_type':
                    data_type = child.value
                elif child.type == 'identifier':
                    param_name = child.value
                elif child.type == 'params_dim':
                    if child.children:
                        is_array = True
                elif child.type == 'params_tail':
                    if data_type and param_name:
                        params.append((param_name, data_type, is_array, dimensions))
                        data_type = None
                        param_name = None
                        is_array = False
                        dimensions = []
                    process_params(child)
                i += 1
            
            if data_type and param_name:
                params.append((param_name, data_type, is_array, dimensions))
        
        process_params(params_node)
        return params
    
    def visit_return_stat(self, node):
        if node.type == 'return_stat_empty':
            self._handle_empty_return()
            return
        
        if not node.children:
            return
        
        # atmosphere() must not return a value
        if self.in_atmosphere and self.current_function is None:
            line, col = self.get_location('gasp')
            self.error("The 'atmosphere' function must not return any value", line, col)
            return
        
        self.has_return_in_current_func = True
        
        for child in node.children:
            if child.type == 'expr':
                return_type = self._get_expression_type(child)
                func_name = (
                    self.get_actual_name(self.current_function)
                    if self.current_function else 'atmosphere'
                )
                line, col = self.get_location('gasp')
                
                if self.current_function_return_type == 'vacuum':
                    self.error(
                        f"Function '{func_name}' has return type 'vacuum' but returns a value",
                        line, col,
                    )
                elif return_type and self.current_function_return_type:
                    if not self._types_compatible(self.current_function_return_type, return_type):
                        self.error(
                            f"Return type mismatch in function '{func_name}': "
                            f"expected '{self.current_function_return_type}', got '{return_type}'",
                            line, col,
                        )
    
    def visit_return_stat_empty(self, node):
        self._handle_empty_return()
    
    def _handle_empty_return(self):
        if self.current_function_return_type and self.current_function_return_type != 'vacuum':
            line, col = self.get_location('gasp')
            func_name = (
                self.get_actual_name(self.current_function)
                if self.current_function else 'atmosphere'
            )
            self.error(
                f"Function '{func_name}' should return a value of type "
                f"'{self.current_function_return_type}'",
                line, col,
            )

    # ====================== Statements ======================

    def visit_body(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_stmt_list(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_stmt_list_empty(self, node):
        pass
    
    def visit_statement(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_identifier_stat(self, node):
        if not node.children:
            return
        
        has_unary = False
        identifier = None
        
        for child in node.children:
            if child.type == 'unary_op':
                has_unary = True
            elif child.type == 'identifier':
                identifier = child.value
            elif child.type == 'id_stat_body':
                self._visit_id_stat_body(child, identifier)
            elif child.type == 'id_access':
                self.visit(child)
        
        if identifier:
            symbol = self.lookup(identifier)
            actual_name = self.get_actual_name(identifier)
            if not symbol:
                line, col = self.get_location(identifier)
                self.error(f"Undeclared identifier '{actual_name}'", line, col)
            elif has_unary:
                # Spec: unary ++/-- only for int and char
                if symbol['data_type'] not in self.UNARY_TYPES:
                    line, col = self.get_location(identifier)
                    self.error(
                        f"Cannot apply increment/decrement to '{actual_name}' "
                        f"of type '{symbol['data_type']}' (only 'int' and 'char' allowed)",
                        line, col,
                    )
                if symbol['is_constant']:
                    line, col = self.get_location(identifier)
                    self.error(f"Cannot modify constant '{actual_name}'", line, col)
    
    def _visit_id_stat_body(self, node, identifier):
        if not node.children:
            return
        
        actual_name = self.get_actual_name(identifier) if identifier else None
        
        # Check if this is struct member assignment (e.g. John.name = "John"~)
        # id_stat_body has children [id_access, id_stat_tail]; assignment is inside id_stat_tail
        id_access_for_member = None
        assignment_node = None
        for child in node.children:
            if child.type == 'id_access' and len(child.children) >= 2 and child.children[0] == '.':
                id_access_for_member = child
            elif child.type == 'assignment':
                assignment_node = child
            elif child.type == 'id_stat_tail' and child.children:
                first = child.children[0]
                if getattr(first, 'type', None) == 'assignment':
                    assignment_node = first
        
        if id_access_for_member is not None and assignment_node is not None and identifier:
            # Member assignment: validate member, constant, and RHS type
            member_id = id_access_for_member.children[1]
            member_id_value = member_id.value if hasattr(member_id, 'value') else None
            if member_id_value is not None:
                member_type = self._validate_struct_member_access(identifier, member_id_value)
                symbol = self.lookup(identifier)
                if symbol and symbol.get('is_constant'):
                    line, col = self.get_location(identifier)
                    self.error(
                        f"Content of constant gust cannot be modified",
                        line, col,
                    )
                elif member_type:
                    expr_node = None
                    for c in assignment_node.children:
                        if hasattr(c, 'type') and c.type == 'expr':
                            expr_node = c
                            break
                    if expr_node:
                        expr_type = self._get_expression_type(expr_node)
                        if expr_type and not self._types_compatible(member_type, expr_type):
                            line, col = self.get_location(identifier)
                            self.error(
                                f"Type mismatch: cannot assign '{expr_type}' to member '{self.get_actual_name(member_id_value)}' of type '{member_type}'",
                                line, col,
                            )
            return
        
        for child in node.children:
            if child.type == 'param_opts':
                self._check_function_call(identifier, child)
            elif child.type == 'assignment':
                self._check_assignment(child, identifier, actual_name)
            elif child.type in ('id_stat_tail', 'identifier_stat'):
                self._visit_id_stat_tail(child, identifier)
            elif child.type == 'id_access':
                self._visit_id_access_for_assignment(child, identifier)
    
    def _visit_id_access_for_assignment(self, node, identifier):
        """Handle id_access which might be struct member access or array index."""
        for child in node.children:
            if hasattr(child, 'type'):
                if child.type == 'identifier':
                    # Struct member access: id.member
                    self._validate_struct_member_access(identifier, child.value)
                self.visit(child)
    
    def _validate_struct_member_access(self, struct_id, member_id):
        """Validate that a struct member exists and return its type."""
        if struct_id is None:
            return None
        
        symbol = self.lookup(struct_id)
        if not symbol:
            return None
        
        struct_type = symbol.get('struct_type')
        if not struct_type:
            return None
        
        struct_def = self.get_structure(struct_type)
        if not struct_def:
            return None
        
        if member_id not in struct_def:
            line, col = self.get_location(member_id)
            actual_struct = self.get_actual_name(struct_id)
            actual_member = self.get_actual_name(member_id)
            self.error(
                f"'{actual_member}' is not a member of structure '{actual_struct}'",
                line, col,
            )
            return None
        
        return struct_def[member_id]
    
    def _visit_id_stat_tail(self, node, identifier):
        symbol = self.lookup(identifier) if identifier else None
        actual_name = self.get_actual_name(identifier) if identifier else None
        
        for child in node.children:
            if child.type == 'unary_op':
                if symbol:
                    if symbol['data_type'] not in self.UNARY_TYPES:
                        line, col = self.get_location(identifier)
                        self.error(
                            f"Cannot apply increment/decrement to '{actual_name}' "
                            f"of type '{symbol['data_type']}' (only 'int' and 'char' allowed)",
                            line, col,
                        )
                    if symbol['is_constant']:
                        line, col = self.get_location(identifier)
                        self.error(f"Cannot modify constant '{actual_name}'", line, col)
            elif child.type == 'assignment':
                self._check_assignment(child, identifier, actual_name)
    
    def _check_assignment(self, assignment_node, identifier, actual_name=None):
        symbol = self.lookup(identifier) if identifier else None
        if actual_name is None and identifier:
            actual_name = self.get_actual_name(identifier)
        
        if not symbol:
            return
        
        # Cannot assign to constants
        if symbol['is_constant']:
            line, col = self.get_location(identifier)
            self.error(f"Cannot assign to constant '{actual_name}'", line, col)
        
        if not assignment_node.children:
            return
        
        # Detect compound assignment operator
        compound_op = None
        expr_node = None
        for c in assignment_node.children:
            if c.type == 'assi_op' and hasattr(c, 'value'):
                compound_op = c.value
            elif c.type == 'operator' and hasattr(c, 'value'):
                compound_op = c.value
            elif c.type == 'expr':
                expr_node = c
        
        if compound_op is None:
            for c in assignment_node.children:
                if isinstance(c, str) and c in ('+=', '-=', '*=', '/=', '%=', '='):
                    compound_op = c
                    break
        
        # For compound assignments (+=, -=, *=, /=, %=), variable must support arithmetic
        if compound_op in ('+=', '-=', '*=', '/=', '%='):
            if symbol['data_type'] not in self.ARITHMETIC_TYPES:
                line, col = self.get_location(identifier)
                self.error(
                    f"Compound assignment '{compound_op}' cannot be applied to "
                    f"'{actual_name}' of type '{symbol['data_type']}'",
                    line, col,
                )
            if compound_op == '%=' and symbol['data_type'] != 'int':
                line, col = self.get_location(identifier)
                self.error(
                    f"Modulus assignment '%%=' requires integer type, "
                    f"'{actual_name}' is '{symbol['data_type']}'",
                    line, col,
                )
        
        # Type compatibility for the expression being assigned
        if expr_node is None:
            for c in assignment_node.children:
                if hasattr(c, 'type') and c.type == 'expr':
                    expr_node = c
                    break
        
        if expr_node:
            expr_type = self._get_expression_type(expr_node)
            if expr_type and not self._types_compatible(symbol['data_type'], expr_type):
                line, col = self.get_location(identifier)
                self.error(
                    f"Type mismatch: cannot assign '{expr_type}' to "
                    f"'{symbol['data_type']}' variable '{actual_name}'",
                    line, col,
                )

    # -------------------- Function Calls --------------------

    def _check_function_call(self, func_name, param_opts_node):
        if not func_name:
            return
        
        actual_name = self.get_actual_name(func_name)
        
        # atmosphere() cannot be called from subfunctions
        if actual_name == 'atmosphere' or func_name == 'atmosphere':
            line, col = self.get_location(func_name)
            self.error("'atmosphere' (main) function cannot be called", line, col)
            return
        
        symbol = self.lookup(func_name)
        
        if not symbol:
            line, col = self.get_location(func_name)
            self.error(f"Undeclared function '{actual_name}'", line, col)
            return
        
        if not symbol['is_function']:
            line, col = self.get_location(func_name)
            self.error(f"'{actual_name}' is not a function", line, col)
            return
        
        arg_types = self._get_argument_types(param_opts_node)
        expected_params = symbol['params']
        
        if len(arg_types) != len(expected_params):
            line, col = self.get_location(func_name)
            self.error(
                f"Function '{actual_name}' expects {len(expected_params)} "
                f"argument(s), got {len(arg_types)}",
                line, col,
            )
            return
        
        # Use strict parameter conversion rules (only int<->float)
        for i, (arg_type, (param_name, param_type, is_array, dims)) in enumerate(
            zip(arg_types, expected_params)
        ):
            if arg_type and not self._types_compatible_for_params(param_type, arg_type):
                line, col = self.get_location(func_name)
                self.error(
                    f"Argument {i+1} of function '{actual_name}': "
                    f"expected '{param_type}', got '{arg_type}'",
                    line, col,
                )
    
    def _get_argument_types(self, param_opts_node):
        types = []
        if param_opts_node.type == 'param_opts_empty':
            return types
        
        def process_params(node):
            if not hasattr(node, 'children') or not node.children:
                return
            for child in node.children:
                if child.type == 'param_item':
                    for c in child.children:
                        if c.type == 'expr':
                            types.append(self._get_expression_type(c))
                elif child.type in ('param_list', 'param_tail'):
                    process_params(child)
        
        process_params(param_opts_node)
        return types

    # ====================== Input / Output ======================

    def visit_input_output(self, node):
        if not node.children:
            return
        
        io_type = node.children[0] if isinstance(node.children[0], str) else None
        
        if io_type == 'inhale':
            for child in node.children[1:]:
                if hasattr(child, 'type') and child.type == 'identifier':
                    identifier = child.value
                    actual_name = self.get_actual_name(identifier)
                    symbol = self.lookup(identifier)
                    if not symbol:
                        line, col = self.get_location(identifier)
                        self.error(f"Undeclared identifier '{actual_name}' in inhale statement", line, col)
                    elif symbol['is_constant']:
                        line, col = self.get_location(identifier)
                        self.error(f"Cannot read into constant '{actual_name}'", line, col)
        
        elif io_type == 'exhale':
            for child in node.children[1:]:
                if hasattr(child, 'type'):
                    if child.type == 'output':
                        self._validate_exhale_output(child)
                    self.visit(child)

    # ====================== Control Flow ======================

    def visit_conditioner(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_if_stat(self, node):
        for child in node.children:
            if child.type == 'cond_stat':
                cond_type = self._get_expression_type(child)
                if cond_type and cond_type not in ('bool', 'int', 'float', 'char'):
                    line, col = self.get_node_location(child)
                    self.error(
                        f"Condition in 'if' must evaluate to a boolean-compatible type, got '{cond_type}'",
                        line, col,
                    )
            elif child.type == 'stmt_ctrl':
                self.visit(child)
            elif child.type == 'if_tail':
                self.visit(child)
    
    def visit_if_tail(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_if_tail_empty(self, node):
        pass
    
    def visit_switch_stat(self, node):
        if not node.children:
            return
        
        switch_var = None
        switch_type = None
        old_in_switch = self.in_switch
        self.in_switch = True
        
        for child in node.children:
            if child.type == 'identifier':
                switch_var = child.value
                symbol = self.lookup(switch_var)
                if not symbol:
                    line, col = self.get_location(switch_var)
                    actual_name = self.get_actual_name(switch_var)
                    self.error(f"Undeclared identifier '{actual_name}' in stream statement", line, col)
                else:
                    switch_type = symbol['data_type']
                    # stream expression must be int or char
                    if switch_type not in ('int', 'char'):
                        line, col = self.get_location(switch_var)
                        actual_name = self.get_actual_name(switch_var)
                        self.error(
                            f"Stream (switch) variable '{actual_name}' must be 'int' or 'char' type, "
                            f"got '{switch_type}'",
                            line, col,
                        )
            elif child.type == 'switch_cases':
                self._check_switch_cases(child, switch_type, switch_var)
            elif child.type == 'switch_def':
                self.visit(child)
        
        self.in_switch = old_in_switch
    
    def _check_switch_cases(self, node, switch_type, switch_var=None):
        if node.type == 'switch_cases_empty':
            return
        
        for child in node.children:
            if child.type == 'switch_opts':
                pass
            elif child.type == 'stmt_list':
                self.visit(child)
            elif child.type == 'switch_cases':
                self._check_switch_cases(child, switch_type, switch_var)
            elif hasattr(child, 'type'):
                self.visit(child)
    
    def visit_switch_def(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_switch_def_empty(self, node):
        pass

    # ====================== Loops ======================

    def visit_iteration(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_while_loop(self, node):
        old_in_loop = self.in_loop
        self.in_loop = True
        
        for child in node.children:
            if child.type == 'cond_stat':
                self._get_expression_type(child)
            elif child.type == 'stmt_ctrl':
                self.visit(child)
            elif hasattr(child, 'type'):
                self.visit(child)
        
        self.in_loop = old_in_loop
    
    def visit_for_init(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_stmt_ctrl(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_stmt_ctrl_empty(self, node):
        pass
    
    def visit_ctrl_flow(self, node):
        """resist/flow — valid inside loops; resist also valid inside stream (switch)."""
        ctrl_word = node.value
        if ctrl_word == 'resist':
            if not self.in_loop and not self.in_switch:
                line, col = self.get_location('resist')
                self.error("'resist' (break) must be inside a loop or stream (switch)", line, col)
        elif ctrl_word == 'flow':
            if not self.in_loop:
                line, col = self.get_location('flow')
                self.error("'flow' (continue) must be inside a loop", line, col)

    # ====================== Expressions & Type Checking ======================

    def visit_expr(self, node):
        return self._get_expression_type(node)
    
    def visit_cond_stat(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_output(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)

    def _validate_exhale_output(self, output_node):
        """Per spec v11: expressions (arithmetic, relational, logical) are not allowed as exhale output.
        Only identifiers, function calls, literals, and string/char concatenation are permitted.
        Whole gusts cannot be displayed; must access a member (e.g. Doe.studentName)."""
        if not output_node or not output_node.children:
            return
        self._check_output_no_expression(output_node)
        # Resolve type so struct member access is validated (e.g. Doe.studendfGrade → error if not a member)
        expr_type = self._get_expression_type(output_node)
        # Output semantics: exhale cannot display a whole gust; must access a member
        if expr_type and self.get_structure(expr_type) is not None:
            line, col = self.get_node_location(output_node)
            if line == 0 and col == 0:
                line, col = self.get_location('exhale')
            self.error(
                "Must access member; whole gusts cannot be displayed",
                line, col,
            )

    def _check_output_no_expression(self, node):
        """Recursively check that an output node does not contain arithmetic, relational, or logical expressions."""
        if node is None:
            return
        forbidden_expr_types = {
            'arith_op1', 'arith_op2', 'rela_sym',
        }
        if node.type in forbidden_expr_types:
            line, col = (0, 0)
            if hasattr(node, 'value') and node.value:
                line, col = self.get_location(node.value)
            self.error(
                f"Expressions are not allowed as output in exhale; "
                f"only identifiers, function calls, literals, and concatenation are permitted",
                line, col,
            )
            return
        if node.type == 'arith_tail' and node.children:
            line, col = self.get_location('exhale')
            self.error(
                "Arithmetic expressions are not allowed as output in exhale",
                line, col,
            )
            return
        if node.type == 'rela_tail' and node.children:
            line, col = self.get_location('exhale')
            self.error(
                "Relational expressions are not allowed as output in exhale",
                line, col,
            )
            return
        if node.type in ('or_tail', 'and_tail') and node.children:
            line, col = self.get_location('exhale')
            self.error(
                "Logical expressions are not allowed as output in exhale",
                line, col,
            )
            return
        if hasattr(node, 'children') and node.children:
            for child in node.children:
                if hasattr(child, 'type'):
                    self._check_output_no_expression(child)
    
    def visit_id_access(self, node):
        """Handle id_access (e.g. .member or [index]). Set flag so member name is not reported as undeclared."""
        if not node.children:
            return
        # id_access → . id (struct member access)
        if len(node.children) >= 2 and node.children[0] == '.':
            self._in_struct_member_access = True
            try:
                for child in node.children:
                    if hasattr(child, 'type'):
                        self.visit(child)
            finally:
                self._in_struct_member_access = False
            return
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)

    def visit_identifier(self, node):
        if node.value:
            symbol = self.lookup(node.value)
            if not symbol:
                # Do not error if this identifier is a struct member (e.g. studentName in Doe.studentName)
                if not self._in_struct_member_access:
                    line, col = self.get_location(node.value)
                    actual_name = self.get_actual_name(node.value)
                    self.error(f"Undeclared identifier '{actual_name}'", line, col)
        if hasattr(node, 'children') and node.children:
            for child in node.children:
                if hasattr(child, 'type'):
                    self.visit(child)

    # -------------------- Expression Type Resolution --------------------

    def _get_expression_type(self, node):
        if node is None:
            return None
        
        # Leaf: literal value
        if node.type == 'value':
            if node.value and isinstance(node.value, str) and '"' in node.value:
                self._validate_string_interpolation(node.value, node)
            return self._get_value_type(node.value)
        
        # Identifier: standalone or with id_tail (e.g. Doe.studentName)
        if node.type == 'identifier':
            if node.value and (not hasattr(node, 'children') or not node.children):
                symbol = self.lookup(node.value)
                if symbol:
                    return symbol['data_type']
                return None
            # identifier → id id_tail with id_tail → param_opts (call) or id_access . member
            if hasattr(node, 'children') and node.children and len(node.children) >= 2:
                first = node.children[0]
                id_tail = node.children[1]
                if first.type == 'identifier' and hasattr(first, 'value') and id_tail.type == 'id_tail':
                    if id_tail.children and id_tail.children[0].type == 'param_opts':
                        # Case 1: Function call in expression (e.g. string x = hello()~)
                        self._check_function_call(first.value, id_tail.children[0])
                        symbol = self.lookup(first.value)
                        if symbol and symbol.get('is_function'):
                            return symbol.get('return_type') or 'vacuum'
                        return None
                    base_type = self._get_expression_type(first)
                    if not base_type:
                        return None
                    # Check id_tail for id_access [ '.', member_id ]
                    if id_tail.children:
                        id_access = id_tail.children[0]
                        if id_access.type == 'id_access' and len(id_access.children) >= 2 and id_access.children[0] == '.':
                            member_id_node = id_access.children[1]
                            if hasattr(member_id_node, 'value'):
                                member_type = self._validate_struct_member_access(first.value, member_id_node.value)
                                if member_type is not None:
                                    return member_type
                    return base_type
            return None
        
        # String/char literal in output context
        if node.type == 'output_content':
            value = node.value
            if value and isinstance(value, str) and '"' in value:
                self._validate_string_interpolation(value, node)
            if value and len(value) >= 2:
                if value[0] == '"':
                    return 'string'
                if value[0] == "'":
                    return 'char'
            return 'string'
        
        # Predefined / built-in function call
        if node.type == 'function_call':
            return self._resolve_function_call_type(node)
        
        # Concatenation with & yields string
        if node.type == 'output_tail' and node.children:
            return 'string'
        
        # Arithmetic binary expression: arith_tail contains operator + operand
        if node.type == 'arith_tail' and node.children and len(node.children) >= 2:
            op_node = node.children[0]
            right = node.children[1] if len(node.children) > 1 else None
            right_type = self._get_expression_type(right)
            op = op_node.value if hasattr(op_node, 'value') else None
            
            # Modulus requires integers
            if op == '%' and right_type and right_type != 'int':
                line, col = self._find_value_location(right)
                self.error(f"Modulus operator '%%' requires integer operands, got '{right_type}'", line, col)
            
            if right_type == 'float' or right_type == 'int':
                return right_type
            return right_type
        
        if node.type == 'term_tail' and node.children and len(node.children) >= 2:
            op_node = node.children[0]
            right = node.children[1] if len(node.children) > 1 else None
            right_type = self._get_expression_type(right)
            op = op_node.value if hasattr(op_node, 'value') else None
            
            if op == '%' and right_type and right_type != 'int':
                line, col = self._find_value_location(right)
                self.error(f"Modulus operator '%%' requires integer operands, got '{right_type}'", line, col)
            
            return right_type
        
        # Relational / logical tails always produce bool
        if node.type in ('rela_tail', 'and_tail', 'or_tail'):
            if node.children:
                return 'bool'
        
        # Wrapper nodes — delegate to first child
        if node.type in (
            'expr', 'logic_expr', 'and_expr', 'rela_expr', 'arith_expr',
            'term', 'factor', 'primary', 'size', 'pdim_size', 'row_size',
            'literal', 'cond_stat', 'output',
        ):
            if node.children:
                child_type = self._get_expression_type(node.children[0])
                # For arith_expr/term: check if there's a tail that promotes to float
                if node.type == 'arith_expr' and len(node.children) > 1:
                    return self._resolve_binary_type(child_type, node.children[1])
                if node.type == 'term' and len(node.children) > 1:
                    return self._resolve_binary_type(child_type, node.children[1])
                if node.type == 'rela_expr' and len(node.children) > 1:
                    tail = node.children[1]
                    if tail.type == 'rela_tail' and tail.children:
                        return 'bool'
                if node.type == 'logic_expr' and len(node.children) > 1:
                    tail = node.children[1]
                    if tail.type == 'or_tail' and tail.children:
                        return 'bool'
                if node.type == 'and_expr' and len(node.children) > 1:
                    tail = node.children[1]
                    if tail.type == 'and_tail' and tail.children:
                        return 'bool'
                return child_type
        
        # Fallback: try children
        if hasattr(node, 'children') and node.children:
            for child in node.children:
                if hasattr(child, 'type'):
                    result = self._get_expression_type(child)
                    if result:
                        return result
        
        return None
    
    def _resolve_binary_type(self, left_type, tail_node):
        """Determine result type when a left type combines with an arith_tail/term_tail."""
        if tail_node is None or not hasattr(tail_node, 'children') or not tail_node.children:
            return left_type
        
        if tail_node.type in ('arith_tail_empty', 'term_tail_empty'):
            return left_type
        
        op_node = tail_node.children[0] if tail_node.children else None
        right_node = tail_node.children[1] if len(tail_node.children) > 1 else None
        right_type = self._get_expression_type(right_node) if right_node else None
        
        op = op_node.value if op_node and hasattr(op_node, 'value') else None
        
        # Validate arithmetic operand types
        if left_type and left_type not in self.ARITHMETIC_TYPES:
            line, col = self._find_value_location(tail_node)
            self.error(f"Cannot perform arithmetic on type '{left_type}'", line, col)
        if right_type and right_type not in self.ARITHMETIC_TYPES:
            line, col = self._find_value_location(right_node)
            self.error(f"Cannot perform arithmetic on type '{right_type}'", line, col)
        
        # Modulus is integer-only
        if op == '%':
            if left_type and left_type != 'int':
                line, col = self._find_value_location(tail_node)
                self.error(f"Modulus operator '%%' requires integer operands, got '{left_type}'", line, col)
            if right_type and right_type != 'int':
                line, col = self._find_value_location(right_node)
                self.error(f"Modulus operator '%%' requires integer operands, got '{right_type}'", line, col)
            return 'int'
        
        # If either operand is float, result is float
        if left_type == 'float' or right_type == 'float':
            result = 'float'
        elif left_type == 'char' and right_type == 'char':
            result = 'int'
        elif left_type == 'char' or right_type == 'char':
            result = 'int'
        else:
            result = left_type or right_type
        
        # Check for further chained tail
        if len(tail_node.children) > 2:
            return self._resolve_binary_type(result, tail_node.children[2])
        
        return result
    
    def _resolve_function_call_type(self, node):
        """Resolve type for predefined (built-in) function calls and validate parameters."""
        if not node.children:
            return None
        
        func_name = None
        for child in node.children:
            if isinstance(child, str):
                func_name = child
                break
        
        if func_name and func_name in self.BUILTIN_RETURN_TYPES:
            self._validate_predefined_function(func_name, node)
            return self.BUILTIN_RETURN_TYPES[func_name]
        
        return None
    
    def _validate_predefined_function(self, func_name, node):
        """Validate parameter types for predefined functions per spec."""
        param_items = []
        self._collect_param_items(node, param_items)
        
        if func_name in ('toRise', 'toFall'):
            # Accepts strings, chars, string vars, char vars
            if param_items:
                ptype = self._get_expression_type(param_items[0])
                if ptype and ptype not in ('string', 'char'):
                    line, col = self._find_value_location(param_items[0])
                    self.error(
                        f"'{func_name}' expects a string or char argument, got '{ptype}'",
                        line, col,
                    )
        
        elif func_name == 'horizon':
            # Accepts int, float, string
            if param_items:
                ptype = self._get_expression_type(param_items[0])
                if ptype and ptype not in ('int', 'float', 'string'):
                    line, col = self._find_value_location(param_items[0])
                    self.error(
                        f"'horizon' expects an int, float, or string argument, got '{ptype}'",
                        line, col,
                    )
        
        elif func_name == 'sizeOf':
            # Accepts arrays and structs
            pass  # Hard to validate statically without runtime info; skip deep check
        
        elif func_name == 'toInt':
            if param_items:
                ptype = self._get_expression_type(param_items[0])
                if ptype and ptype != 'string':
                    line, col = self._find_value_location(param_items[0])
                    self.error(f"'toInt' expects a string argument, got '{ptype}'", line, col)
        
        elif func_name == 'toFloat':
            if param_items:
                ptype = self._get_expression_type(param_items[0])
                if ptype and ptype != 'string':
                    line, col = self._find_value_location(param_items[0])
                    self.error(f"'toFloat' expects a string argument, got '{ptype}'", line, col)
        
        elif func_name == 'toString':
            pass  # Accepts any type
        
        elif func_name == 'toChar':
            if param_items:
                ptype = self._get_expression_type(param_items[0])
                if ptype and ptype not in ('int', 'string'):
                    line, col = self._find_value_location(param_items[0])
                    self.error(f"'toChar' expects an int or string argument, got '{ptype}'", line, col)
        
        elif func_name == 'toBool':
            pass
        
        elif func_name == 'waft':
            if len(param_items) >= 1:
                p1type = self._get_expression_type(param_items[0])
                if p1type and p1type not in ('float', 'int'):
                    line, col = self._find_value_location(param_items[0])
                    self.error(f"'waft' first argument must be float or int, got '{p1type}'", line, col)
            if len(param_items) >= 2:
                p2type = self._get_expression_type(param_items[1])
                if p2type and p2type != 'int':
                    line, col = self._find_value_location(param_items[1])
                    self.error(f"'waft' second argument must be int, got '{p2type}'", line, col)
            if len(param_items) != 2:
                line, col = self.get_node_location(node)
                self.error(f"'waft' expects exactly 2 arguments, got {len(param_items)}", line, col)
    
    def _collect_param_items(self, node, items):
        if not hasattr(node, 'children') or not node.children:
            return
        for child in node.children:
            if hasattr(child, 'type'):
                if child.type == 'param_item':
                    items.append(child)
                elif child.type == 'expr' and node.type == 'param_item':
                    items.append(child)
                else:
                    self._collect_param_items(child, items)
    
    def _get_value_type(self, value):
        if value in ('yuh', 'naur'):
            return 'bool'
        try:
            if '.' in str(value):
                return 'float'
            int(value)
            return 'int'
        except (ValueError, TypeError):
            return None

    # -------------------- Type Compatibility --------------------

    def _types_compatible(self, target_type, source_type):
        """Check if source_type can be assigned to target_type (declaration/assignment)."""
        if target_type == source_type:
            return True
        if source_type in self.IMPLICIT_CONVERSIONS:
            if target_type in self.IMPLICIT_CONVERSIONS[source_type]:
                return True
        return False
    
    def _types_compatible_for_params(self, param_type, arg_type):
        """Stricter check for function parameter passing — only int<->float allowed."""
        if param_type == arg_type:
            return True
        if arg_type in self.PARAM_CONVERSIONS:
            if param_type in self.PARAM_CONVERSIONS[arg_type]:
                return True
        return False

    # ====================== Arrays ======================

    def _get_array_dimensions(self, row_size_node):
        dimensions = []
        
        def process_node(node):
            if not hasattr(node, 'children'):
                return
            for child in node.children:
                if child.type == 'size' and child.children:
                    dimensions.append('sized')
                elif child.type == 'size_empty':
                    dimensions.append('unsized')
                elif child.type == 'col_size':
                    process_node(child)
        
        process_node(row_size_node)
        return dimensions if dimensions else ['unsized']
    
    def _validate_array_size(self, row_size_node, identifier=None):
        if not row_size_node.children:
            return
        
        for child in row_size_node.children:
            if child.type == 'size' and child.children:
                size_type = self._get_expression_type(child)
                if size_type and size_type != 'int':
                    line, col = self._find_value_location(child)
                    if line == 0 and col == 0 and identifier:
                        line, col = self.get_location(identifier)
                    actual_name = self.get_actual_name(identifier) if identifier else 'array'
                    self.error(
                        f"Array size for '{actual_name}' must be an integer, got '{size_type}'",
                        line, col,
                    )
            elif child.type == 'col_size' and child.children:
                for col_child in child.children:
                    if col_child.type == 'pdim_size' and col_child.children:
                        col_size_type = self._get_expression_type(col_child)
                        if col_size_type and col_size_type != 'int':
                            line, col = self._find_value_location(col_child)
                            if line == 0 and col == 0 and identifier:
                                line, col = self.get_location(identifier)
                            actual_name = self.get_actual_name(identifier) if identifier else 'array'
                            self.error(
                                f"Array column size for '{actual_name}' must be an integer, got '{col_size_type}'",
                                line, col,
                            )
    
    def _validate_array_elements(self, array_node, expected_type, identifier=None):
        if not array_node.children:
            return
        
        actual_name = self.get_actual_name(identifier) if identifier else 'array'
        element_index = [0]
        
        def check_element(n):
            if n is None:
                return
            
            if n.type == 'output_content':
                elem_type = self._get_expression_type(n)
                if elem_type and not self._is_array_element_compatible(expected_type, elem_type):
                    line, col = self._find_value_location(n)
                    if line == 0 and col == 0:
                        if hasattr(n, 'value') and n.value:
                            line, col = self.get_literal_location(n.value)
                    if line == 0 and col == 0 and identifier:
                        line, col = self.get_location(identifier)
                    self.error(
                        f"Array element type mismatch in '{actual_name}': "
                        f"expected '{expected_type}', got '{elem_type}'",
                        line, col,
                    )
                element_index[0] += 1
                return
            
            if n.type == 'value':
                elem_type = self._get_expression_type(n)
                if elem_type and not self._is_array_element_compatible(expected_type, elem_type):
                    line, col = self._find_value_location(n)
                    if line == 0 and col == 0 and identifier:
                        line, col = self.get_location(identifier)
                    self.error(
                        f"Array element type mismatch in '{actual_name}': "
                        f"expected '{expected_type}', got '{elem_type}'",
                        line, col,
                    )
                element_index[0] += 1
                return
            
            if n.type == 'identifier':
                if hasattr(n, 'value') and n.value:
                    symbol = self.lookup(n.value)
                    if symbol:
                        elem_type = symbol['data_type']
                        if not self._is_array_element_compatible(expected_type, elem_type):
                            line, col = self.get_location(n.value)
                            var_name = self.get_actual_name(n.value)
                            self.error(
                                f"Array element type mismatch in '{actual_name}': "
                                f"expected '{expected_type}', got '{elem_type}' from variable '{var_name}'",
                                line, col,
                            )
                    element_index[0] += 1
                    return
                if hasattr(n, 'children') and n.children:
                    for c in n.children:
                        if hasattr(c, 'type'):
                            check_element(c)
                return
            
            if n.type == 'literal':
                for c in n.children:
                    if hasattr(c, 'type'):
                        check_element(c)
                return
            
            if n.type == 'output':
                for c in n.children:
                    if hasattr(c, 'type'):
                        check_element(c)
                return
            
            if hasattr(n, 'children') and n.children:
                for c in n.children:
                    if hasattr(c, 'type'):
                        check_element(c)
        
        for child in array_node.children:
            if hasattr(child, 'type') and child.type == 'arr_element':
                check_element(child)
            elif hasattr(child, 'type') and child.type != 'operator':
                check_element(child)
    
    def _is_array_element_compatible(self, array_type, element_type):
        """
        Check element compatibility with the array's declared type.
        Uses the same implicit conversion rules as assignment.
        """
        if array_type == element_type:
            return True
        if array_type == 'int':
            return element_type in ('int', 'bool', 'char', 'float')
        if array_type == 'float':
            return element_type in ('float', 'int')
        if array_type == 'char':
            return element_type in ('char', 'int')
        if array_type == 'string':
            return element_type in ('string', 'char')
        if array_type == 'bool':
            return element_type in ('bool', 'int')
        return False
    
    def _validate_array_index(self, row_size_node):
        if not row_size_node.children:
            return
        for child in row_size_node.children:
            if child.type == 'size' and child.children:
                index_type = self._get_expression_type(child)
                if index_type and index_type != 'int':
                    line, col = self._find_value_location(child)
                    self.error(f"Array index must be an integer, got '{index_type}'", line, col)
            elif child.type == 'col_size' and child.children:
                for col_child in child.children:
                    if col_child.type == 'pdim_size' and col_child.children:
                        col_index_type = self._get_expression_type(col_child)
                        if col_index_type and col_index_type != 'int':
                            line, col = self._find_value_location(col_child)
                            self.error(f"Array index must be an integer, got '{col_index_type}'", line, col)

    # ====================== Remaining Visitor Stubs ======================

    def visit_data_type(self, node):
        return node.value
    
    def visit_return_type(self, node):
        if node.value:
            return node.value
        if node.children:
            return self.visit(node.children[0])
        return 'vacuum'
    
    def visit_norm_dec(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_norm_dec_empty(self, node):
        pass
    
    def visit_norm_tail(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_norm_tail_empty(self, node):
        pass
    
    def visit_array(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_array_empty(self, node):
        pass
    
    def visit_id_tail(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                self.visit(child)
    
    def visit_dimension(self, node):
        for child in node.children:
            if hasattr(child, 'type'):
                if child.type == 'row_size':
                    self._validate_array_index(child)
                self.visit(child)
    
    def visit_dimension_empty(self, node):
        pass


def analyze(ast, tokens=None):
    """Convenience function to run semantic analysis."""
    analyzer = SemanticAnalyzer(ast, tokens)
    errors = analyzer.analyze()
    return errors
