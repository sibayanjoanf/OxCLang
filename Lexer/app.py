from flask import Flask, render_template, request, jsonify
from lexer import Lexer

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
    
    # Separate valid tokens and errors
    valid_tokens = [t for t in tokens_dict if t['type'] != 'ERROR']
    errors = [t for t in tokens_dict if t['type'] == 'ERROR']
    
    return jsonify({
        'tokens': valid_tokens,
        'errors': errors
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)