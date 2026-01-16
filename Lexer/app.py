from flask import Flask, render_template, request, jsonify
from lexer import Lexer
from parser import Parser

app = Flask(__name__)

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
            'syntax_errors': syntax_errors_dict
        })
    else:
        return jsonify({
            'success': True,
            'message': 'Parsing successful!',
            'all_tokens': tokens_dict, 
            'lexical_errors': [],
            'syntax_errors': [],     
            'ast': ast.to_dict() if ast else None
        })

if __name__ == '__main__':
    app.run(debug=True, port=5000)