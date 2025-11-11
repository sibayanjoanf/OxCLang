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
    
    # Separate errors for the error section
    errors = [t for t in tokens_dict if t['is_error']]
    
    return jsonify({
        'all_tokens': tokens_dict,  # Send ALL tokens including errors
        'errors': errors
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)