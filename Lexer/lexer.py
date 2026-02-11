from delimiters import (
    wspace_dlm,
    fun_dlm,
    term_dlm,
    num_dlm,
    singq_dlm,
    doubq_dlm,
    bool_dlm,
    id_dlm,
    ass_dlm,
    equal_dlm,
    not_dlm,
    eqto_dlm,
    rel_dlm,
    log_dlm,
    do_dlm,
    ctrl_dlm,
    colon_dlm,
    strm_dlm,
    arith_dlm,
    amper_dlm,
    sub_dlm,
    unary_dlm,
    comma_dlm,
    closecurl_dlm,
    closepare_dlm,
    closesqua_dlm,
    opencurl_dlm,
    openpare_dlm,
    opensqua_dlm
)

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
        self.id_counter = 0
        self.id_map = {}

        self.MAX_INT = 9_999_999_999
        self.MIN_INT = -9_999_999_999
        self.MAX_FLOAT = 10
        self.MAX_FLOAT_POINT = 6

    def peek(self, offset=0):
        pos = self.position + offset
        if pos < len(self.source_code):
            return self.source_code[pos]
        return ''

    def peek_backwards(self, offset=1, skip_whitespace=False):
        pos = self.position - offset

        if skip_whitespace:
            while pos >= 0:
                char = self.source_code[pos]
                if char and not char.isspace():
                    return char
                pos -= 1
            return ''

        if pos >= 0 and pos < len(self.source_code):
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
    
    # def skip_whitespace(self):
    #     while self.position < len(self.source_code) and self.peek().isspace():
    #         self.advance()
    
    def error(self, message, line, column):
        self.tokens.append(Token('ERROR', message, line, column))

    def continues_as_identifier(self):
        next_char = self.peek()
        return next_char and (next_char.isalnum() or next_char == '_')

    def check_keyword_delimiter(self, keyword_name, delimiter_func, saved_position, saved_line, saved_column, start_line, start_col):
        if self.continues_as_identifier(): 
            self.position = saved_position
            self.line = saved_line
            self.column = saved_column
            return False 
        elif delimiter_func(self.peek()):
            self.tokens.append(Token(keyword_name, keyword_name, start_line, start_col))
            return True
        else:
            char = self.peek()
            if char in ['']:
                self.error(f"expecting a valid delimiter: {keyword_name}", self.line, self.column)
                return True
            else:
                self.error(f"invalid character after '{keyword_name}' keyword: {self.peek()}", self.line, self.column)
                return True
        
    def tokenize_single(self):
        if self.td_keyword():
            return True
        if self.td_identifier():
            return True
        if self.td_number():
            return True
        if self.td_operator_structure():
            return True
        return False

    # TRANSITION DIAGRAM: Keywords/Reserved Words
    def td_keyword(self):
        start_line = self.line
        start_col = self.column
        saved_position = self.position  
        saved_line = self.line          
        saved_column = self.column
        
        char = self.peek()
        
        if not char.isalpha():
            return False
        # Check for 'a' keywords: air, atmosphere
        if char == 'a':
            self.advance()
            if self.peek() == 'i': 
                self.advance()
                if self.peek() == 'r':
                    self.advance()
                    return self.check_keyword_delimiter(
                        'air', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                    )
            elif self.peek() == 't': 
                self.advance()
                if self.peek() == 'm':
                    self.advance()
                    if self.peek() == 'o':
                        self.advance()
                        if self.peek() == 's':
                            self.advance()  
                            if self.peek() == 'p':
                                self.advance()
                                if self.peek() == 'h':
                                    self.advance()
                                    if self.peek() == 'e':
                                        self.advance()
                                        if self.peek() == 'r':
                                            self.advance()
                                            if self.peek() == 'e':
                                                self.advance()
                                                return self.check_keyword_delimiter(
                                                    'atmosphere', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                                )
        
        # Check for 'b' keywords: bool
        elif char == 'b':
            self.advance()
            if self.peek() == 'o':
                self.advance()
                if self.peek() == 'o':
                    self.advance()
                    if self.peek() == 'l':
                        self.advance()
                        return self.check_keyword_delimiter(
                            'bool', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                        )
        
        # Check for 'c' keywords: case, char, cycle
        elif char == 'c':
            self.advance()
            if self.peek() == 'a': 
                self.advance()
                if self.peek() == 's':
                    self.advance()
                    if self.peek() == 'e':
                        self.advance()
                        return self.check_keyword_delimiter(
                            'case', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                        )
            elif self.peek() == 'h':
                self.advance()
                if self.peek() == 'a':
                    self.advance()
                    if self.peek() == 'r':
                        self.advance()
                        return self.check_keyword_delimiter(
                            'char', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                        )
            elif self.peek() == 'y':
                self.advance()
                if self.peek() == 'c':
                    self.advance()
                    if self.peek() == 'l':
                        self.advance()
                        if self.peek() == 'e':
                            self.advance()
                            return self.check_keyword_delimiter(
                                'cycle', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                            )
        
        # Check for 'd' keywords: diffuse, do
        elif char == 'd':
            self.advance()
            if self.peek() == 'i':
                self.advance()
                if self.peek() == 'f':
                    self.advance()
                    if self.peek() == 'f':
                        self.advance()
                        if self.peek() == 'u':
                            self.advance()
                            if self.peek() == 's':
                                self.advance()
                                if self.peek() == 'e':
                                    self.advance()
                                    return self.check_keyword_delimiter(
                                        'diffuse', strm_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                    )
            elif self.peek() == 'o': 
                self.advance()
                return self.check_keyword_delimiter(
                    'do', do_dlm, saved_position, saved_line, saved_column, start_line, start_col
                )
        
        # Check for 'e' keywords: else, elseif, echo, exhale
        elif char == 'e':
            self.advance()
            if self.peek() == 'c': 
                self.advance()
                if self.peek() == 'h':
                    self.advance()
                    if self.peek() == 'o':
                        self.advance()
                        return self.check_keyword_delimiter(
                            'echo', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                        )
            elif self.peek() == 'l':
                self.advance()
                if self.peek() == 's':
                    self.advance()
                    if self.peek() == 'e':
                        self.advance()
                        if do_dlm(self.peek()):
                            self.tokens.append(Token('else', 'else', start_line, start_col))
                            return True
                        elif self.peek() == 'i':
                            self.advance()
                            if self.peek() == 'f':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'elseif', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
                        else:
                            self.error(f"invalid character after 'else' keyword: {self.peek()}", self.line, self.column)
                            return True
            elif self.peek() == 'x':
                self.advance()
                if self.peek() == 'h':
                    self.advance()
                    if self.peek() == 'a':
                        self.advance()
                        if self.peek() == 'l':
                            self.advance()
                            if self.peek() == 'e':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'exhale', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
        
        # Check for 'f' keywords: float, flow
        elif char == 'f':
            self.advance()
            if self.peek() == 'l':
                self.advance()
                if self.peek() == 'o':
                    self.advance()
                    if self.peek() == 'a': 
                        self.advance()
                        if self.peek() == 't':
                            self.advance()
                            return self.check_keyword_delimiter(
                                'float', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                            )
                    elif self.peek() == 'w': 
                        self.advance()
                        return self.check_keyword_delimiter(
                            'flow', ctrl_dlm, saved_position, saved_line, saved_column, start_line, start_col
                        )
        
        # Check for 'g' keywords: gasp, gust
        elif char == 'g':
            self.advance()
            if self.peek() == 'a':  
                self.advance()
                if self.peek() == 's':
                    self.advance()
                    if self.peek() == 'p':
                        self.advance()
                        return self.check_keyword_delimiter(
                            'gasp', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                        )
            elif self.peek() == 'u':
                self.advance()
                if self.peek() == 's':
                    self.advance()
                    if self.peek() == 't':
                        self.advance()
                        return self.check_keyword_delimiter(
                            'gust', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                        )
                        
        # Check for 'h' keywords: horizon
        elif char == 'h':
            self.advance()
            if self.peek() == 'o':
                self.advance()
                if self.peek() == 'r':
                    self.advance()
                    if self.peek() == 'i':
                        self.advance()
                        if self.peek() == 'z':
                            self.advance()
                            if self.peek() == 'o':
                                self.advance()
                                if self.peek() == 'n':
                                    self.advance()
                                    return self.check_keyword_delimiter(
                                        'horizon', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                    )
        
        # Check for 'i' keywords: if, inhale, int
        elif char == 'i':
            self.advance()
            if self.peek() == 'f':
                self.advance()
                return self.check_keyword_delimiter(
                    'if', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                )
            elif self.peek() == 'n':
                self.advance()
                if self.peek() == 'h': 
                    self.advance()
                    if self.peek() == 'a':
                        self.advance()
                        if self.peek() == 'l':
                            self.advance()
                            if self.peek() == 'e':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'inhale', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
                elif self.peek() == 't':  
                    self.advance()
                    return self.check_keyword_delimiter(
                        'int', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                    )
        
        # Check for 'n' keywords: naur
        elif char == 'n':
            self.advance()
            if self.peek() == 'a':
                self.advance()
                if self.peek() == 'u':
                    self.advance()
                    if self.peek() == 'r':
                        self.advance()
                        return self.check_keyword_delimiter(
                            'naur', bool_dlm, saved_position, saved_line, saved_column, start_line, start_col
                        )
            
        # Check for 'r' keywords: resist
        elif char == 'r':
            self.advance()
            if self.peek() == 'e':
                self.advance()
                if self.peek() == 's':
                    self.advance()
                    if self.peek() == 'i':
                        self.advance()
                        if self.peek() == 's':
                            self.advance()
                            if self.peek() == 't':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'resist', ctrl_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
        
        # Check for 's' keywords: sizeOf, stream, string
        elif char == 's':
            self.advance()
            if self.peek() == 'i':
                self.advance()
                if self.peek() == 'z':
                    self.advance()
                    if self.peek() == 'e':
                        self.advance()
                        if self.peek() == 'O':
                            self.advance()
                            if self.peek() == 'f':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'sizeOf', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
                                
            elif self.peek() == 't':
                self.advance()
                if self.peek() == 'r':
                    self.advance()
                    if self.peek() == 'e': 
                        self.advance()
                        if self.peek() == 'a':
                            self.advance()
                            if self.peek() == 'm':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'stream', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
                    elif self.peek() == 'i': 
                        self.advance()
                        if self.peek() == 'n':
                            self.advance()
                            if self.peek() == 'g':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'string', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
                        
        # Check for 't' keywords: toBool, toChar, toFall, toFloat, toInt, toRise, toString, waft
        elif char == 't':
            self.advance()
            if self.peek() == 'o':
                self.advance()
                if self.peek() == 'B': 
                    self.advance()
                    if self.peek() == 'o':
                        self.advance()
                        if self.peek() == 'o':
                            self.advance()
                            if self.peek() == 'l':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'toBool', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
                elif self.peek() == 'C': 
                    self.advance()
                    if self.peek() == 'h':
                        self.advance()
                        if self.peek() == 'a':
                            self.advance()
                            if self.peek() == 'r':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'toChar', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
                elif self.peek() == 'F':
                    self.advance()
                    if self.peek() == 'a':
                        self.advance()
                        if self.peek() == 'l':
                            self.advance()
                            if self.peek() == 'l':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'toFall', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
                    elif self.peek() == 'l':
                        self.advance()
                        if self.peek() == 'o':
                            self.advance()
                            if self.peek() == 'a':
                                self.advance()
                                if self.peek() == 't':
                                    self.advance()
                                    return self.check_keyword_delimiter(
                                        'toFloat', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                    )
                elif self.peek() == 'I': 
                    self.advance()
                    if self.peek() == 'n':
                        self.advance()
                        if self.peek() == 't':
                            self.advance()
                            return self.check_keyword_delimiter(
                                'toInt', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                            )
                elif self.peek() == 'R': 
                    self.advance()
                    if self.peek() == 'i':
                        self.advance()
                        if self.peek() == 's':
                            self.advance()
                            if self.peek() == 'e':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'toRise', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
                elif self.peek() == 'S':  
                    self.advance()
                    if self.peek() == 't':
                        self.advance()
                        if self.peek() == 'r':
                            self.advance()
                            if self.peek() == 'i':
                                self.advance()
                                if self.peek() == 'n':
                                    self.advance()
                                    if self.peek() == 'g':
                                        self.advance()
                                        return self.check_keyword_delimiter(
                                            'toString', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                        )
        
        # Check for 'u' keywords: universal
        elif char == 'u':
            self.advance()
            if self.peek() == 'n':
                self.advance()
                if self.peek() == 'i':
                    self.advance()
                    if self.peek() == 'v':
                        self.advance()
                        if self.peek() == 'e':
                            self.advance()
                            if self.peek() == 'r':
                                self.advance()
                                if self.peek() == 's':
                                    self.advance()
                                    if self.peek() == 'a':
                                        self.advance()
                                        if self.peek() == 'l':
                                            self.advance()
                                            return self.check_keyword_delimiter(
                                                'universal', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                            )
        
        # Check for 'v' keywords: vacuum
        elif char == 'v':
            self.advance()
            if self.peek() == 'a':
                self.advance()
                if self.peek() == 'c':
                    self.advance()
                    if self.peek() == 'u':
                        self.advance()
                        if self.peek() == 'u':
                            self.advance()
                            if self.peek() == 'm':
                                self.advance()
                                return self.check_keyword_delimiter(
                                    'vacuum', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                                )
        
        # Check for 'w' keywords: wind
        elif char == 'w':
            self.advance()
            if self.peek() == 'i':
                self.advance()
                if self.peek() == 'n':
                    self.advance()
                    if self.peek() == 'd':
                        self.advance()
                        return self.check_keyword_delimiter(
                            'wind', wspace_dlm, saved_position, saved_line, saved_column, start_line, start_col
                        )
            elif self.peek() == 'a':
                self.advance()
                if self.peek() == 'f':
                    self.advance()
                    if self.peek() == 't':
                        self.advance()
                        return self.check_keyword_delimiter(
                            'waft', fun_dlm, saved_position, saved_line, saved_column, start_line, start_col
                        )
        
        # Check for 'y' keywords: yuh
        elif char == 'y':
            self.advance()
            if self.peek() == 'u':
                self.advance()
                if self.peek() == 'h':
                    self.advance()
                    return self.check_keyword_delimiter(
                        'yuh', bool_dlm, saved_position, saved_line, saved_column, start_line, start_col
                    )
                
        self.position = saved_position
        self.line = saved_line
        self.column = saved_column
        return False
         
    # TD - Operator/Structure     
    def td_operator_structure(self):
        start_line = self.line
        start_col = self.column

        char = self.peek()
        
        if char not in '+-*/%(){}[]><=!\\:.~,@&|':
            return False
        
        if char == '+':
            self.advance()
            if arith_dlm(self.peek()):
                self.tokens.append(Token('+','+', start_line, start_col))
                return True
            elif self.peek() == '+':
                self.advance()
                if unary_dlm(self.peek()):
                    self.tokens.append(Token('++','++', start_line, start_col))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '++'", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '++': {self.peek()}", self.line, self.column)
                        return True
            elif self.peek() == '=':
                self.advance()
                if ass_dlm(self.peek()):
                    self.tokens.append(Token('+=','+=', start_line, start_col))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '+='", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '+=': {self.peek()}", self.line, self.column)
                        return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '+'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '+': {self.peek()}", self.line, self.column)
                    return True
            
        elif char == '-':
            self.advance()
            if sub_dlm(self.peek()):
                self.tokens.append(Token('-','-', start_line, start_col))
                return True
            elif self.peek() == '-':
                self.advance()
                if unary_dlm(self.peek()):
                    self.tokens.append(Token('--','--', start_line, start_col))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '--'", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '--': {self.peek()}", self.line, self.column)
                        return True
            elif self.peek() == '=':
                self.advance()
                if ass_dlm(self.peek()):
                    self.tokens.append(Token('-=','-=', start_line, start_col))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '-='", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '-=': {self.peek()}", self.line, self.column)
                        return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '-'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '-': {self.peek()}", self.line, self.column)
                    return True
            
        elif char == '*':
            self.advance()
            if arith_dlm(self.peek()):
                self.tokens.append(Token('*','*', start_line, start_col))
                return True
            elif self.peek() == '=':
                self.advance()
                if ass_dlm(self.peek()):
                    self.tokens.append(Token('*=','*=', start_line, start_col))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '*='", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '*=': {self.peek()}", self.line, self.column)
                        return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '*'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '*': {self.peek()}", self.line, self.column)
                    return True
            
        elif char == '/':
            self.advance()
            if arith_dlm(self.peek()):
                self.tokens.append(Token('/','/', start_line, start_col))
                return True
            elif self.peek() == '=':
                self.advance()
                if ass_dlm(self.peek()):
                    self.tokens.append(Token('/=','/=', start_line, start_col))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '/='", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '/=': {self.peek()}", self.line, self.column)
                        return True
            elif self.peek() == '/':
                self.advance()
                while self.peek() and self.peek() != '\n':
                    self.advance()
                return True
            elif self.peek() == '~':
                self.advance()
                while self.peek() and not (self.peek() == '~' and self.peek(1) == '/'):
                    self.advance()
                if self.peek() == '~' and self.peek(1) == '/':
                    self.advance()
                    self.advance()
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '/'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '/': {self.peek()}", self.line, self.column)
                    return True
            
        elif char == '%':
            self.advance()
            if arith_dlm(self.peek()):
                self.tokens.append(Token('%','%', self.line, self.column))
                return True
            elif self.peek() == '=':
                self.advance()
                if ass_dlm(self.peek()):
                    self.tokens.append(Token('%=','%=', self.line, self.column))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '%='", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '%=': {self.peek()}", start_line, start_col)
                        return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '%'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '%': {self.peek()}", start_line, start_col)
                    return True
        
        # structure - (),{},[]
        elif char == '(':
            self.advance()
            if openpare_dlm(self.peek()):
                self.tokens.append(Token('(','(', self.line, self.column))
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '('", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '(': {self.peek()}", start_line, start_col)
                    return True
            
        elif char == ')':
            self.advance()
            if closepare_dlm(self.peek()):
                self.tokens.append(Token(')',')', self.line, self.column))
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after ')'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after ')': {self.peek()}", start_line, start_col)
                    return True
        
        elif char == '{':
            self.advance()
            if opencurl_dlm(self.peek()):
                self.tokens.append(Token('{','{', self.line, self.column))
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '{'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '{{': {self.peek()}", start_line, start_col)
                    return True

        elif char == '}':
            self.advance()
            
            if closecurl_dlm(self.peek()):
                self.tokens.append(Token('}','}', self.line, self.column))
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '}'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '}}': {self.peek()}", start_line, start_col)
                    return True
            
        elif char == '[':
            self.advance()
            if opensqua_dlm(self.peek()):
                self.tokens.append(Token('[','[', self.line, self.column))
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '['", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '[': {self.peek()}", start_line, start_col)
                    return True

        elif char == ']':
            self.advance()
            if closesqua_dlm(self.peek()):
                self.tokens.append(Token(']',']', self.line, self.column))
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after ']'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after ']': {self.peek()}", start_line, start_col)
                    return True
            
        # operators - >, >=, <, <=, =, ==, !, !=
        elif char == '>':
            self.advance()
            if rel_dlm(self.peek()):
                self.tokens.append(Token('>','>', self.line, self.column))
                return True
            elif self.peek() == '=':
                self.advance()
                if rel_dlm(self.peek()):
                    self.tokens.append(Token('>=','>=', self.line, self.column))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '>='", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '>=': {self.peek()}", start_line, start_col)
                        return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '>'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '>': {self.peek()}", start_line, start_col)
                    return True
            
        elif char == '<':
            self.advance()
            if rel_dlm(self.peek()):
                self.tokens.append(Token('<','<', self.line, self.column))
                return True
            elif self.peek() == '=':
                self.advance()
                if rel_dlm(self.peek()):
                    self.tokens.append(Token('<=','<=', self.line, self.column))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '<='", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '<=': {self.peek()}", start_line, start_col)
                        return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '<'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '<': {self.peek()}", start_line, start_col)
                    return True
            
        elif char == '=':
            self.advance()
            if equal_dlm(self.peek()):
                self.tokens.append(Token('=','=', self.line, self.column))
                return True
            elif self.peek() == '=':
                self.advance()
                if eqto_dlm(self.peek()):
                    self.tokens.append(Token('==','==', self.line, self.column))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '=='", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '==': {self.peek()}", start_line, start_col)
                        return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '='", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '=': {self.peek()}", start_line, start_col)
                    return True
            
        elif char == '!':
            self.advance()
            if not_dlm(self.peek()):
                self.tokens.append(Token('!','!', self.line, self.column))
                return True
            elif self.peek() == '=':
                self.advance()
                if eqto_dlm(self.peek()):
                    self.tokens.append(Token('!=','!=', self.line, self.column))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '!='", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '!=': {self.peek()}", start_line, start_col)
                        return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '!'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '!': {self.peek()}", start_line, start_col)
                    return True
            
        elif char == '&':
            self.advance()
            if amper_dlm(self.peek()):
                self.tokens.append(Token('&','&', self.line, self.column))
                return True
            elif self.peek() == '&':
                self.advance()
                if log_dlm(self.peek()):
                    self.tokens.append(Token('&&','&&', self.line, self.column))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '&&'", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '&&': {self.peek()}", start_line, start_col)
                        return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '&'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '&': {self.peek()}", start_line, start_col)
                    return True
            
        elif char == '|':
            self.advance()
            if self.peek() == '|':
                self.advance()
                if log_dlm(self.peek()):
                    self.tokens.append(Token('||','||', self.line, self.column))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['']:
                        self.error("expecting a valid delimiter after '||'", self.line, self.column)
                        return True
                    else:
                        self.error(f"invalid character after '||': {self.peek()}", start_line, start_col)
                        return True
            else:
                self.error(f"'|' is not recognized. (Did you mean '||'?)", self.line, self.column)
            return True
        
        # structure - :, ., ~, ,
        elif char == ':':
            self.advance()
            if colon_dlm(self.peek()):
                self.tokens.append(Token(':',':', self.line, self.column))
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after ':'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after ':': {self.peek()}", start_line, start_col)
                    return True
            
        elif char == '.':
            self.advance()
            if self.peek().isalpha():
                self.tokens.append(Token('.','.', self.line, self.column))
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '.'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '.': {self.peek()}", start_line, start_col)
                    return True
            
        elif char == '~':
            self.advance()
            if term_dlm(self.peek()):
                self.tokens.append(Token('~','~', self.line, self.column))
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after '~'", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after '~': {self.peek()}", start_line, start_col)
                    return True
            
        elif char == ',':
            self.advance()
            if comma_dlm(self.peek()):
                self.tokens.append(Token(',',',', self.line, self.column))
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['']:
                    self.error("expecting a valid delimiter after ','", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after ',': {self.peek()}", start_line, start_col)
                    return True

    # TRANSITION DIAGRAM: Character Literal
    def td_char(self):
        if self.peek() != '\'':
            return False
        
        start_line = self.line
        start_col = self.column
        char_content = ''

        self.advance()

        while self.peek() and self.peek() not in '\'"\n':
            if ord(self.peek()) < 128:
                char_content += self.advance()
            else:
                self.advance()
        if self.peek() == '\'':
            self.advance()
            if len(char_content) == 0 or len(char_content) == 1:
                if singq_dlm(self.peek()):
                    self.tokens.append(Token('char_lit', f"'{char_content}'", start_line, start_col))
                    return True
                else:
                    peekChar = self.peek()
                    if peekChar in ['', '\n']: 
                        self.error(f"expecting a valid delimiter: '{char_content}'", self.line, self.column)
                        return True
                    else:
                        self.error(f'invalid character after \'{char_content}\': {self.peek()}', self.line, self.column)
                        return True
            else:
                if singq_dlm(self.peek()):
                    self.error(f"expected none or exactly one character between single quotes: '{char_content}'", start_line, start_col)
                    return True
                else:
                    self.error(f"expected none or exactly one character between single quotes: '{char_content}'", start_line, start_col)
                    peekChar = self.peek()
                    if peekChar in ['', '\n']:
                        self.error(f"expecting a valid delimiter: '{char_content}'", self.line, self.column)
                        return True
                    else:
                        self.error(f'invalid character after \'{char_content}\': {self.peek()}', self.line, self.column)
                        return True
        else:
            if char_content == "":
                self.error('unterminated single quote', start_line, start_col)
                return True
            else:
                self.error(f'unterminated single quote: \'{char_content}', start_line, start_col)
                return True
        
    # TRANSITION DIAGRAM: String Literal
    def td_string(self):
        if self.peek() != '"':
            return False
        
        start_line = self.line
        start_col = self.column
        string_content = ""

        self.advance()

        while self.peek() and self.peek() not in '\'"\n':
            self.peek()
            if ord(self.peek()) < 128:
                string_content += self.advance()
            else:
                self.advance()
        if self.peek() == '"':
            self.advance()
            if doubq_dlm(self.peek()):
                if string_content or not any(t.type == 'string_lit' for t in self.tokens[-5:]):
                    self.tokens.append(Token('string_lit', f'"{string_content}"', start_line, start_col))
                return True
            else:
                peekChar = self.peek()
                if peekChar in ['', '\n']:
                    self.error(f'expecting a valid delimiter: "{string_content}"', self.line, self.column)
                    return True
                else:
                    self.error(f'invalid character after "{string_content}": {self.peek()}', self.line, self.column)
                    return True
        else:
            if string_content == "":
                self.error('unterminated double quote', start_line, start_col)
                return True
            else:
                self.error(f'unterminated double quote: "{string_content}', start_line, start_col)
                return True
        
    # TRANSITION DIAGRAM: Identifiers
    def td_identifier(self):
        start_line = self.line
        start_col = self.column
        id_content = ""
        
        first_char = self.peek()
        
        if not first_char.isalpha():
            return False
        while self.peek() and (self.peek().isalnum() or self.peek() == '_'):
            id_content += self.advance()
        if len(id_content) > 15:
            self.error(f"'{id_content}' exceeds max length of 15 characters", start_line, start_col)
            return True
        if not id_dlm(self.peek()):
            peekChar = self.peek()
            if peekChar in ['', '\n']:
                self.error(f"expecting a valid delimiter: {id_content}", self.line, self.column)
                return True
            else:
                self.error(f"invalid character after '{id_content}': {self.peek()}", self.line, self.column)
                return True
        if id_content not in self.id_map:
            self.id_counter += 1
            self.id_map[id_content] = f"id{self.id_counter}"
        token_id = self.id_map[id_content]
        self.tokens.append(Token(token_id, id_content, start_line, start_col))
        return True

    # TRANSITION DIAGRAM: Invalid Identifier (starts with underscore)
    def td_invalid_identifier(self):
        if self.peek() != '_':
            return False
        
        start_line = self.line
        start_col = self.column
        illegal_sequence = self.advance()
        
        while self.peek() and (self.peek().isalnum() or self.peek() == '_'):
            illegal_sequence += self.advance()
        
        self.error(f'invalid leading character (underscore): {illegal_sequence}', start_line, start_col)
        return True
    
    # TRANSITION DIAGRAM: Number Literal
    def td_number(self):
        start_line = self.line
        start_col = self.column
        saved_pos = self.position
        saved_line = self.line
        saved_col = self.column
        char = self.peek()
        number = ''
        has_dot = False
        dot_count = 0

        if char == '-':            
            number += self.advance()
            char = self.peek()
        
        if not char.isdigit():
            if len(number) > 0:
                self.position = saved_pos
                self.line = saved_line
                self.column = saved_col
            return False
        
        while self.peek() and self.peek().isdigit():
            number += self.advance()
        
        if self.peek() == '.':
            invalid_sequence = number
            while self.peek() and (self.peek().isdigit() or self.peek() == '.'):
                char = self.advance()
                invalid_sequence += char
                if char == '.':
                    dot_count += 1
                elif char.isdigit() and dot_count == 1:
                    has_dot = True
            
            if dot_count > 1:
                self.error(f'invalid number literal with multiple decimal points: {invalid_sequence}', start_line, start_col)
                self.error(f'number not expected after additional dots: {invalid_sequence}', start_line, start_col)
                return True
            
            if dot_count == 1 and not has_dot:
                self.error('expected digit after dot (.)', start_line, start_col)
                return True
 
            number = invalid_sequence
        
        # letters and number mixed
        next_char = self.peek()
        if next_char and (next_char.isalpha() or next_char == '_'):
            illegal_sequence = ""
            while self.peek() and (self.peek().isalnum() or self.peek() == '_'):
                illegal_sequence += self.advance()
            illegal_lexeme = number + illegal_sequence
            self.error(f'invalid number literal: {illegal_lexeme}', start_line, start_col)
            # self.error(f'invalid leading character (digit): {illegal_lexeme}', start_line, start_col)
            return True
        
        parts = number.split('.')
        integer_part = parts[0].lstrip('+-')
        
        if has_dot:
            if len(integer_part) > self.MAX_FLOAT:
                self.error(f'{number} exceeds maximum digits before decimal of {self.MAX_FLOAT}', start_line, start_col)
                return True
            decimal_part = parts[1]
            if len(decimal_part) > self.MAX_FLOAT_POINT:
                self.error(f'{number} exceeds maximum decimal places of {self.MAX_FLOAT_POINT}', start_line, start_col)
                return True
            if num_dlm(self.peek()):
                self.tokens.append(Token('float_lit', number, start_line, start_col))
            else:
                peekChar = self.peek()
                if peekChar in ['', '\n']:
                    self.error(f"expecting a valid delimiter: {number}", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after {number}: {self.peek()}", start_line, start_col)
                    return True
        else:
            if len(integer_part) > len(str(self.MAX_INT)):
                self.error(f'{number} exceeds maximum of 10 digits', start_line, start_col)
                return True
            if num_dlm(self.peek()):
                self.tokens.append(Token('int_lit', number, start_line, start_col))
            else:
                peekChar = self.peek()
                if peekChar in ['', '\n']:
                    self.error(f"expecting a valid delimiter: {number}", self.line, self.column)
                    return True
                else:
                    self.error(f"invalid character after {number}: {self.peek()}", start_line, start_col)
                    return True
        return True

    # Main tokenization function
    def tokenize(self):
        self.tokens = []
        while self.position < len(self.source_code):
            # self.skip_whitespace()
            
            if self.position >= len(self.source_code):
                break
            
            start_line = self.line
            start_col = self.column
            char = self.peek()

            if char.isspace():
                while self.peek() and self.peek().isspace():
                    self.advance()
                continue 
            if char in '-':
                prev_char = self.peek_backwards(skip_whitespace=True)
                if prev_char and (prev_char.isalnum() or prev_char in ')]}'):
                    if self.td_operator_structure():
                        continue
                else:
                    if self.td_number():
                        continue
                    if self.td_operator_structure():
                        continue   
            if self.td_string():
                continue
            if self.td_char():
                continue
            if self.td_number():
                continue
            if self.td_keyword():
                continue
            if self.td_identifier():
                continue
            if self.td_operator_structure():
                continue
            if self.td_invalid_identifier():
                continue
            unknown = self.advance()
            self.tokens.append(Token('ERROR', f'"{unknown}" is not recognized', start_line, start_col))
        
        return self.tokens