class Token:
    def __init__(self, type, value, line, column):
        self.type = type
        self.value = value
        self.line = line
        self.column = column
        self.is_error = (type == 'ERROR')
    
    def to_dict(self):
        return {
            'type': self.type,
            'value': self.value,
            'line': self.line,
            'column': self.column,
            'is_error': self.is_error
        }


class Lexer:
    def __init__(self, source_code):
        self.source_code = source_code
        self.position = 0
        self.line = 1
        self.column = 1
        self.tokens = []
        self.string_buffer = ""
        self.in_string_literal = False
        self.id_counter = 0
        self.id_map = {}

        self.MAX_INT = 9_999_999_999
        self.MIN_INT = -9_999_999_999
        self.MAX_FLOAT = 10
        self.MAX_FLOAT_POINT = 6
        
        self.token_class = {
            'atmosphere': 'atmosphere', 'air': 'air', 'vacuum': 'vacuum', 'gasp': 'gasp',
            'universal': 'universal', 'wind': 'wind', 'int': 'int', 'float': 'float',
            'char': 'char', 'string': 'string', 'bool': 'bool', 'yuh': 'yuh',
            'naur': 'naur', 'inhale': 'inhale', 'exhale': 'exhale', 'if': 'if',
            'else': 'else', 'elseif': 'elseif', 'stream': 'stream', 'case': 'case',
            'diffuse': 'diffuse', 'echo': 'echo', 'cycle': 'cycle', 'do': 'do',
            'resist': 'resist', 'flow': 'flow', 'gust': 'gust', 'horizon': 'horizon',
            'sizeOf': 'sizeOf', 'toBool': 'toBool', 'toChar': 'toChar', 'toFall': 'toFall',
            'toFloat': 'toFloat', 'toInt': 'toInt', 'toRise': 'toRise', 'toString': 'toString',
            '{': '{', '}': '}', '[': '[', ']': ']', '(': '(', ')': ')',
            ':': ':', '.': '.', ',': ',', '~': '~', '@': '@'
        }
        
        self.keywords = ['atmosphere', 'air', 'vacuum', 'gasp', 'universal', 'wind',
                             'int', 'float', 'char', 'string', 'bool', 'yuh', 'naur',
                             'inhale', 'exhale', 'if', 'else', 'elseif', 'stream',
                             'case', 'diffuse', 'echo', 'cycle', 'do', 'resist',
                             'flow', 'gust', 'horizon', 'sizeOf', 'toBool', 'toChar',
                             'toFall', 'toFloat', 'toInt', 'toRise', 'toString']
        self.operators = ['=', '!', '+', '-', '*', '/', '%', '<', '>', '&', '|', '==', '!=', '+=', '-=', '*=', '/=', '%=', '<=', '>=', '&&', '||', '++', '--']
        self.structures = ['{', '}', '(', ')', '[', ']', ':', '.', ',', '~', '@']
        self.escape_sequences = {'\\': '\\', '"': '"', '\'': '\'', '@': '@', 'n': '\n', 't': '\t'}

    # peek current character without consuming
    def peek(self, offset=0):
        pos = self.position + offset
        if pos < len(self.source_code):
            return self.source_code[pos]
        return ''
    
    # consume (get?) current position
    def advance(self):
        if self.position < len(self.source_code):
            char = self.source_code[self.position]
            self.position += 1
            if char == '\n':
                self.line += 1
                self.column = 1
            else:
                self.column += 1
            return char
        return ''
    
    # skip spaces
    def skip_whitespace(self):
        while self.position < len(self.source_code) and self.peek().isspace():
            self.advance()
    
    # Record an error token
    def error(self, message, line, column):
        self.tokens.append(Token('ERROR', message, line, column))
    
    # lexing string_lits
    def lex_string(self):
        start_line = self.line
        start_col = self.column
        self.advance()
        self.string_buffer = ""
        
        while self.peek() and self.peek() != '"' and self.peek() != '\n':
            char = self.peek()
            
            if char == '\\':
                self.advance()
                escape_char = self.peek()
                
                if escape_char in self.escape_sequences:
                    self.advance()
                    actual_char = self.escape_sequences[escape_char]
                    self.string_buffer += actual_char
                else:
                    self.error(f'Invalid escape sequence in string: \\{escape_char}', self.line, self.column)
                    self.advance()
            else:
                self.string_buffer += self.advance()
                continue
                
        if self.peek() == '"':
            self.advance()
            self.tokens.append(Token('string_lit', f'"{self.string_buffer}"', start_line, start_col))
            
        else:
            self.error('string was not closed', start_line, start_col)

    # tokenization func
    def tokenize(self):
        self.tokens = []
        
        while self.position < len(self.source_code):
            self.skip_whitespace()
            
            if self.position >= len(self.source_code):
                break
            
            char = self.peek()
            start_line = self.line
            start_col = self.column
            
            # single comment
            if char == '/' and self.peek(1) == '/':
                self.advance()
                self.advance()
                while self.peek() and self.peek() != '\n':
                    self.advance()
                continue

            # multi-line comment
            if char == '/' and self.peek(1) == '~':
                self.advance()
                self.advance()
                while self.peek() and not (self.peek() == '~' and self.peek(1) == '/'):
                    self.advance()
                if self.peek() == '~' and self.peek(1) == '/':
                    self.advance()
                    self.advance()
                continue
            
            # tokenize operators (even two-char-ops)
            if char in self.operators:
                lexeme = char
                self.advance()

                possible_two_char = lexeme + self.peek()
                if possible_two_char in self.operators:
                    lexeme = possible_two_char
                    self.advance()
                
                self.tokens.append(Token(lexeme, lexeme, start_line, start_col))
                continue
            
            # tokenize structures (e.g. {, }, (, ), etc.)
            if char in self.structures:
                self.advance()
                self.tokens.append(Token(self.token_class[char], char, start_line, start_col))
                continue
            
            # tokenize string_lits
            if char == '"':
                self.lex_string()
                continue
            
            # tokenize char_lits
            if char == '\'':
                self.advance()
                char_content = ''

                # tokenize escape sequences in char literals
                if self.peek() == '\\':
                    self.advance()
                    escape_char = self.peek()
                    if escape_char in self.escape_sequences:
                        self.advance()
                        char_content = self.escape_sequences[escape_char]
                    else:
                        self.error(f'invalid escape sequence in char: \\{escape_char}', self.line, self.column)
                        self.advance()
                elif self.peek() and self.peek() != '\'' and self.peek() != '\n':
                    char_content = self.advance()
                
                if self.peek() == '\'':
                    self.advance()
                    if len(char_content) == 0 or len(char_content) == 1:
                        self.tokens.append(Token('char_lit', f"'{char_content}'", start_line, start_col))
                    else:
                        self.error('character literal must contain none or exactly one character', start_line, start_col)
                else:
                    self.error('character literal was not closed', start_line, start_col)
                continue 

            # tokenize number literals (int_lit and float_lit)
            if char.isdigit() or ((char == '+' or char == '-') and self.peek(1).isdigit()):
                number = ''
                start_line = self.line
                start_col = self.column
                
                if char == '+' or char == '-':  
                    number += self.advance()
                
                has_dot = False
                while self.peek() and (self.peek().isdigit() or self.peek() == '.'):
                    if self.peek() == '.':
                        if has_dot:
                            self.error('multiple dot (.) found in number literal', self.line, self.column)
                            while self.peek() and not self.peek().isspace():
                                self.advance()
                            return self.tokens
                        has_dot = True
                    number += self.advance()
                
                if number.endswith('.'):
                    self.error('number after dot (.) was expected', start_line, start_col)
                    continue
                
                if self.peek() and self.peek().isalpha():
                    illegal_sequence = ""
                    while self.peek() and (self.peek().isalnum() or self.peek() == '_'):
                        illegal_sequence += self.advance()
                    illegal_lexeme = number + illegal_sequence
                    self.error(f'invalid number literal: {illegal_lexeme}', start_line, start_col)
                    continue
                
                parts = number.split('.')
                integer_part = parts[0].lstrip('+-')
                if has_dot:
                    if len(integer_part) > self.MAX_FLOAT:
                        self.error(f'float literal exceeds max digits before decimal of {self.MAX_FLOAT}: {number}', start_line, start_col)
                        continue  
                    
                    decimal_part = parts[-1]
                    if len(decimal_part) > self.MAX_FLOAT_POINT:
                        self.error(f'float literal exceeds max decimal places of {self.MAX_FLOAT_POINT}: {number}', start_line, start_col)
                        continue

                    self.tokens.append(Token('float_lit', number, start_line, start_col))
                else:
                    abs_value = number.lstrip('+-')
                    if len(abs_value) > len(str(self.MAX_INT)):
                        self.error(f'integer literal exceeds maximum magnitude (10 digits): {number}', start_line, start_col)
                        continue

                    try:
                        num_value = int(number)
                        if num_value > self.MAX_INT or num_value < self.MIN_INT:
                            self.error(f'integer literal value is out of range ({self.MIN_INT} to {self.MAX_INT}): {number}', start_line, start_col)
                            continue
                    except ValueError:
                        self.error(f'invalid integer literal: {number}', start_line, start_col)
                        continue
                    self.tokens.append(Token('int_lit', number, start_line, start_col))
                continue

            # tokenize identifier names
            if char.isalpha():
                start_line = self.line
                start_col = self.column  
                word = self.advance()
                
                while self.peek() and (self.peek().isalnum() or self.peek() == '_'):
                    word += self.advance()

                if len(word) > 15:
                    self.error(f"identifier name exceeds max length of 15 characters: '{word}'", start_line, start_col)
                    word = word[:15] 
                    continue
                
                if word in self.keywords:
                    self.tokens.append(Token(self.token_class[word], word, start_line, start_col))
                else:
                    if word not in self.id_map:
                        self.id_counter += 1
                        self.id_map[word] = f"id{self.id_counter}"
                    token_value = self.id_map[word]    
                    self.tokens.append(Token(token_value, word, start_line, start_col))
                continue

            # unrecognized character
            unknown = self.advance()
            self.tokens.append(Token('ERROR', f'"{unknown}" is not recognized', start_line, start_col))
        
        return self.tokens