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
nozenum = {'1','2','3','4','5','6','7','8','9'}
nums = {'0'} | nozenum
arithmetic = {'+','-','*','/','%'}
relational = {'<','>'}
logical = {'&','|'}
operator = arithmetic | relational | logical | {'=', '!'}
wspace = {' ', '\n', '\t'}

# DELIMITERS
def wspace_dlm(char):
    if char == '':
        return False
    return (char in wspace)
    
def fun_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char == '(')
    
def term_dlm(char):
    if char == '':
        return False
    return (char in wspace or
            char.isalpha() or
            char in '})')
    
def num_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char in operator or
            char in ',~)]}:')
    
def singq_dlm(char):
    if char == '':
        return False
    return (char in wspace or
            char in '=,~)}:&')
    
def doubq_dlm(char):
    if char == '':
        return False
    return (char in wspace or
            char in '=,~&)}')

def bool_dlm(char):
    if char == '':
        return False
    return (char in wspace or
            char in '~,)}' or
            char in logical)

def id_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char in operator or
            char in '(),.~[]{}')

def ass_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalnum() or
            char in '({-')

def equal_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalnum() or
            char in '("{-')

def not_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char == '(')

def eqto_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalnum() or
            char in '-"\'(')

def rel_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalnum() or
            char in '-(\'')

def log_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalnum() or
            char in '-(')

def do_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char == '{')

def ctrl_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char == '~')

def colon_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalpha())

def strm_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char == ':')

def arith_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalnum() or
            char in '(\'-')

def amper_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char in '"\'')

def sub_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalnum() or
            char in '(\'')

def unary_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalpha() or
            char in '~)')

def comma_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalnum() or
            char in '"\'-{')

def closecurl_dlm(char):
    if not char:
        return True
    return (char in wspace or 
            char.isalpha() or
            char in '~},')

def closepare_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char in operator or
            char in '~{})],')

def closesqua_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char in operator or
            char in ',~)[')

def opencurl_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalnum() or
            char in '"\'{}-')

def openpare_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalnum() or
            char in '"\'()+-!')

def opensqua_dlm(char):
    if char == '':
        return False
    return (char in wspace or 
            char.isalnum() or
            char in '(]')
