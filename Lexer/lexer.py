class Token:
    def __init__(self, type, value, line, column):
        self.type = type
        self.value = value
        self.line = line
        self.column = column
    
    def to_dict(self):
        return {
            'type': self.type,
            'value': self.value,
            'line': self.line,
            'column': self.column
        }


class Lexer:
    def __init__(self, source_code):
        self.source_code = source_code
        self.position = 0
        self.line = 1
        self.column = 1
        self.tokens = []
        
        # Token class mappings
        self.token_class = {
            '(': 'OPENPARENTHESIS',
            ')': 'CLOSEPARENTHESIS',
            '{': 'OPENCURLYBRACE',
            '}': 'CLOSECURLYBRACE',
            ';': 'SEMI-COLON',
            'float': 'FLOAT',
            'int': 'INT',
            'scan': 'SCAN',
            'print': 'PRINT',
            'if': 'IF',
            'else': 'ELSE',
            'while': 'WHILE'
        }
        
        self.keywords = ['int', 'float', 'scan', 'print', 'if', 'else', 'while']
        self.operators = ['=', '/', '-', '*', '+', '==', '!=', '<=', '<', '>=', '>']
        self.separators = ['(', ')', '{', '}', ';']
        
    def peek(self, offset=0):
        pos = self.position + offset
        if pos < len(self.source_code):
            return self.source_code[pos]
        return ''
    
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
    
    def skip_whitespace(self):
        while self.position < len(self.source_code) and self.peek().isspace():
            self.advance()
    
    def tokenize(self):
        self.tokens = []
        
        while self.position < len(self.source_code):
            self.skip_whitespace()
            
            if self.position >= len(self.source_code):
                break
            
            char = self.peek()
            start_line = self.line
            start_col = self.column
            
            # SINGLE-LINE COMMENT
            if char == '/' and self.peek(1) == '/':
                self.advance()  # skip /
                self.advance()  # skip /
                while self.peek() and self.peek() != '\n':
                    self.advance()
                continue
            
            # MULTI-LINE COMMENT
            if char == '\\' and self.peek(1) == '*':
                self.advance()  # skip \
                self.advance()  # skip *
                while self.peek() and not (self.peek() == '*' and self.peek(1) == '\\'):
                    self.advance()
                if self.peek() == '*' and self.peek(1) == '\\':
                    self.advance()  # skip *
                    self.advance()  # skip \
                continue
            
            # OPERATORS
            if char in self.operators:
                self.advance()
                # Check for two-character operators
                if self.peek() == '=' and (char + '=') in self.operators:
                    self.advance()
                    self.tokens.append(Token('OPERATOR', char + '=', start_line, start_col))
                else:
                    self.tokens.append(Token('OPERATOR', char, start_line, start_col))
                continue
            
            # SEPARATORS
            if char in self.separators:
                self.advance()
                self.tokens.append(Token(self.token_class[char], char, start_line, start_col))
                continue
            
            # IDs and KEYWORDS
            if char.isalpha() or char == '_':
                word = ''
                word += self.advance()
                
                while self.peek() and (self.peek().isalnum() or self.peek() == '_'):
                    word += self.advance()
                
                # Check for _ in the middle (after first character)
                if '_' in word[1:]:
                    self.tokens.clear()
                    self.tokens.append(Token('ERROR', 'invalid ID', start_line, start_col))
                    return self.tokens
                
                # Check if it's a keyword
                if word in self.keywords:
                    self.tokens.append(Token(self.token_class[word], word, start_line, start_col))
                else:
                    self.tokens.append(Token('IDENTIFIER', word, start_line, start_col))
                continue
            
            # INTs and FLOATs (with optional + or - prefix)
            if char.isdigit() or ((char == '+' or char == '-') and self.peek(1).isdigit()):
                number = ''
                
                # Handle sign
                if char == '+' or char == '-':
                    number += self.advance()
                
                has_dot = False
                while self.peek() and (self.peek().isdigit() or self.peek() == '.'):
                    if self.peek() == '.':
                        if has_dot:
                            break  # Second dot
                        has_dot = True
                    number += self.advance()
                
                # Check if number ends with a dot
                if number.endswith('.'):
                    self.tokens.clear()
                    self.tokens.append(Token('ERROR', 'number after dot was expected', start_line, start_col))
                    return self.tokens
                
                if has_dot:
                    self.tokens.append(Token('FLOAT', number, start_line, start_col))
                else:
                    self.tokens.append(Token('INT', number, start_line, start_col))
                continue
            
            # STRINGS
            if char == '"':
                string = ''
                string += self.advance()  # opening "
                
                while self.peek() and self.peek() != '"' and self.peek() != '\n':
                    string += self.advance()
                
                if self.peek() == '"':
                    string += self.advance()  # closing "
                    self.tokens.append(Token('STRING', string, start_line, start_col))
                else:
                    self.tokens.clear()
                    self.tokens.append(Token('ERROR', 'string was not closed', start_line, start_col))
                    return self.tokens
                continue
            
            # Unknown character
            self.tokens.clear()
            self.tokens.append(Token('ERROR', 'not an accepted character', start_line, start_col))
            return self.tokens
        
        return self.tokens