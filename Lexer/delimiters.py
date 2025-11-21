# REGULAR DEFINITION
# lets_up       =>      isupper()
# lets_down     =>      islower()
# lets          =>      isalpha()
# zero          =>      0
# alphanum      =>      isalpha() && isdigit()
# negative      =>      -
# equal         =>      isequal()
# not           =>      !
# escape        =>      \
# call          =>      @
# ascii         =>      odr(char) < 128
# space         =>      isspace()
nozenum = {'1','2','3','4','5','6','7','8','9'}
nums = {'0'} | nozenum
arithmetic = {'+','-','*','/','%'}
relational = {'<','>'}
logical = {'&','|'}
operator = arithmetic | relational | logical | {'=', '!'}


# DELIMITERS
def spc_only_dlm(char):
    if not char:
        return True
    return (char.isspace())
    
def fun_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char == '(')
    
def term_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char == '\n' or
            char.isalpha() or
            char == '}')
    
def num_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char in operator or
            char == '\n' or
            char in ',~)]}:')
    
def singq_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char == '=' or
            char in ',~)}:')
    
def doubq_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char == '=' or
            char in ',~+)}')
    
def string_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char == '\n' or
            char in '"@\\' or
            (ord(char) < 128) and char not in '\'')        # all ascii char except '

def bool_dlm(char):
    if not char:
        return True
    return (char.isspace() or
            char in '~,)' or
            char in logical)

def id_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char in operator or
            char == '\n' or
            char in '(),.~[]{}')

def ass_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalnum() or
            char in '({-')

def equal_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalnum() or
            char in '("{-')

def not_dlm(char):
    if not char:
        return True
    return (char.isalpha() or
            char == '(')

def eqto_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalnum() or
            char in '"\'(-')

def rel_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalnum() or
            char in '-(\'')

def log_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalnum() or
            char in '-(')

def do_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char == '{')

def ctrl_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char == '~')

def colon_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalpha() or
            char == '\n')

def strm_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char == ':')

def arith_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalnum() or
            char in '(\'-')

def amper_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char in '"\'')

def sub_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalnum() or
            char in '(\'')

def unary_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalpha() or
            char in '~)')

def comma_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalnum() or
            char in '"\'-{')

def closecurl_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalpha() or
            char in '~},' or
            char == '\n')

def closepare_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char in operator or
            char == '\n' or
            char in '~}{)],')

def closesqua_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char in operator or
            char in ',~)[')

def opencurl_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalnum() or
            char == '\n' or
            char in '"\'}{')

def openpare_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalnum() or
            char == '\n' or
            char in '"\'()-!')

def opensqua_dlm(char):
    if not char:
        return True
    return (char.isspace() or 
            char.isalnum() or
            char in '(]')