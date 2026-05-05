from flask import Flask, render_template, request, jsonify
import uuid
from lexer import Lexer
from parser import Parser
from semantic import SemanticAnalyzer
from interpreter import InterpreterError
from tac import TACGenerator
from tac_vm import TACVM
from codegen_c import generate_c_program

app = Flask(__name__)

# In-memory terminal sessions (per browser tab/user session)
_SESSIONS = {}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/tokenize', methods=['POST'])
def tokenize():
    data = request.get_json()
    source_code = data.get('code', '')
    
    lexer = Lexer(source_code)
    tokens = lexer.tokenize()
    
    # Convert tokens to dictionaries for JSON response
    tokens_dict = [token.to_dict() for token in tokens]
    
    # Separate errors for the error section
    errors = [t for t in tokens_dict if t['is_error']]
    
    return jsonify({
        'all_tokens': tokens_dict,  # Send all tokens including errors
        'errors': errors
    })

@app.route('/parse', methods=['POST'])
def parse():
    data = request.get_json()
    source_code = data.get('code', '')

    # Tokenizer
    lexer = Lexer(source_code)
    tokens = lexer.tokenize()
    
    tokens_dict = [token.to_dict() for token in tokens]
    lexical_errors = [t for t in tokens_dict if t['is_error']]

    # Lexical Errors
    if lexical_errors:
        return jsonify({
            'success': False,
            'error': 'Cannot parse: Lexical Impostors must be ejected first.',
            'all_tokens': tokens_dict,
            'lexical_errors': lexical_errors, 
            'syntax_errors': []
        })

    # Parsley
    valid_tokens = [t for t in tokens if not t.is_error]
    parser = Parser(valid_tokens)
    ast, syntax_errors = parser.parse()

    syntax_errors_dict = [err.to_dict() for err in syntax_errors]

    if syntax_errors:
        return jsonify({
            'success': False,
            'all_tokens': tokens_dict,  
            'lexical_errors': [],
            'syntax_errors': syntax_errors_dict,
            'semantic_errors': []
        })

    # Semantic Analysis
    semantic_errors = []
    semantic_warnings = []
    if ast:
        analyzer = SemanticAnalyzer(ast, valid_tokens)
        semantic_errors = [err.to_dict() for err in analyzer.analyze()]
        semantic_warnings = [w.to_dict() for w in analyzer.warnings]

    return jsonify({
        'success': True if not semantic_errors else False,
        'message': 'Parsing successful!' if not semantic_errors else 'Semantic errors found.',
        'all_tokens': tokens_dict, 
        'lexical_errors': [],
        'syntax_errors': [],     
        'semantic_errors': semantic_errors,
        'semantic_warnings': semantic_warnings,
        'ast': ast.to_dict() if ast else None
    })


@app.route('/run', methods=['POST'])
def run_code():
    data = request.get_json(silent=True) or {}
    source_code = data.get('code', '') or ''

    lexer = Lexer(source_code)
    tokens = lexer.tokenize()
    tokens_dict = [token.to_dict() for token in tokens]
    lexical_errors = [t for t in tokens_dict if t['is_error']]

    if lexical_errors:
        return jsonify({
            'success': False,
            'stage': 'lexical',
            'all_tokens': tokens_dict,
            'lexical_errors': lexical_errors,
            'syntax_errors': [],
            'semantic_errors': [],
            'semantic_warnings': [],
            'terminal': None,
        })

    valid_tokens = [t for t in tokens if not t.is_error]
    parser = Parser(valid_tokens)
    ast, syntax_errors = parser.parse()
    syntax_errors_dict = [err.to_dict() for err in syntax_errors]

    if syntax_errors:
        return jsonify({
            'success': False,
            'stage': 'syntax',
            'all_tokens': tokens_dict,
            'lexical_errors': [],
            'syntax_errors': syntax_errors_dict,
            'semantic_errors': [],
            'semantic_warnings': [],
            'terminal': None,
        })

    analyzer = SemanticAnalyzer(ast, valid_tokens)
    semantic_results = analyzer.analyze()
    semantic_errors = [err.to_dict() for err in semantic_results if getattr(err, "message", None)]
    semantic_warnings = [w.to_dict() for w in analyzer.warnings]

    if semantic_errors:
        return jsonify({
            'success': False,
            'stage': 'semantic',
            'all_tokens': tokens_dict,
            'lexical_errors': [],
            'syntax_errors': [],
            'semantic_errors': semantic_errors,
            'semantic_warnings': semantic_warnings,
            'terminal': None,
        })

    session_id = uuid.uuid4().hex
    interp = TACVM(analyzer, valid_tokens, ast_root=ast)

    runtime_errors = []
    tac_strings = []
    try:
        tac_code = TACGenerator(ast, analyzer).generate()
        print("\n===== TAC START =====")
        for idx, instr in enumerate(tac_code):
            print(f"{idx:04d}: {instr}")
        print("===== TAC END =====\n")

        # Also return TAC to the browser so it can be logged in the console.
        tac_strings = [str(instr) for instr in tac_code]

        c_code = generate_c_program(tac_code, analyzer)
        print("\n===== C CODE START =====")
        print(c_code)
        print("===== C CODE END =====\n")

        interp.run_tac(tac_code)
    except InterpreterError as e:
        runtime_errors.append(e.to_dict())
    except Exception as e:
        runtime_errors.append({'message': str(e), 'line': 0, 'column': 0})

    _SESSIONS[session_id] = interp

    output_list = getattr(interp, 'output', None)
    if not isinstance(output_list, list):
        output_list = []
    output_str = ''.join(str(p) for p in output_list)

    term = {
        'session_id': session_id,
        'output': output_str,
        'waiting_for_input': bool(interp.waiting_for_input),
        'prompt': interp.input_request.prompt if interp.input_request else '',
        'runtime_errors': runtime_errors,
    }

    return jsonify({
        'success': True if not runtime_errors else False,
        'stage': 'runtime',
        'all_tokens': tokens_dict,
        'lexical_errors': [],
        'syntax_errors': [],
        'ast': ast.to_dict() if ast else None,
        'tac': tac_strings,
        'semantic_errors': [],
        'semantic_warnings': semantic_warnings,
        'terminal': term,
    })


@app.route('/stdin', methods=['POST'])
def stdin():
    data = request.get_json()
    session_id = data.get('session_id')
    user_input = data.get('input', '')

    interp = _SESSIONS.get(session_id)
    if not interp:
        return jsonify({
            'success': False,
            'error': 'Terminal session not found. Please run again.',
            'terminal': None,
        })

    runtime_errors = []
    try:
        interp.provide_input(user_input)
    except InterpreterError as e:
        runtime_errors.append(e.to_dict())
    except Exception as e:
        runtime_errors.append({'message': str(e), 'line': 0, 'column': 0})

    term = {
        'session_id': session_id,
        'output': ''.join(interp.output),
        'waiting_for_input': bool(interp.waiting_for_input),
        'prompt': interp.input_request.prompt if interp.input_request else '',
        'runtime_errors': runtime_errors,
    }

    return jsonify({
        'success': True if not runtime_errors else False,
        'terminal': term,
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)