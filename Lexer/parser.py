from lexer import Token

class ASTNode:
    def __init__(self, type, children=None, value=None):
        self.type = type
        if children is None:
            self.children = []
        elif isinstance(children, list):
            self.children = children
        else:
            self.children = [children]
        self.value = value
    
    def __repr__(self, level=0):
        indent = "  " * level
        result = f"{indent}ASTNode({self.type}"
        if self.value:
            result += f", value={self.value}"
        if self.children:
            result += ",\n"
            for child in self.children:
                if isinstance(child, ASTNode):
                    result += child.__repr__(level + 1) + "\n"
                else:
                    result += f"{indent}  {child}\n"
            result += indent
        result += ")"
        return result
    
    def to_dict(self):
        # Convert ASTNode to dictionary for JSON
        result = {'type': self.type}
        if self.value:
            result['value'] = self.value
        if self.children:
            result['children'] = [
                child.to_dict() if isinstance(child, ASTNode) else str(child)
                for child in self.children
            ]
        return result

class ParseError:
    def __init__(self, message, line, column):
        self.message = message
        self.line = line
        self.column = column
    
    def to_dict(self):
        return {
            'message': self.message,
            'line': self.line,
            'column': self.column
        }

class Parser:
    FIRST_PRIMARY = {'(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 
                     'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                     'int_lit', 'float_lit', 'yuh', 'naur', 'char_lit', 'string_lit', '!'}

    def __init__(self, tokens):
        self.tokens = tokens
        self.position = 0
        self.current_token = tokens[0] if tokens else None
        self.errors = []
        self.error_reported_at_position = -1
    
    def error(self, message):
        if self.position == self.error_reported_at_position:
            return
        
        if self.current_token:
            line = self.current_token.line
            column = self.current_token.column
        else:
            line = 0
            column = 0
        
        self.errors.append(ParseError(message, line, column))
        self.error_reported_at_position = self.position

    def peek(self):
        if self.current_token:
            return self.current_token.type
        return None
    
    def advance(self):
        self.position += 1
        if self.position < len(self.tokens):
            self.current_token = self.tokens[self.position]
        else:
            self.current_token = None
      
    def match(self, expected_type):
        if self.current_token is None:
            self.error(f"Expected '{expected_type}', but reached end of input")
            raise StopIteration
        
        # Check if current token type matches what we expect
        if self.current_token.type == expected_type:
            token = self.current_token
            self.advance()
            return token
        else:
            self.error(f"Expected '{expected_type}', got '{self.current_token.type}'")
            raise StopIteration
        
    def check_id(self):
        current = self.peek()

        if current and current.startswith('id'):
            # identifier_value = self.current_token.value
            identifier_type = self.current_token.type
            self.match(identifier_type) 
            return ASTNode('identifier', value=identifier_type)
        else:
            if self.current_token is None:
                self.error(f"Expected identifier, but reached end of input.")
            else:
                self.error(f"Expected 'identifier', got '{self.current_token.type}' ")
            raise StopIteration
        
    
    # Production 1: <program> → <global_dec> <sub_functions> atmosphere() { <body> }
    # PREDICT = {universal, air, atmosphere}
    
    def parse(self):
        try: 
            current = self.peek()
            
            if current in ['universal', 'air', 'atmosphere']:
                ast = self.parse_program()
                return ast, self.errors
            else:
                self.error(f"Program must start with 'universal', 'air', or 'atmosphere', got '{current}'")
                return None, self.errors
        
        except StopIteration:
            return None, self.errors
        except Exception as e:
            self.error(f"Errors: {str(e)}")
            return None, self.errors
    
    def parse_program(self):
        try: 
            global_dec_node = self.parse_global_dec()
            sub_functions_node = self.parse_sub_functions()
            
            self.match('atmosphere')
            self.match('(')
            self.match(')')
            self.match('{')
            body_node = self.parse_body()
            if self.peek() != '}':
                self.error(f"Expected '}}' to close atmosphere() function, got '{self.peek()}'")
            else:
                self.match('}')
            
            return ASTNode('program', children=[
                global_dec_node,
                sub_functions_node,
                body_node
            ])    
        except StopIteration:
            if self.current_token and self.current_token.type == '}':
                pass
            elif self.peek() != '}':
                self.error(f"Expected '}}' to close atmosphere() function")
            return None
        
    # <global_dec> 
    # Production 2: global_dec → universal <declaration> <global_dec>
    # PREDICT = {universal}
    
    # Production 3: global_dec → λ
    # PREDICT = {air, atmosphere}
    
    def parse_global_dec(self):
        current = self.peek()

        if current == 'universal':            
            self.match('universal')
            declaration_node = self.parse_declaration()
            global_dec_node = self.parse_global_dec()
            return ASTNode('global_dec', children=[declaration_node, global_dec_node])

        elif current in ['air', 'atmosphere']:
            return ASTNode('global_dec_empty')
        
        else:
            self.error(f"[2-3] Expected 'universal', 'air', or 'atmosphere', got '{current}'")
    
    # <declaration>
    # Production 4: declaration → <normal>
    # PREDICT = {int, float, char, string, bool}

    # Production 5: declaration → <structure>
    # PREDICT = {gust}

    # Production 6: declaration → wind <constant>
    # PREDICT = {wind}

    def parse_declaration(self):
        current = self.peek()
        
        if current in ['int', 'float', 'char', 'string', 'bool']:
            normal_node = self.parse_normal()
            return ASTNode('declaration', children=[normal_node])

        elif current == 'gust':
            structure_node = self.parse_structure()
            return ASTNode('declaration', children=[structure_node])
        
        elif current == 'wind':
            self.match('wind')
            constant_node = self.parse_constant()
            return ASTNode('declaration', children=[constant_node])
        
        else:
            self.error(f"[4-6] Expected data type (int, float, char, string, bool) or 'gust' or 'wind', got '{current}'")
    
    
    # <normal>
    # Production 7: normal → <data_type> id <norm_dec> <norm_tail>~
    # PREDICT = {int, float, char, string, bool}

    def parse_normal(self):
        current = self.peek()

        if current in ['int', 'float', 'char', 'string', 'bool']:
            data_type_node = self.parse_data_type()
            id_no = self.check_id()
            norm_dec_node = self.parse_norm_dec()
            norm_tail_node = self.parse_norm_tail()

            current_tok = self.peek()
            if current_tok != '~':
                if current_tok == '}':
                    self.error(f"Missing '~' terminator.")
                elif current_tok and current_tok not in [',', '~']:
                    self.error(f"Unexpected '{current_tok}' in declaration - expected ',' or '~'")
                else:
                    self.error(f"Expected '~' to end declaration, got '{current_tok}'")
                raise StopIteration

            self.match('~')        
            return ASTNode('normal', children=[data_type_node, id_no, norm_dec_node, norm_tail_node])
        else: 
            self.error(f"[7] Expected data type (int, float, char, string, bool) got '{current}'")
    
    # <norm_dec>
    # Production 8: norm_dec → <row_size> <array>
    # PREDICT = {[}

    # Production 9: norm_dec → = <expr>
    # PREDICT = {=}

    # Production 10: norm_dec → λ
    # PREDICT = {,,~}
 
    def parse_norm_dec(self):     
        current = self.peek()
        
        if current == '[':
            row_size_node = self.parse_row_size()
            array_node = self.parse_array()
            return ASTNode('norm_dec', children=[row_size_node, array_node])
        elif current == '=':
            self.match('=')

            next_tok = self.peek()
            if next_tok in [',', '~', None] or next_tok in ['atmosphere', 'air', 'universal']:
                self.error(f"Expected value or expression after '=', not '{next_tok}'")
                raise StopIteration
            
            expr_node = self.parse_expr()

            return ASTNode('norm_dec', children=[ASTNode('operator', value='='), expr_node])
        elif current in [',', '~']:
            
            return ASTNode('norm_dec_empty')
        else:
            self.error(f"[8-10] Expected '[' or '=' or ',' or terminator '~' got '{current}'")
    
    # <norm_tail>
    # Production 11: norm_tail → , id <norm_dec> <norm_tail>
    # PREDICT = {,}

    # Production 12: norm_tail → λ
    # PREDICT = {~}

    def parse_norm_tail(self):
        current = self.peek()
        
        if current == ',':
            self.match(',')
            id_no = self.check_id()
            norm_dec_node = self.parse_norm_dec()
            norm_tail_node = self.parse_norm_tail()
            return ASTNode('norm_tail', children=[id_no, norm_dec_node, norm_tail_node])
        elif current == '~':
            return ASTNode('norm_tail_empty')
        else:
            self.error(f"[11-12] Expected ',' or terminator '~' got '{current}'")

    # <array>
    # Production 13: array → = {<arr_element>}
    # PREDICT = {=}

    # Production 14: array → λ
    # PREDICT = {,,~}

    def parse_array(self):
        current = self.peek()
    
        if current == '=':
            self.match('=')
            self.match('{')
            arr_element_node = self.parse_arr_element()
            self.match('}')
            return ASTNode('array', children=[ASTNode('operator', value='='), arr_element_node])
        elif current in [',','~']:
            return ASTNode('array_empty')
        else:
            self.error(f"[13-14] Expected '=' or ',' or terminator '~' got '{current}'")

    # <arr_element>
    # Production 15: arr_element → <1d_element>
    # PREDICT = {++, --, id, toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, int_lit, float_lit, yuh, naur, char_lit, string_lit, }}

    # Production 16: arr_element → <2d_element>
    # PREDICT = {{}

    def parse_arr_element(self):
        current = self.peek()

        if current in ['++', '--','toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '}'] or (current and current.startswith('id')): 
            oned_element_node = self.parse_1d_element()
            return ASTNode('arr_element', children=[oned_element_node])
        elif current == '{':
            twod_element_node = self.parse_2d_element()
            return ASTNode('arr_element', children=[twod_element_node])
        else:
            self.error("[15-16] Expected a value literal or '}'"
                        f"got '{current}'"
                       )


    # <1d_element>
    # Production 17: 1d_element → <output> <element_tail>
    # PREDICT = {++, --, id, toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, int_lit, float_lit, yuh, naur, char_lit, string_lit}

    # Production 18: 1d_element → λ
    # PREDICT = {}}

    def parse_1d_element(self):
        current = self.peek()

        if current in ['++', '--','toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit'] or (current and current.startswith('id')): 
            output_node = self.parse_output()
            element_tail_node = self.parse_element_tail()
            return ASTNode('1d_element', children=[output_node, element_tail_node])
        elif current == '}':
            return ASTNode('1d_element_node_empty')
        else:
            self.error(f"[17-18] Expected a value literal, got '{current}")


    # <2d_element>
    # Production 19: 2d_element → {<1d_element>} <2d_tail>
    # PREDICT = {{}

    def parse_2d_element(self):
        current = self.peek()

        if current == '{':
            self.match('{')
            oned_element_node = self.parse_1d_element()
            self.match('}')
            twod_tail_node = self.parse_2d_tail()
            return ASTNode('2d_element', children=[oned_element_node, twod_tail_node])
        else:
            self.error("[19] Expected '{', "
                        f"got '{current}'"
                       )


    # <element_tail>
    # Production 20: element_tail → , <output> <element_tail>
    # PREDICT = {,}

    # Production 21: element_tail → λ
    # PREDICT = {}}

    def parse_element_tail(self):
        current = self.peek()
    
        if current == ',':
            self.match(',')
            output_node = self.parse_output()
            element_tail_node = self.parse_element_tail()
            return ASTNode('element_tail', children=[output_node, element_tail_node])
        elif current == '}':
            return ASTNode('element_tail_empty')
        else:
            self.error("[20-21] Expected ',' or '}' "
                        f"got '{current}'")


    # <2d_tail>
    # Production 22: 2d_tail → , {<1d_element>} <2d_tail>
    # PREDICT = {,}

    # Production 23: 2d_tail → λ
    # PREDICT = {}}

    def parse_2d_tail(self):
        current = self.peek()
    
        if current == ',':
            self.match(',')
            self.match('{')
            oned_element_node = self.parse_1d_element()
            self.match('}')
            twod_tail_node = self.parse_2d_tail()
            return ASTNode('2d_tail', children=[oned_element_node, twod_tail_node])
        elif current == '}':
            return ASTNode('2d_tail_empty')
        else:
            self.error("[22-23] Expected ',' or '}' "
                        f"got '{current}'")


    # <structure>
    # Production 24: structure → gust id <struct_tail>~
    # PREDICT = {gust}

    def parse_structure(self):
        current = self.peek()

        if current == 'gust':
            self.match('gust')
            id_no = self.check_id()
            struct_tail_node = self.parse_struct_tail()
            self.match('~')
            return ASTNode('structure', children=[id_no, struct_tail_node])
        else:
            self.error(f"[24] Expected 'gust', got '{current}'")

    # <struct_tail>
    # Production 25: struct_tail → { <data_type> id~ <gust_tail> }
    # PREDICT = {{}

    # Production 26: struct_tail → id <struct_tail2>
    # PREDICT = {id}

    def parse_struct_tail(self):
        current = self.peek()

        if current == '{':
            self.match('{')
            data_type_node = self.parse_data_type()
            id_no = self.check_id()
            self.match('~')
            gust_tail_node = self.parse_gust_tail()
            self.match('}')
            return ASTNode('struct_tail', children=[data_type_node, id_no, gust_tail_node])
        elif current and current.startswith('id'):
            id_no = self.check_id()
            struct_tail2_node = self.parse_struct_tail2()
            return ASTNode('struct_tail', children=[id_no, struct_tail2_node])
        else:
            self.error("[25-26] Expected '{' or identifier, "
                        f"got '{current}'")

    # <struct_tail2>
    # Production 27: struct_tail2 → = {<1d_element>}
    # PREDICT = {=}

    # Production 28: struct_tail2 → λ
    # PREDICT = {~}

    def parse_struct_tail2(self):
        current = self.peek()

        if current == '=':
            self.match('=')
            self.match('{')
            oned_element_node = self.parse_1d_element()
            self.match('}')
            return ASTNode('struct_tail2', children=[ASTNode('operator', value='='), oned_element_node])
        elif current == '~':
            return ASTNode('struct_tail2_empty')
        else:
            self.error(f"[27-28] Expected '=' or '~', got '{current}'")



    # <gust_tail>
    # Production 29: gust_tail → <data_type> id~ <gust_tail>
    # PREDICT = {int, float, char, string, bool}

    # Production 30: gust_tail → λ
    # PREDICT = {}}

    def parse_gust_tail(self):
        current = self.peek()

        if current in ['int', 'float', 'char', 'string', 'bool']:
            data_type_node = self.parse_data_type()
            id_no = self.check_id()
            self.match('~')
            gust_tail_node = self.parse_gust_tail()
            return ASTNode('gust_tail', children=[data_type_node, id_no, gust_tail_node])
        elif current == '}':
            return ASTNode('gust_tail_empty')
        else:
            self.error("[29-30] Expected data type or '}', "
                            f"got '{current}'")


    # <constant>
    # Production 31: constant → <data_type> id <const_dec>~
    # PREDICT = {int, float, char, string, bool}

    # Production 32: constant → <struct_const>
    # PREDICT = {gust}

    def parse_constant(self):
        current = self.peek()

        if current in ['int', 'float', 'char', 'string', 'bool']:
            data_type_node = self.parse_data_type()
            id_no = self.check_id()
            const_dec_node = self.parse_const_dec()
            self.match('~')
            return ASTNode('constant', children=[data_type_node, id_no, const_dec_node])
        elif current == 'gust':
            struct_const_node = self.parse_struct_const()
            return ASTNode('constant', children=[struct_const_node])
        else:
            self.error(f"[31-32] Expected data type or gust, got '{current}'")


    # <const_dec>
    # Production 33: const_dec → = <expr> <const_tail>
    # PREDICT = {=}

    # Production 34: const_dec → <row_size> = {<const_arr>} <const_tail>
    # PREDICT = {[}

    def parse_const_dec(self):
        current = self.peek()

        if current == '=':
            self.match('=')
            expr_node = self.parse_expr()
            const_tail_node = self.parse_const_tail()
            return ASTNode('const_dec', children=[ASTNode('operator', value='='), expr_node, const_tail_node])
        elif current == '[':
            row_size_node = self.parse_row_size()
            self.match('=')
            self.match('{')
            const_arr_node = self.parse_const_arr()
            self.match('}')
            const_tail_node = self.parse_const_tail()
            return ASTNode('const_dec', children=[row_size_node, ASTNode('operator', value='='), const_arr_node, const_tail_node])
        else:
            self.error(f"[33-34] Expected '=' or '[', got '{current}'")


    # <const_tail>
    # Production 35: const_tail → , id <const_dec>
    # PREDICT = {,}

    # Production 36: const_tail → λ
    # PREDICT = {~}

    def parse_const_tail(self):
        current = self.peek()

        if current == ',':
            self.match(',')
            id_no = self.check_id()
            const_dec_node = self.parse_const_dec()
            return ASTNode('const_tail', children=[id_no, const_dec_node])
        elif current == '~':
            return ASTNode('const_tail_empty')
        else:
            self.error(f"[35-36] Expected ',' or '~', got '{current}' ")


    # <const_arr>
    # Production 37: const_arr → <const_1d>
    # PREDICT = {int_lit, float_lit, char_lit, string_lit, yuh, naur}

    # Production 38: const_arr → <const_2d>
    # PREDICT = {{}

    def parse_const_arr(self):
        current = self.peek()

        if current in ['int_lit', 'float_lit', 'char_lit', 'string_lit', 'yuh', 'naur']:
            const_1d_node = self.parse_const_1d()
            return ASTNode('const_arr', children=[const_1d_node])
        elif current == '{':
            const_2d_node = self.parse_const_2d()
            return ASTNode('const_arr', children=[const_2d_node])
        else:
            self.error("[37-38] Expected value literal or '{', " 
                        f"got '{current}'")


    # <const_1d>
    # Production 39: const_1d → <output> <element_tail>
    # PREDICT = { ++, --, id, toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, int_lit, float_lit, yuh, naur, char_lit, string_lit }

    def parse_const_1d(self):
        current = self.peek()

        if current in ['++', '--','toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit'] or (current and current.startswith('id')): 
            output_node = self.parse_output()
            element_tail_node = self.parse_element_tail()
            return ASTNode('const_1d', children=[output_node, element_tail_node])
        else:
            self.error(f"[39] Expected value literal, got '{current}'")


    # <const_2d>
    # Production 40: const_2d → {<const_1d>} <const_2d_tail>
    # PREDICT = {{}

    def parse_const_2d(self):
        current = self.peek()

        if current == '{':
            self.match('{')
            const_1d_node = self.parse_const_1d()
            self.match('}')
            const_2d_tail_node = self.parse_const_2d_tail()
            return ASTNode('const_2d', children=[const_1d_node, const_2d_tail_node])
        else:
            self.error(f"[40] Expected '{{', got '{current}'")


    # <const_2d_tail>
    # Production 41: const_2d_tail → , {<const_1d>} <const_2d_tail>
    # PREDICT = {,}

    # Production 42: const_2d_tail → λ
    # PREDICT = {}}

    def parse_const_2d_tail(self):
        current = self.peek()

        if current == ',':
            self.match(',')
            self.match('{')
            const_1d_node = self.parse_const_1d()
            self.match('}')
            const_2d_tail_node = self.parse_const_2d_tail()
            return ASTNode('const_2d_tail', children=[const_1d_node, const_2d_tail_node])
        elif current == '}':
            return ASTNode('const_2d_tail_empty')
        else:
            self.error("[41-42] Expected ',' or '{', "
                        f"got '{current}'")


    # <struct_const>
    # Production 43: struct_const → gust id id = {<const_1d>}~
    # PREDICT = {gust}

    def parse_struct_const(self):
        current = self.peek()

        if current == 'gust':
            self.match('gust')
            id_no = self.check_id()
            id_no2 = self.check_id()
            self.match('=')
            self.match('{')
            const_1d_node = self.parse_const_1d()
            self.match('}')
            self.match('~')
            return ASTNode('struct_const', children=[id_no, id_no2, ASTNode('operator', value='='), const_1d_node])
        else:
            self.error(f"[43] Expected 'gust', got '{current}'")


    # <dimension>
    # Production 44: dimension → <row_size>
    # PREDICT = {[}

    # Production 45: dimension → λ
    # PREDICT = {~, ++, --,  =, +=, -=, *=, /=, %=, +, -, *, /, %, ], >, <, >=, <=, ==, !=, ,, ), ||, &&, } }

    def parse_dimension(self):
        current = self.peek()

        if current == '[':
            row_size_node = self.parse_row_size()
            return ASTNode('dimension', children=[row_size_node])
        elif current in ['~', '++', '--', '=', '+=', '-=', '*=', '/=', '%=',
                        '+', '-', '*', '/', '%', ']', '>', '<', '>=', '<=', '==', '!=',
                        ',', ')', '||', '&&', '}' ]:
            return ASTNode('dimension_empty')
        else:
            self.error("[44-45] Expected '~', '++', '--', operator, ')', '}'" 
                        f"got '{current}'")


    # <row_size>
    # Production 46: row_size → [<size>] <col_size>
    # PREDICT = {[}

    def parse_row_size(self):
        current = self.peek()

        if current == '[':
            self.match('[')
            size_node = self.parse_size()
            self.match(']')
            col_size_node = self.parse_col_size()
            return ASTNode('row_size', children=[size_node, col_size_node])
        else:
            self.error(f"[46] Expected '[', got '{current}'")


    # <col_size>
    # Production 47: col_size → [<pdim_size>]
    # PREDICT = {[}

    # Production 48: col_size → λ
    # PREDICT = {~, ++, --,  =, +=, -=, *=, /=, %=, +, -, *, /, %, ], >, <, >=, <=, ==, !=, ,, ), ||, &&, } }

    def parse_col_size(self):
        current = self.peek()

        if current == '[':
            self.match('[')
            pdim_size_node = self.parse_pdim_size()
            self.match(']')
            return ASTNode('col_size', children=[pdim_size_node])
        elif current in ['~', '++', '--', '=', '+=', '-=', '*=', '/=', '%=',
                        '+', '-', '*', '/', '%', ']', '>', '<', '>=', '<=', '==', '!=',
                        ',', ')', '||', '&&', '}' ]:
            return ASTNode('col_size_empty')
        else:
            self.error("[47-48] Expected '[', '}', identifier, '~', operator, statement, or 'gasp', " 
                        f"got '{current}'")


    # <size>
    # Production 49: size → <arith_expr>
    # PREDICT = {(, ++, --, id, toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, 
    #               int_lit, float_lit, yuh, naur, char_lit, string_lit, !}

    # Production 50: size → λ
    # PREDICT = {]}

    def parse_size(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            arith_expr_node = self.parse_arith_expr()
            return ASTNode('size', children=[arith_expr_node])
        elif current == ']':
            return ASTNode('size_empty')
        else:
            self.error(f"[49-50] Expected '(', identifier, value literal, or predefined function, got '{current}'")

    # <data_type>
    # Production 51–55: data_type → int | float | char | string | bool
    # PREDICT = { int | float | char | string | bool }

    def parse_data_type(self):
        current = self.peek()
    
        if current == 'int':
            self.match('int')
            return ASTNode('data_type', value='int')
        elif current == 'float':
            self.match('float')
            return ASTNode('data_type', value='float')
        elif current == 'char':
            self.match('char')
            return ASTNode('data_type', value='char')
        elif current == 'string':
            self.match('string')
            return ASTNode('data_type', value='string')
        elif current == 'bool':
            self.match('bool')
            return ASTNode('data_type', value='bool')
        else:
            self.error(f"[51-55] Expected data type (int, float, char, string, bool), got '{current}'")

    # <sub_functions>
    # Production 56: sub_functions → <air_func> <sub_functions>
    # PREDICT = {air}

    # Production 57: sub_functions → λ
    # PREDICT = {atmosphere}

    def parse_sub_functions(self):
        current = self.peek()
    
        if current == 'air':
            air_func_node = self.parse_air_func()
            sub_functions_node = self.parse_sub_functions()
            return ASTNode('sub_functions', children=[air_func_node, sub_functions_node])
        elif current == 'atmosphere':
            return ASTNode('sub_functions_empty')
        else:
            self.error(f"[56-57] Expected 'air' or 'atmosphere', got '{current}'")


    # <air_func>
    # Production 58: air_func → air <return_type> id (<params>) { <body> <return_stat> }
    # PREDICT = {air}

    def parse_air_func(self):
        current = self.peek()

        if current == 'air':
            self.match('air')
            return_type_node = self.parse_return_type()
            id_no = self.check_id()
            self.match('(')
            params_node = self.parse_params()
            self.match(')')
            self.match('{')
            body_node = self.parse_body()
            return_stat_node = self.parse_return_stat()
            self.match('}')
            return ASTNode('air_func', children=[return_type_node, id_no, params_node, body_node, return_stat_node])
        else:
            self.error(f"[58] Expected 'air', got '{current}'")


    # <return_type>
    # Production 59: return_type → <data_type>
    # PREDICT = {int, float, char, string, bool}

    # Production 60: return_type → vacuum
    # PREDICT = {vacuum}

    def parse_return_type(self):
        current = self.peek()

        if current in ['int', 'float', 'char', 'string', 'bool']:
            data_type_node = self.parse_data_type()
            return ASTNode('return_type', children=[data_type_node])
        elif current == 'vacuum':
            self.match('vacuum')
            return ASTNode('return_type', value='vacuum')
        else:
            self.error(f"[59-60] Expected data type or 'vacuum', got '{current}'")


    # <params>
    # Production 61: params → <data_type> id <params_dim> <params_tail>
    # PREDICT = {int, float, char, string, bool}

    # Production 62: params → λ
    # PREDICT = {)}

    def parse_params(self):
        current = self.peek()

        if current in ['int', 'float', 'char', 'string', 'bool']:
            data_type_node = self.parse_data_type()
            id_no = self.check_id()
            params_dim_node = self.parse_params_dim()
            params_tail_node = self.parse_params_tail()
            return ASTNode('params', children=[data_type_node, id_no, params_dim_node, params_tail_node])
        elif current == ')':
            return ASTNode('params_empty')
        else:
            self.error(f"[61-62] Expected data type or ')', got '{current}'")


    # <params_dim>
    # Production 63: params_dim → [<pdim_tail>
    # PREDICT = {[}

    # Production 64: params_dim → λ
    # PREDICT = {,, )}

    def parse_params_dim(self):
        current = self.peek()

        if current == '[':
            self.match('[')
            pdim_tail_node = self.parse_pdim_tail()
            return ASTNode('params_dim', children=[pdim_tail_node])
        elif current in [',',')']:
            return ASTNode('params_dim_node')
        else:
            self.error(f"[63-64] Expected '[' or ',' or ')', got '{current}'")


    # <pdim_tail>
    # Production 65: pdim_tail → ]
    # PREDICT = {]}

    # Production 66: pdim_tail → <pdim_size>] [<pdim_size>]
    # PREDICT = {(, ++, --, id, int_lit, float_lit, char_lit, string_lit, yuh, naur, toRise, toFall, 
    #            horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, !}

    def parse_pdim_tail(self):
        current = self.peek()

        if current == ']':
            self.match(']')
            return ASTNode('params_pdim_tail', value=']')
        elif current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            pdim_size_node = self.parse_pdim_size()
            self.match(']')
            self.match('[')
            pdim_size_node2 = self.parse_pdim_size()
            self.match(']')
            return ASTNode('params_pdim_tail', children=[pdim_size_node, pdim_size_node2])
        else:
            self.error(f"[65-66] Expected '(', identifier, value literal, or predefined function, got '{current}'")


    # <pdim_size>
    # Production 67: pdim_size → <arith_expr>
    # PREDICT = {(, ++, --, id, int_lit, float_lit, char_lit, string_lit, yuh, naur, toRise, toFall, 
    #            horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, !}

    def parse_pdim_size(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            arith_expr_node = self.parse_arith_expr()
            return ASTNode('pdim_size', children=[arith_expr_node])
        else:
            self.error(f"[67] Expected '(', identifier, value literal, or predefined function, got '{current}'")


    # <params_tail>
    # Production 68: params_tail → , <data_type> id <params_dim> <params_tail>
    # PREDICT = {,}

    # Production 69: params_tail → λ
    # PREDICT = {)}

    def parse_params_tail(self):
        current = self.peek()

        if current == ',':
            self.match(',')
            data_type_node = self.parse_data_type()
            id_no = self.check_id()
            params_dim_node = self.parse_params_dim
            params_tail_node = self.parse_params_tail
            return ASTNode('params_tail', children=[data_type_node, id_no, params_dim_node, params_tail_node])
        elif current == ')':
            return ASTNode('params_tail_empty')
        else:
            self.error(f"[68-69] Expected ',' or ')', got '{current}'")


    # <body>
    # Production 70: body → <stmt_list>
    # PREDICT = { int, float, char, string, bool, gust, wind, inhale, exhale, ++, --, id, if, stream, cycle, echo, do, }, gasp }

    def parse_body(self):
        current = self.peek()

        if current in ['int', 'float', 'char', 'string', 'bool', 'gust', 'wind', 'inhale', 'exhale', '++', '--',
                            'if', 'stream', 'cycle', 'echo', 'do', '}', 'gasp'] or (current and current.startswith('id')):
            stmt_list_node = self.parse_stmt_list()
            return ASTNode('body', children=[stmt_list_node])
        else:
            self.error(f"[70] Invalid body start. Expected statements, got '{current}'")



    # <stmt_list>
    # Production 71: stmt_list → <statement> <stmt_list>
    # PREDICT = { int, float, char, string, bool, gust, wind, inhale, exhale, ++, --, id, if, stream, cycle, echo, do }

    # Production 72: stmt_list → λ
    # PREDICT = {}, gasp, resist}

    def parse_stmt_list(self):
        current = self.peek()

        if current in ['int', 'float', 'char', 'string', 'bool', 'gust', 'wind', 'inhale', 'exhale', '++', '--',
                            'if', 'stream', 'cycle', 'echo', 'do'] or (current and current.startswith('id')):
            statement_node = self.parse_statement()

            if statement_node is None:  # Error occurred
                raise StopIteration

            stmt_list_node = self.parse_stmt_list()
            return ASTNode('stmt_list', children=[statement_node, stmt_list_node])
        elif current in ['}','gasp','resist']:
            return ASTNode('stmt_list_empty')
        elif current == None:
            raise StopIteration
        else:
            self.error(f"[71-72] Invalid statements, got '{current}'")
            



    # <statement>
    # Production 73–77: statement → <declaration> | <input_output> | <identifier_stat> | <conditioner> | <iteration>
    # PREDICT = {int, float, char, string, bool, gust, wind}
    # PREDICT = {inhale, exhale}
    # PREDICT = {++, --, id}
    # PREDICT = {if, stream}
    # PREDICT = {cycle, echo, do}

    def parse_statement(self):
        current = self.peek()
        try:
            if current in ['int', 'float', 'char', 'string', 'bool', 'gust', 'wind']:
                declaration_node = self.parse_declaration()
                return ASTNode('statement', children=[declaration_node])
            elif current in ['inhale', 'exhale']:
                input_output_node = self.parse_input_output()
                return ASTNode('statement', children=[input_output_node])
            elif current in ['++', '--'] or (current and current.startswith('id')):
                identifier_stat_node = self.parse_identifier_stat()
                return ASTNode('statement', children=[identifier_stat_node])
            elif current in ['if', 'stream']:
                conditioner_node = self.parse_conditioner()
                return ASTNode('statement', children=[conditioner_node])
            elif current in ['cycle', 'echo', 'do']:
                iteration_node = self.parse_iteration()
                return ASTNode('statement', children=[iteration_node])
            else:
                self.error(f"[73-77] Invalid statements, got '{current}'")
                raise StopIteration
        except StopIteration:
            # Re-raise to propagate up
            raise


    # <identifier_stat>
    # Production 78: identifier_stat → <unary_op> id <id_access>~
    # PREDICT = {++,--}

    # Production 79: identifier_stat → id <id_stat_body>~
    # PREDICT = {id}

    def parse_identifier_stat(self):
        current = self.peek()

        if current in ['++', '--']:
            unary_op_node = self.parse_unary_op()
            id_no = self.check_id()
            id_access_node = self.parse_id_access()
            self.match('~')
            return ASTNode('identifier_stat', children=[unary_op_node, id_no, id_access_node])
        elif current and current.startswith('id'):
            id_no = self.check_id()
            id_stat_body_node = self.parse_id_stat_body()
            self.match('~')
            return ASTNode('identifier_stat', children=[id_no, id_stat_body_node])
        else:
            self.error(f"[78-79] Expected '++' or '--' or identifier, got '{current}'")

    # <id_stat_body>
    # Production 80: id_stat_body → (<param_opts>)
    # PREDICT = {(}

    # Production 81: id_stat_body → <id_access> <id_stat_tail>
    # PREDICT = {[, .,  ++, --,  =, +=, -=, *=, /=, %=}

    def parse_id_stat_body(self):
        current = self.peek()

        if current == '(':
            self.match('(')
            param_opts_node = self.parse_param_opts()
            self.match(')')
            return ASTNode('id_stat_body', children=[param_opts_node])
        elif current in [ '[', '.', '++', '--', '=', '+=', '-=', '*=', '/=', '%=']:
            id_access_node = self.parse_id_access()
            id_stat_tail_node = self.parse_id_stat_tail()
            return ASTNode('id_stat_body', children=[id_access_node, id_stat_tail_node])
        else:
            self.error(f"[80-81] Expected [, .,  ++, --,  =, +=, -=, *=, /=, %=, got '{current}'")
            
    # <id_stat_tail>
    # Production 82: id_stat_tail → <unary_op>
    # PREDICT = { ++, -- }

    # Production 83: id_stat_tail → <assignment>
    # PREDICT = { =, +=, -=, *=, /=, %= }

    def parse_id_stat_tail(self):
        current = self.peek()

        if current in ['++', '--']:
            unary_op_node = self.parse_unary_op()
            return ASTNode('id_stat_tail', children=[unary_op_node])
        elif current in ['=', '+=', '-=', '*=', '/=', '%=']:
            assignment_node = self.parse_assignment()
            return ASTNode('identifier_stat', children=[assignment_node])
        else:
            self.error(f"[82-83] Expected ++, --,  =, +=, -=, *=, /=, %=, got '{current}'")

    # <identifier>
    # Production 84: identifier → <unary_op> id<id_access>
    # PREDICT = { ++, -- }

    # Production 85: identifier → id<id_tail>
    # PREDICT = {id}

    def parse_identifier(self):
        current = self.peek()

        if current in ['++', '--']:
            unary_op_node = self.parse_unary_op()
            id_no = self.check_id()
            id_access_node = self.parse_id_access()
            return ASTNode('identifier', children=[unary_op_node, id_access_node])
        elif current and current.startswith('id'):
            id_no = self.check_id()
            id_tail_node = self.parse_id_tail()
            return ASTNode('identifier', children=[id_no, id_tail_node])
        else:
            self.error(f"[84-85] Expected ++, --, or identifier, got '{current}'")

    # <id_tail>
    # Production 86: id_tail → (<param_opts>)
    # PREDICT = { ( }

    # Production 87: id_tail → <id_access> <unary_op2>
    # PREDICT = { [, ., ~, ++, --, =, +=, -=, *=, /=, %=, +, -, *, /, %, ], >, <, >=, <=, ==, !=, ,, ), ||, &&, } }

    def parse_id_tail(self):
        current = self.peek()

        if current == '(':
            self.match('(')
            param_opts_node = self.parse_param_opts()
            self.match(')')
            return ASTNode('id_tail', children=[param_opts_node])
        elif current in ['[', '.', '~', '++', '--', '=','+=', '-=', '*=', '/=', '%=',
                         '+', '-', '*', '/', '%', ']', '>', '<', '>=', '<=', '==', '!=',',', ')', '||', '&&', '}']:
            id_access_node = self.parse_id_access()
            unary_op2_node = self.parse_unary_op2()
            return ASTNode('id_tail', children=[id_access_node, unary_op2_node])
        else:
            self.error("[86-87] Expected (, [, ., ~, ++, --, +, -, *, /, %, ], >, <, >=, <=, ==, !=, ,, ), ||, &&, } " 
            f"got '{current}'")        


    # <id_access>
    # Production 88: id_access → <dimension>
    # PREDICT = { [, ~, ++, --,  =, +=, -=, *=, /=, %=, +, -, *, /, %, ], >, <, >=, <=, ==, !=, ,, ), ||, &&, } }

    # Production 89: id_access → .id
    # PREDICT = {.}


    def parse_id_access(self):
        current = self.peek()

        if current in ['[', '~', '++', '--', '=','+=', '-=', '*=', '/=', '%=',
                         '+', '-', '*', '/', '%', ']', '>', '<', '>=', '<=', '==', '!=',',', ')', '||', '&&', '}']:
            dimension_node = self.parse_dimension()
            return ASTNode('id_access', children=[dimension_node])
        elif current == '.':
            self.match('.')
            id_no = self.check_id()
            return ASTNode('id_access', children=['.', id_no])
        else:
            self.error("[88-91] Expected (, [, ., ~, ++, --, +, -, *, /, %, ], >, <, >=, <=, ==, !=, ,, ), ||, &&, }  "
                    f"got '{current}'")


    # <unary_op>
    # Production 90–91: unary_op → ++ | --
    # PREDICT = {++ | --}

    def parse_unary_op(self):
        current = self.peek()

        if current == '++':
            self.match('++')
            return ASTNode('unary_op', value='++')
        elif current == '--':
            self.match('--')
            return ASTNode('unary_op', value='--')
        else:
            self.error(f"[90-91] Expected '++' or '--', got '{current}'")
                

    # <unary_op2>
    # Production 92: unary_op2 → <unary_op>
    # PREDICT = {++, --}

    # Production 93: unary_op2 → λ
    # PREDICT = { +, -, *, /, %, ], >, <, >=, <=, ==, !=, ,, ~, ), ||, &&, } }

    def parse_unary_op2(self):
        current = self.peek()

        if current in ['++', '--']:
            unary_op_node = self.parse_unary_op()
            return ASTNode('unary_op2', children=[unary_op_node])
        elif current in ['+', '-', '*', '/', '%', ']', '>', '<', '>=', '<=', '==', '!=', ',', '~', ')', '||', '&&', '}']:
            return ASTNode('unary_op2_empty')
        else:
            self.error("[92-93] Expected ++, --, +, -, *, /, %, ], >, <, >=, <=, ==, !=, ,, ~, ), ||, &&, }" 
                        f"got '{current}'")


    # <input_output>
    # Production 94: input_output → inhale(id <id_access>)~
    # PREDICT = {inhale}

    # Production 95: input_output → exhale(<output>)~
    # PREDICT = {exhale}

    def parse_input_output(self):
        current = self.peek()

        if current == 'inhale':
            self.match('inhale')
            self.match('(')
            id_no = self.check_id()
            id_access_node = self.parse_id_access()
            self.match(')')
            self.match('~')
            return ASTNode('input_output', children=['inhale', id_no, id_access_node])
        elif current == 'exhale':
            self.match('exhale')
            self.match('(')
            output_node = self.parse_output()
            self.match(')')
            self.match('~')
            return ASTNode('input_output', children=['exhale', output_node])
        else:
            self.error(f"[94-95] Expected 'inhale' or 'exhale', got '{current}'")

    # <output>
    # Production 96: output → <identifier> 
    # PREDICT = {++, --, id}

    # Production 97: output → <function_call>
    # PREDICT = {toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft}

    # Production 98: output → <value>
    # PREDICT = { int_lit, float_lit, yuh, naur }

    # Production 99: output → <output_concat> <output_tail>
    # PREDICT = {char_lit, string_lit}

    def parse_output(self):
        current = self.peek()

        if current in ['++', '--'] or (current and current.startswith('id')):
            identifier_node = self.parse_identifier()
            return ASTNode('output', children=[identifier_node])
        elif current in ['toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft']:
            function_call_node = self.parse_function_call()
            return ASTNode('output', children=[function_call_node])
        elif current in ['int_lit', 'float_lit', 'yuh', 'naur']:
            value_node = self.parse_value()
            return ASTNode('output', children=[value_node])
        elif current in ['char_lit', 'string_lit']:
            output_concat_node = self.parse_output_concat()
            output_tail_node = self.parse_output_tail()
            return ASTNode('output', children=[output_concat_node, output_tail_node])
        else:
            self.error(f"[98-100] Expected '++, --' or function call or character/string literal, got '{current}'")

    # <value>
    # Production 100–103: value → int_lit | float_lit | yuh | naur
    # PREDICT = { int_lit | float_lit | yuh | naur }

    def parse_value(self):
        current = self.peek()

        if current == 'int_lit':
            litvalue = self.match('int_lit')
            return ASTNode('value', value=litvalue.value)
        elif current == 'float_lit':
            litvalue = self.match('float_lit')
            return ASTNode('value', value=litvalue.value)
        elif current == 'yuh':
            litvalue = self.match('yuh')
            return ASTNode('value', value=litvalue.value)
        elif current == 'naur':
            litvalue = self.match('naur')
            return ASTNode('value', value=litvalue.value)
        else:
            self.error(f"[100-103] Expected value literal, got '{current}'")

    # <output_concat>
    # Production 104–105: output_content → char_lit | string_lit
    # PREDICT = {char_lit | string_lit}

    def parse_output_concat(self):
        current = self.peek()

        if current == 'char_lit':
            litvalue = self.match('char_lit')
            return ASTNode('output_content', value=litvalue.value)
        elif current == 'string_lit':
            litvalue = self.match('string_lit')
            return ASTNode('output_content', value=litvalue.value)
        else:
            self.error(f"[104-105] Expected character/string literal, got '{current}'")

    # <output_tail>
    # Production 106: output_tail → & <output_concat> <output_tail>
    # PREDICT = {&}

    # Production 107: output_tail → λ
    # PREDICT = { +, -, *, /, %, ], >, <, >=, <=, ==, !=, ,, ~, ), ||, &&, } }

    def parse_output_tail(self):
        current = self.peek()

        if current == '&':
            self.match('&')
            output_concat_node = self.parse_output_concat()
            output_tail_node = self.parse_output_tail()
            return ASTNode('output_tail', children=[output_concat_node, output_tail_node])
        elif current in ['+', '-', '*', '/', '%', ']', '>', '<', '>=', '<=', '==', '!=', ',', '~', ')', '||', '&&', '}']:
            return ASTNode('output_tail_empty')
        else:
            self.error(f"[106-107] Expected '&' or operators or terminator '~', got '{current}'")

    # <assi_op>
    # Production 108-113: assi_op → = | += | -= | *= | /= | %=
    # PREDICT = {= | += | -= | *= | /= | %=}

    def parse_assi_op(self):
        current = self.peek()

        if current == '=':
            self.match('=')
            return ASTNode('assi_op', ASTNode('operator', value='='))
        elif current == '+=':
            self.match('+=')
            return ASTNode('assi_op', ASTNode('operator', value='+='))
        elif current == '-=':
            self.match('-=')
            return ASTNode('assi_op', ASTNode('operator', value='-='))
        elif current == '*=':
            self.match('*=')
            return ASTNode('assi_op', ASTNode('operator', value='*='))
        elif current == '/=':
            self.match('/=')
            return ASTNode('assi_op', ASTNode('operator', value='/='))
        elif current == '%=':
            self.match('%=')
            return ASTNode('assi_op', ASTNode('operator', value='%='))
        else:
            self.error(f"[108-113] Expected assignment operator, got '{current}'")


    # <assignment>
    # Production 114: assignment → <assi_op> <expr>
    # PREDICT = {=, +=, -=, *=, /=, %=}

    def parse_assignment(self):
        current = self.peek()

        if current in ['=', '+=', '-=', '*=', '/=', '%=']:
            assi_op_node = self.parse_assi_op()
            expr_node = self.parse_expr()
            return ASTNode('assignment', children=[assi_op_node, expr_node])
        else:
            self.error(f"[114] Expected assignment operator, got '{current}'")


    # <expr>
    # Production 115: expr → <logic_expr>
    # PREDICT = { (, ++, --, id, toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, int_lit, float_lit, yuh, naur, char_lit, string_lit, ! }

    def parse_expr(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            logic_expr_node = self.parse_logic_expr()
            return ASTNode('expr', children=[logic_expr_node])
        else:
            self.error(f"[115] Expected '(', identifier, value literal, or function call, got '{current}'")


    # <logic_expr>
    # Production 116: logic_expr → <and_expr> <or_tail>
    # PREDICT = {(, ++, --, id, toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, int_lit, float_lit, yuh, naur, char_lit, string_lit, ! }

    def parse_logic_expr(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            and_expr_node = self.parse_and_expr()
            or_tail_node = self.parse_or_tail()
            return ASTNode('logic_expr', children=[and_expr_node, or_tail_node])
        else:
            self.error(f"[116] Expected '(', identifier, value literal, or function call, got '{current}'")


    # <or_tail>
    # Production 117: or_tail → || <and_expr> <or_tail>
    # PREDICT = {||}

    # Production 118: or_tail → λ
    # PREDICT = {~, ,, )}

    def parse_or_tail(self):
        current = self.peek()
        
        if current == '||':
            self.match('||')

            next_tok = self.peek()
            if next_tok not in self.FIRST_PRIMARY and not (next_tok and next_tok.startswith('id')):
                self.error(f"[117-118] Expected relational expression after or boolean '||' operator")
                raise StopIteration
            
            and_expr_node = self.parse_and_expr()
            or_tail_node = self.parse_or_tail()
            return ASTNode('or_tail', children=[and_expr_node, or_tail_node])
        elif current in ['~', ',', ')']:
            return ASTNode('or_tail_empty')
        else:
            self.error(f"[117-118] Expected '||' or '~' or ',' , got '{current}'")


    # <and_expr>
    # Production 119: and_expr → <rela_expr> <and_tail>
    # PREDICT = {(, ++, --, id, int_lit, float_lit, char_lit, string_lit, yuh, naur, toRise, toFall, 
    #            horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, !}

    def parse_and_expr(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            rela_expr_node = self.parse_rela_expr()
            and_tail_node = self.parse_and_tail()
            return ASTNode('and_expr', children=[rela_expr_node, and_tail_node])
        else:
            self.error(f"[119] Expected '(', identifier, value literal, or function call, got '{current}'")


    # <and_tail>
    # Production 120: and_tail → && <rela_expr> <and_tail>
    # PREDICT = {&&}

    # Production 121: and_tail → λ
    # PREDICT = { ~, ,, ), || }

    def parse_and_tail(self):
        current = self.peek()
        
        if current == '&&':
            self.match('&&')

            next_tok = self.peek()
            if next_tok not in self.FIRST_PRIMARY and not (next_tok and next_tok.startswith('id')):
                self.error(f"[120-121] Expected relational expression or boolean after '&&' operator")
                raise StopIteration

            rela_expr_node = self.parse_rela_expr()
            and_tail_node = self.parse_and_tail()
            return ASTNode('and_tail', children=[rela_expr_node, and_tail_node])
        elif current in ['||', '~', ',', ')']:
            return ASTNode('and_tail_empty')
        else:
            self.error(f"[120-121] Expected '&&' or '||' or '~' or ',' , got '{current}'")


    # <rela_expr>
    # Production 122: rela_expr → <arith_expr> <rela_tail>
    # PREDICT = {(, ++, --, id, int_lit, float_lit, char_lit, string_lit, yuh, naur, toRise, toFall, 
    #            horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, !}

    def parse_rela_expr(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            arith_expr_node = self.parse_arith_expr()
            rela_tail_node = self.parse_rela_tail()
            return ASTNode('rela_expr', children=[arith_expr_node, rela_tail_node])
        else:
            self.error(f"[122] Expected '(', identifier, value literal, or function call, got '{current}'")


    # <rela_tail>
    # Production 123: rela_tail → <rela_sym> <arith_expr>
    # PREDICT = {>, <, >=, <=, ==, !=}

    # Production 124: rela_tail → λ
    # PREDICT = { ~, ,, ), ||, && }

    def parse_rela_tail(self):
        current = self.peek()

        if current in ['>', '<', '>=', '<=', '==', '!=']:
            rela_sym_node = self.parse_rela_sym()

            next_tok = self.peek()
            if next_tok not in self.FIRST_PRIMARY and not (next_tok and next_tok.startswith('id')):
                self.error(f"[122-123] Expected expression after '{rela_sym_node.children[0].value}' operator")
                raise StopIteration

            arith_expr_node = self.parse_arith_expr()
            return ASTNode('rela_tail', children=[rela_sym_node, arith_expr_node])
        elif current in ['&&', '||', '~', ',', ')']:
            return ASTNode('rela_tail_empty')
        else:
            self.error(f"[122-123] Expected relational symbols (> < >= <= == !=) or '&&' '||' '~' ',' ')', got '{current}'")

    # <rela_sym>
    # Production 125-130: rela_sym → > | < | >= | <= | == | !=
    # PREDICT = {> | < | >= | <= | == | !=}

    def parse_rela_sym(self):
        current = self.peek()

        if current == '>':
            self.match('>')
            return ASTNode('rela_sym', ASTNode('operator', value='>'))
        elif current == '<':
            self.match('<')
            return ASTNode('rela_sym', ASTNode('operator', value='<'))
        elif current == '>=':
            self.match('>=')
            return ASTNode('rela_sym', ASTNode('operator', value='>='))
        elif current == '<=':
            self.match('<=')
            return ASTNode('rela_sym', ASTNode('operator', value='<='))
        elif current == '==':
            self.match('==')
            return ASTNode('rela_sym', ASTNode('operator', value='=='))
        elif current == '!=':
            self.match('!=')
            return ASTNode('rela_sym', ASTNode('operator', value='!='))
        else:
            self.error(f"[125-130] Expected relational symbols (> < >= <= == !=), got '{current}'")

    # <arith_op1>
    # Production 131-132: arith_op1 → + | -
    # PREDICT = {+ | -}

    def parse_arith_op1(self):
        current = self.peek()

        if current == '+':
            self.match('+')
            return ASTNode('arith_op1', ASTNode('operator', value='+'))
        elif current == '-':
            self.match('-')
            return ASTNode('arith_op1', ASTNode('operator', value='-'))
        else:
            self.error(f"[131-132] Expected '+' or '-', got '{current}'")

    # <arith_op2>
    # Production 133-135: arith_op2 → * | / | %
    # PREDICT = {* | / | %}

    def parse_arith_op2(self):
        current = self.peek()

        if current == '*':
            self.match('*')
            return ASTNode('arith_op2', ASTNode('operator', value='*'))
        elif current == '/':
            self.match('/')
            return ASTNode('arith_op2', ASTNode('operator', value='/'))
        elif current == '%':
            self.match('%')
            return ASTNode('arith_op2', ASTNode('operator', value='%'))
        else:
            self.error(f"[133-135] Expected '*' or '/' or '%', got '{current}'")

    # <arith_expr>
    # Production 136: arith_expr → <term> <arith_tail>
    # PREDICT = {(, ++, --, id, int_lit, float_lit, char_lit, string_lit, yuh, naur, toRise, toFall, 
    #            horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, !}

    def parse_arith_expr(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            term_node = self.parse_term()
            arith_tail_node = self.parse_arith_tail()
            return ASTNode('arith_expr', children=[term_node, arith_tail_node])
        else:
            self.error(f"[136] Expected '(', identifier, value literal, or function call, got '{current}'")


    # <arith_tail>
    # Production 137: arith_tail → <arith_op1> <term> <arith_tail>
    # PREDICT = {+, -}

    # Production 138: arith_tail → λ
    # PREDICT = { ~, ,, ], ), ||, &&, >, <, >=, <=, ==, != }

    def parse_arith_tail(self):
        current = self.peek()

        if current in ['+', '-']:
            arith_op1_node = self.parse_arith_op1()

            next_tok = self.peek()
            if next_tok not in self.FIRST_PRIMARY and not (next_tok and next_tok.startswith('id')):
                self.error(f"[137-138] Expected expression after '{arith_op1_node.children[0].value}' operator")
                raise StopIteration

            term_node = self.parse_term()
            arith_tail_node = self.parse_arith_tail()
            return ASTNode('arith_tail', children=[arith_op1_node, term_node, arith_tail_node])
        elif current in ['~', ',', ']', ')', '||', '&&', '>', '<', '>=', '<=', '==', '!=']:
            return ASTNode('arith_tail_empty')
        else:
            self.error(f"[137-138] Expected operators or '~', got '{current}'")


    # <term>
    # Production 139: term → <factor> <term_tail>
    # PREDICT = {(, ++, --, id, int_lit, float_lit, char_lit, string_lit, yuh, naur, toRise, toFall, 
    #            horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, !}

    def parse_term(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            factor_node = self.parse_factor()
            term_tail_node = self.parse_term_tail()
            return ASTNode('term', children=[factor_node, term_tail_node])
        else:
            self.error(f"[139] Expected '(', identifier, value literal, or function call, got '{current}'")
            raise StopIteration



    # <term_tail>
    # Production 140: term_tail → <arith_op2> <factor> <term_tail>
    # PREDICT = {*, /, %}

    # Production 141: term_tail → λ
    # PREDICT = { ~, ], ,, ), ||, &&, >, <, >=, <=, ==, !=, +, -}

    def parse_term_tail(self):
        current = self.peek()

        if current in ['*', '/', '%']:
            arith_op2_node = self.parse_arith_op2()

            next_tok = self.peek()
            if next_tok not in self.FIRST_PRIMARY and not (next_tok and next_tok.startswith('id')):
                self.error(f"[140-141] Expected expression after '{arith_op2_node.children[0].value}' operator")
                raise StopIteration

            factor_node = self.parse_factor()
            term_tail_node = self.parse_term_tail()
            return ASTNode('term_tail', children=[arith_op2_node, factor_node, term_tail_node])
        elif current in ['~', ']', ',', ')', '||', '&&', '>', '<', '>=', '<=', '==', '!=', '+', '-']:
            return ASTNode('term_tail_empty')
        else:
            self.error(f"[140-141] Expected terminator '~' or continuation of expression, got '{current}'.")
            raise StopIteration
        

    # <factor>
    # Production 142: factor → <primary>
    # PREDICT = {(, ++, --, id, int_lit, float_lit, char_lit, string_lit, yuh, naur, toRise, toFall, 
    #            horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, !}

    def parse_factor(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            primary_node = self.parse_primary()
            return ASTNode('factor', children=[primary_node])
        else:
            self.error(f"[142] Expected '(', identifier, value literal, or function call, got '{current}'")
            raise StopIteration


    # <primary>
    # Production 143: primary → ( <expr> )
    # PREDICT = {(}

    # Production 144: primary → <output>
    # PREDICT = { ++, --, id, toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, int_lit, float_lit, yuh, naur, char_lit, string_lit }

    # Production 145: primary → !( <logic_expr> )
    # PREDICT = {!}

    def parse_primary(self):
        current = self.peek()

        if current == '(':
            self.match('(')
            expr_node = self.parse_expr()
            self.match(')')
            return ASTNode('primary', children=[expr_node])
        elif current in ['++', '--','toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit'] or (current and current.startswith('id')): 
            output_node = self.parse_output()
            return ASTNode('primary', children=[output_node])
        elif current == '!':
            self.match('!')
            self.match('(')
            logic_expr_node = self.parse_logic_expr()
            self.match(')')
            return ASTNode('primary', children=[logic_expr_node])
        else:
            if current in ['+', '-', '*', '/', '%', '=', '+=', '-=', '*=', '/=', '%=', '>', '<', '>=', '<=', '==', '!=']:
                self.error(f"Unexpected operator '{current}' - expected value or identifier")
            elif current in ['~', '}', ',', ')']:
                self.error(f"[143-145] Incomplete expression - unexpected '{current}'")
            else:
                self.error(f"[143-145] Expected value, identifier, or '(' in expression, got '{current}'")
            raise StopIteration


    # <stmt_ctrl>
    # Production 146: stmt_ctrl → <statement> <stmt_ctrl>
    # PREDICT = {int, float, char, string, bool, gust, wind, inhale, exhale, ++, --, id, if, stream, cycle, echo, do}

    # Production 147: stmt_ctrl → <ctrl_flow> <stmt_ctrl>
    # PREDICT = {resist, flow}

    # Production 148: stmt_ctrl → λ
    # PREDICT = {}}

    def parse_stmt_ctrl(self):
        current = self.peek()

        if current in ['int', 'float', 'char', 'string', 'bool', 'gust', 'wind', 'inhale', 'exhale', '++', '--', 'if', 'stream', 'cycle', 'echo', 'do'] or (current and current.startswith('id')):
            statement_node = self.parse_statement()
            stmt_ctrl_node = self.parse_stmt_ctrl()
            return ASTNode('stmt_ctrl', children=[statement_node, stmt_ctrl_node])
        elif current in ['resist', 'flow']:
            ctrl_flow_node = self.parse_ctrl_flow()
            stmt_ctrl_node = self.parse_stmt_ctrl()
            return ASTNode('stmt_ctrl', children=[ctrl_flow_node, stmt_ctrl_node])
        elif current == '}':
            return ASTNode('stmt_ctrl_empty')
        else:
            self.error(f"[146-148] Expected statement(s), got '{current}'")


    # <ctrl_flow>
    # Production 149–150: ctrl_flow → resist~ | flow~
    # PREDICT = {resist | flow}

    def parse_ctrl_flow(self):
        current = self.peek()

        if current == 'resist':
            self.match('resist')
            self.match('~')
            return ASTNode('ctrl_flow', value='resist')
        elif current == 'flow':
            self.match('flow')
            self.match('~')
            return ASTNode('ctrl_flow', value='flow')
        else:
            self.error(f"[149-150] Expected 'resist' or 'flow', got '{current}'")

    # <conditioner>
    # Production 151–152: conditioner → <if_stat> | <switch_stat>
    # PREDICT = {if | stream}

    def parse_conditioner(self):
        current = self.peek()

        if current == 'if':
            if_stat_node = self.parse_if_stat()
            return ASTNode('conditioner', children=[if_stat_node])
        if current == 'stream':
            switch_stat_node = self.parse_switch_stat()
            return ASTNode('conditioner', children=[switch_stat_node])
        else:
            self.error(f"[151-152] Expected 'if' or 'stream', got '{current}'")

    # <if_stat>
    # Production 153: if_stat → if (<cond_stat>) {<stmt_ctrl>} <if_tail>
    # PREDICT = {if}

    def parse_if_stat(self):
        current = self.peek()

        if current == 'if':
            self.match('if')
            self.match('(')
            cond_stat_node = self.parse_cond_stat()
            self.match(')')
            self.match('{')
            stmt_ctrl_node = self.parse_stmt_ctrl()
            self.match('}')
            if_tail_node = self.parse_if_tail()
            return ASTNode('if_stat', children=[cond_stat_node, stmt_ctrl_node, if_tail_node])
        else:
            self.error(f"[153] Expected 'if', got '{current}'")


    # <if_tail>
    # Production 154: if_tail → elseif (<cond_stat>) {<stmt_ctrl>} <if_tail>
    # PREDICT = {elseif}

    # Production 155: if_tail → else {<stmt_ctrl>}
    # PREDICT = {else}

    # Production 156: if_tail → λ
    # PREDICT = { }, int, float, char, string, bool, gust, wind, inhale, exhale, ++, --, id, resist, flow, if, stream, cycle, echo, do, gasp }

    def parse_if_tail(self):
        current = self.peek()

        if current == 'elseif':
            self.match('elseif')
            self.match('(')
            cond_stat_node = self.parse_cond_stat()
            self.match(')')
            self.match('{')
            stmt_ctrl_node = self.parse_stmt_ctrl()
            self.match('}')
            if_tail_node = self.parse_if_tail()
            return ASTNode('if_tail', children=[cond_stat_node, stmt_ctrl_node, if_tail_node])
        elif current == 'else':
            self.match('else')
            self.match('{')
            stmt_ctrl_node = self.parse_stmt_ctrl()
            self.match('}')
            return ASTNode('if_tail', children=[stmt_ctrl_node])
        elif current in ['}', 'int', 'float', 'char', 'string', 'bool', 'gust', 'wind', 'inhale', 'exhale', '++', '--', 
                         'resist', 'flow', 'if', 'stream', 'cycle', 'echo', 'do', 'gasp'] or (current and current.startswith('id')):
            return ASTNode('if_tail_empty')
        else:
            self.error(f"[154-156] Expected 'elseif' or 'else' or other statements, got '{current}'")

    # <cond_stat>
    # Production 157: cond_stat → <expr>
    # PREDICT = {(, ++, --, id, int_lit, float_lit, char_lit, string_lit, yuh, naur, toRise, toFall, 
    #            horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, !}

    def parse_cond_stat(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            expr_node = self.parse_expr()
            return ASTNode('cond_stat', children=[expr_node])
        else:
            self.error(f"[157] Expected '(', identifier, value literal, or function call, got '{current}'")


    # <switch_stat>
    # Production 158: switch_stat → stream (id <id_access>) { <switch_cases> <switch_def> }
    # PREDICT = {stream}

    def parse_switch_stat(self):
        current = self.peek()

        if current == 'stream':
            self.match('stream')
            self.match('(')
            id_no = self.check_id()
            id_access_node = self.parse_id_access()
            self.match(')')
            self.match('{')
            switch_cases_node = self.parse_switch_cases()
            switch_def_node = self.parse_switch_def()
            self.match('}')
            return ASTNode('switch_stat', children=[id_no, id_access_node, switch_cases_node, switch_def_node])
        else:
            self.error(f"[158] Expected 'stream', got '{current}'")


    # <switch_cases>
    # Production 159: switch_cases → case <switch_opts> : <stmt_list> resist~ <switch_cases>
    # PREDICT = {case}

    # Production 160: switch_cases → λ
    # PREDICT = { }, diffuse }

    def parse_switch_cases(self):
        current = self.peek()
        
        if current == 'case':
            self.match('case')
            switch_opts_node = self.parse_switch_opts()
            self.match(':')
            stmt_list_node = self.parse_stmt_list()
            self.match('resist')
            self.match('~')
            switch_cases_node = self.parse_switch_cases()
            return ASTNode('switch_cases', children=[switch_opts_node, stmt_list_node, switch_cases_node])
        elif current in [ '}', 'diffuse']:
            return ASTNode('switch_cases_empty')
        else:
            self.error("[159-160] Expected 'case' or 'diffuse' or '}', " 
                        f"got '{current}'")

    # <switch_opts>
    # Production 161-162: switch_opts → int_lit | char_lit
    # PREDICT = {int_lit | char_lit}

    def parse_switch_opts(self):
        current = self.peek()

        if current == 'int_lit':
            litvalue = self.match('int_lit')
            return ASTNode('switch_opts', value=litvalue.value)
        elif current == 'char_lit':
            litvalue = self.match('char_lit')
            return ASTNode('switch_opts', value=litvalue.value)
        else:
            self.error(f"[161-162] Expected integer type literal, got '{current}'")


    # <switch_def>
    # Production 163: switch_def → diffuse: <stmt_list> resist~
    # PREDICT = {diffuse}

    # Production 164: switch_def → λ
    # PREDICT = {}}

    def parse_switch_def(self):
        current = self.peek()

        if current == 'diffuse':
            self.match('diffuse')
            self.match(':')
            stmt_list_node = self.parse_stmt_list()
            self.match('resist')
            self.match('~')
            return ASTNode('switch_def', children=[stmt_list_node])
        elif current == '}':
            return ASTNode('switch_def_empty')
        else:
            self.error("[163-165] Expected 'diffuse' or '}', " 
                        f"got '{current}'")


    # <iteration>
    # Production 165–167: iteration → <while_loop> | <for_loop> | <dowhile_loop>
    # PREDICT = {cycle | echo | do}

    def parse_iteration(self):
        current = self.peek()

        if current == 'cycle':
            while_loop_node = self.parse_while_loop()
            return ASTNode('iteration', children=[while_loop_node])
        elif current == 'echo':
            for_loop_node = self.parse_for_loop()
            return ASTNode('iteration', children=[for_loop_node])
        elif current == 'do':
            dowhile_loop_node = self.parse_dowhile_loop()
            return ASTNode('iteration', children=[dowhile_loop_node])
        else:
            self.error(f"[165-167] Expected 'cycle' or 'echo' or 'do', got '{current}'")


    # <while_loop>
    # Production 168: while_loop → cycle (<cond_stat>) { <stmt_ctrl> }
    # PREDICT = {cycle}

    def parse_while_loop(self):
        current = self.peek()

        if current == 'cycle':
            self.match('cycle')
            self.match('(')
            cond_stat_node = self.parse_cond_stat()
            self.match(')')
            self.match('{')
            stmt_ctrl_node = self.parse_stmt_ctrl()
            self.match('}')
            return ASTNode('while_loop', children=[cond_stat_node, stmt_ctrl_node])
        else:
            self.error(f"[168] Expected 'cycle', got '{current}'")


    # <for_loop>
    # Production 169: for_loop → echo (<for_init> <cond_stat>~ <identifier_stat>) { <stmt_ctrl> } 
    # PREDICT = {echo}

    def parse_for_loop(self):
        current = self.peek()

        if current == 'echo':
            self.match('echo')
            self.match('(')
            for_init_node = self.parse_for_init()
            cond_stat_node = self.parse_cond_stat()
            self.match('~')
            identifier_stat_node = self.parse_identifier_stat()
            self.match(')')
            self.match('{')
            stmt_ctrl_node = self.parse_stmt_ctrl()
            self.match('}')
            return ASTNode('while_loop', children=[for_init_node, cond_stat_node, identifier_stat_node, stmt_ctrl_node])
        else:
            self.error(f"[169] Expected 'echo', got '{current}'")

    # <dowhile_loop>
    # Production 170: dowhile_loop → do {<stmt_ctrl>} cycle (<cond_stat>)~
    # PREDICT = {do}

    def parse_dowhile_loop(self):
        current = self.peek()

        if current == 'do':
            self.match('do')
            self.match('{')
            stmt_ctrl_node = self.parse_stmt_ctrl()
            self.match('}')
            self.match('cycle')
            self.match('(')
            cond_stat_node = self.parse_cond_stat()
            self.match(')')
            self.match('~')
            return ASTNode('while_loop', children=[stmt_ctrl_node, cond_stat_node])
        else:
            self.error(f"[170] Expected 'do', got '{current}'")

    # <for_init>
    # Production 171: for_init → <normal>
    # PREDICT = { int, float, char, string, bool }

    # Production 172: for_init → <identifier_stat>
    # PREDICT = { ++, --, id }

    def parse_for_init(self):
        current = self.peek()

        if current in ['int', 'float', 'char', 'string', 'bool']:
            normal_node = self.parse_normal()
            return ASTNode('for_init', children=[normal_node])
        elif current in ['++', '--'] or (current and current.startswith('id')):
            identifier_stat_node = self.parse_identifier_stat()
            return ASTNode('for_init', children=[identifier_stat_node])
        else:
            self.error(f"[170] Expected 'do', got '{current}'")


    # <function_call>
    # Production 173–182: function_call → toRise(<param_item>)
    #                                     toFall(<param_item>)
    #                                     horizon(<param_item>)
    #                                     sizeOf(<param_item>)
    #                                     toInt(<param_item>)
    #                                     toFloat(<param_item>)
    #                                     toString(<param_item>)
    #                                     toChar(<param_item>)
    #                                     toBool(<param_item>)
    #                                     waft(<param_item>,<param_item>)
    # PREDICT = {toRise | toFall | horizon | sizeOf | toInt | toFloat | toString | toChar | toBool | waft}

    def parse_function_call(self):
        current = self.peek()

        if current == 'toRise':
            self.match('toRise')
            self.match('(')
            param_item_node = self.parse_param_item()
            self.match(')')
            return ASTNode('function_call', children=[param_item_node])
        elif current == 'toFall':
            self.match('toFall')
            self.match('(')
            param_item_node = self.parse_param_item()
            self.match(')')
            return ASTNode('function_call', children=[param_item_node])
        elif current == 'horizon':
            self.match('horizon')
            self.match('(')
            param_item_node = self.parse_param_item()
            self.match(')')
            return ASTNode('function_call', children=[param_item_node])
        elif current == 'sizeOf':
            self.match('sizeOf')
            self.match('(')
            param_item_node = self.parse_param_item()
            self.match(')')
            return ASTNode('function_call', children=[param_item_node])
        elif current == 'toInt':
            self.match('toInt')
            self.match('(')
            param_item_node = self.parse_param_item()
            self.match(')')
            return ASTNode('function_call', children=[param_item_node])
        elif current == 'toFloat':
            self.match('toFloat')
            self.match('(')
            param_item_node = self.parse_param_item()
            self.match(')')
            return ASTNode('function_call', children=[param_item_node])
        elif current == 'toString':
            self.match('toString')
            self.match('(')
            param_item_node = self.parse_param_item()
            self.match(')')
            return ASTNode('function_call', children=[param_item_node])
        elif current == 'toChar':
            self.match('toChar')
            self.match('(')
            param_item_node = self.parse_param_item()
            self.match(')')
            return ASTNode('function_call', children=[param_item_node])
        elif current == 'toBool':
            self.match('toBool')
            self.match('(')
            param_item_node = self.parse_param_item()
            self.match(')')
            return ASTNode('function_call', children=[param_item_node])
        elif current == 'waft':
            self.match('waft')
            self.match('(')
            param_item1_node = self.parse_param_item()
            self.match(',')
            param_item2_node = self.parse_param_item()
            self.match(')')
            return ASTNode('function_call', children=[param_item1_node, param_item2_node])
        else:
            self.error(f"[173-182] Expected function call (toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft), got '{current}'")


    # <param_opts>
    # Production 183: param_opts → <param_list>
    # PREDICT = { (, ++, --, id, toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, int_lit, float_lit, yuh, naur, char_lit, string_lit, ! }

    # Production 184: param_opts → λ
    # PREDICT = { ) }

    def parse_param_opts(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            param_list_node = self.parse_param_list()
            return ASTNode('param_opts', children=[param_list_node])
        elif current == ')':
            return ASTNode('param_opts_empty')
        else:
            self.error(f"[183-184] Expected '(', ')', identifier, value literal, or function call, got '{current}'")

    # <param_list>
    # Production 185: param_list → <param_item> <param_tail>
    # PREDICT = { (, ++, --, id, toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, int_lit, float_lit, yuh, naur, char_lit, string_lit, ! }

    def parse_param_list(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            param_item_node = self.parse_param_item()
            param_tail_node = self.parse_param_tail()
            return ASTNode('param_opts', children=[param_item_node, param_tail_node])
        else:
            self.error(f"[185] Expected '(', identifier, value literal, or function call, got '{current}'")


    # <param_item>
    # Production 186: param_item → <expr>
    # PREDICT = { (, ++, --, id, toRise, toFall, horizon, sizeOf, toInt, toFloat, toString, toChar, toBool, waft, int_lit, float_lit, yuh, naur, char_lit, string_lit, ! }

    def parse_param_item(self):
        current = self.peek()

        if current in ['(', '++', '--', 'toRise', 'toFall', 'horizon', 'sizeOf', 'toInt', 'toFloat', 'toString', 'toChar', 'toBool', 'waft',
                        'int_lit', 'float_lit', 'yuh', 'naur','char_lit', 'string_lit', '!'] or (current and current.startswith('id')):
            expr_node = self.parse_expr()
            return ASTNode('param_item', children=[expr_node])
        else:
            self.error(f"[186] Expected '(', identifier, value literal, or function call, got '{current}'")


    # <param_tail>
    # Production 187: param_tail → , <param_list>
    # PREDICT = {,}

    # Production 188: param_tail → λ
    # PREDICT = {)}

    def parse_param_tail(self):
        current = self.peek()

        if current == ',':
            self.match(',')
            param_list_node = self.parse_param_list()
            return ASTNode('param_tail', children=[param_list_node])
        elif current == ')':
            return ASTNode('param_tail_empty')
        else:
            self.error(f"[187-188] Expected ',' or ')', got '{current}'")


    # <return_stat>
    # Production 189: return_stat → gasp <expr>
    # PREDICT = {gasp}

    # Production 190: return_stat → λ
    # PREDICT = {}}

    def parse_return_stat(self):
        current = self.peek()

        if current == 'gasp':
            self.match('gasp')
            expr_node = self.parse_expr()
            self.match('~')
            return ASTNode('return_stat', children=[expr_node])
        elif current == '}':
            return ASTNode('return_stat_empty')
        else:
            self.error(f"[189-190] Expected 'gasp' or end of program, got '{current}'")


    # def parse_data_type(self):
    #     current = self.peek()
        
    #     if current in ['int', 'float', 'char', 'string', 'bool']:
    #         data_type_value = self.current_token.value
    #         self.match(current)  # Consume the data type token
    #         return ASTNode('data_type', value=data_type_value)
    #     else:
    #         raise SyntaxError(
    #             f"Expected data type (int, float, char, string, bool), "
    #             f"got '{current}' at line {self.current_token.line}"

    #         )



