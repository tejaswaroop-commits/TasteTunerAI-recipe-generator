from flask import Flask, render_template
import os

# Ensure templates folder exists relative to this test script
TEMPLATE_DIR = os.path.abspath('./templates')
print(f"Looking for templates in: {TEMPLATE_DIR}")

app = Flask(__name__, template_folder='templates') # Explicitly set folder

@app.route('/')
def home():
    try:
        # We will create a simple_test.html file
        print("Attempting to render simple_test.html")
        return render_template('simple_test.html')
    except Exception as e:
        print(f"Error in route: {e}")
        return f"Error rendering template: {e}", 500

if __name__ == '__main__':
    # Run on a different port like 5001 to avoid conflict with your main app
    app.run(debug=True, port=5001)