# --- Imports ---
import sqlite3
import os
import json
from dataclasses import dataclass, field
from typing import List, Optional
import traceback # For more detailed error printing

import click # For CLI commands
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash # For password hashing

# --- NEW: Import Google Generative AI Library ---
import google.generativeai as genai

# --- Dataclass Definition ---
@dataclass
class RecipeCriteria:
    ingredients_available: List[str] = field(default _ factory=list)
    cuisine_preference: Optional[str] = None
    dietary_restrictions: List[str] = field(default_factory=list)
    max_prep_time_minutes: Optional[int] = None
    max_calories: Optional[int] = None

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Configuration ---
app.config['SECRET_KEY'] = os.urandom(24)
DATABASE = os.path.join(app.instance_path, 'users.db')

# --- NEW: Configure Gemini API ---
try:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable not set.")
    genai.configure(api_key=api_key)
    # Choose a Gemini model (e.g., 'gemini-1.5-flash', 'gemini-pro')
    # Using flash as it's generally faster and cheaper
    gemini_model = genai.GenerativeModel('gemini-1.5-flash')
    print("Gemini API Configured Successfully.")
except Exception as e:
    print("Error configuring Gemini API: {e}")
    # Consider how to handle this - maybe disable LLM features?
    gemini_model = None # Set model to None if config fails

# Ensure the instance folder exists for the SQLite database
try:
    os.makedirs(app.instance_path)
except OSError:
    pass


# --- Database Setup Functions (Standard sqlite3) ---
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        );
    ''')
    conn.commit()
    conn.close()

@app.cli.command("init-db")
def init_db_command():
    init_db()
    click.echo("Initialized the database.")

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate_recipe', methods=['POST'])
def generate_recipe():
    if not gemini_model:
         flash("Recipe generation service is currently unavailable. Please try again later.", "danger")
         return redirect(url_for('index'))

    if request.method == 'POST':
        user_input = request.form.get('recipe_input')

        if not user_input:
            flash("Please enter your recipe request.", "warning")
            return redirect(url_for('index'))

        # --- Step 1: Prompt LLM for Structured Data Extraction ---
        parsing_prompt = (
            f"Analyze the following user request for a recipe: '{user_input}'. "
            f"Extract the available ingredients, cuisine preference, dietary restrictions "
            f"(such as 'vegan', 'gluten-free', etc.), maximum preparation time in minutes, "
            f"and maximum calories mentioned in the request. "
            f"Respond STRICTLY with a JSON object containing the following keys: "
            f"'ingredients_available' (as a JSON list of strings), "
            f"'cuisine_preference' (as a JSON string or null if not mentioned), "
            f"'dietary_restrictions' (as a JSON list of strings or an empty list), "
            f"'max_prep_time_minutes' (as a JSON integer or null if not mentioned), "
            f"'max_calories' (as a JSON integer or null if not mentioned). "
            f"If a specific criterion isn't mentioned, use null for its value where appropriate (strings, integers) or an empty list for list types."
            f"Do not include any text before or after the JSON object." # Important instruction
        )
        print(f"--- Sending Parsing Prompt to LLM ---\n{parsing_prompt}\n------------------------------------")

        llm_parsing_response_str = None
        try:
            # --- Step 2: LLM Call for Parsing ---
            response = gemini_model.generate_content(parsing_prompt)
            # Check for safety feedback or blocked content
            if not response.parts:
                 # Handle cases where the response might be blocked
                 safety_feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else 'No parts in response, possibly blocked.'
                 print(f"Warning: Parsing LLM call might have been blocked. Feedback: {safety_feedback}")
                 flash("Your request could not be processed due to safety filters. Please rephrase.", "warning")
                 return redirect(url_for('index'))

            llm_parsing_response_str = response.text
            print(f"--- Received Parsing Response from LLM ---\n{llm_parsing_response_str}\n------------------------------------")

        except Exception as e:
            print(f"Error during LLM Parsing API call: {e}")
            print(traceback.format_exc()) # Print full traceback for debugging
            flash("An error occurred while communicating with the recipe analysis service.", "danger")
            return redirect(url_for('index'))

        # --- Step 3: Parse LLM Response into Structured Object ---
        criteria = None
        if llm_parsing_response_str:
            try:
                # Attempt to strip potential markdown/formatting around JSON
                json_str_cleaned = llm_parsing_response_str.strip().strip('`').strip()
                if json_str_cleaned.startswith("json"):
                     json_str_cleaned = json_str_cleaned[4:].strip() # Remove potential 'json' prefix

                parsed_data = json.loads(json_str_cleaned)
                criteria = RecipeCriteria(
                    ingredients_available=parsed_data.get('ingredients_available', []),
                    cuisine_preference=parsed_data.get('cuisine_preference'),
                    dietary_restrictions=parsed_data.get('dietary_restrictions', []),
                    max_prep_time_minutes=parsed_data.get('max_prep_time_minutes'),
                    max_calories=parsed_data.get('max_calories')
                )
                print(f"--- Parsed Criteria Object ---\n{criteria}\n-----------------------------")
            except json.JSONDecodeError:
                print(f"Error: Failed to parse JSON response from LLM for criteria: {llm_parsing_response_str}")
                flash("Sorry, I had trouble understanding the structured details of your request. Proceeding with raw text.", "warning")
                criteria = user_input # Use raw input as fallback if parsing fails
            except Exception as e:
                print(f"Error creating RecipeCriteria object: {e}")
                flash("An unexpected error occurred while processing your request details.", "danger")
                criteria = user_input # Use raw input as fallback
        else:
             # Handle case where LLM parsing response was empty or None
             flash("Could not get structured details from the request. Proceeding with raw text.", "warning")
             criteria = user_input # Use raw input as fallback

        # --- Step 4: Construct Prompt for Recipe Generation ---
        if isinstance(criteria, RecipeCriteria):
            generation_prompt = f"Generate a detailed recipe based on the following criteria:\n"
            if criteria.ingredients_available:
                generation_prompt += f"- Ingredients Available: {', '.join(criteria.ingredients_available)}\n"
            if criteria.cuisine_preference:
                generation_prompt += f"- Cuisine Preference: {criteria.cuisine_preference}\n"
            if criteria.dietary_restrictions:
                generation_prompt += f"- Dietary Restrictions: {', '.join(criteria.dietary_restrictions)}\n"
            if criteria.max_prep_time_minutes:
                generation_prompt += f"- Maximum Prep Time: {criteria.max_prep_time_minutes} minutes\n"
            if criteria.max_calories:
                generation_prompt += f"- Maximum Calories: {criteria.max_calories} kcal\n"
            generation_prompt += "\nPlease provide cooking steps and nutritional information."
        else: # Fallback if criteria is just the raw string
            generation_prompt = (
                f"Please generate a detailed recipe based on the following user request: "
                f"'{criteria}'. " # criteria here is the user_input string
                f"Pay attention to any specified ingredients, desired cuisine, dietary restrictions "
                f"(like vegan, gluten-free), maximum preparation time, or calorie goals mentioned. "
                f"Provide cooking steps and nutritional information if possible."
            )

        print(f"--- Sending Generation Prompt to LLM ---\n{generation_prompt}\n---------------------------------------")

        generated_recipe_text = "Placeholder: Recipe generation failed." # Default text
        try:
            # --- Step 5: LLM Call for Recipe Generation ---
            response = gemini_model.generate_content(generation_prompt)

            if not response.parts:
                 safety_feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else 'No parts in response, possibly blocked.'
                 print(f"Warning: Generation LLM call might have been blocked. Feedback: {safety_feedback}")
                 flash("The recipe generation was blocked by safety filters. Please try a different request.", "warning")
                 # Avoid setting recipe text if blocked
            else:
                 generated_recipe_text = response.text
                 print(f"--- Received Generation Response from LLM ---")

        except Exception as e:
            print(f"Error during LLM Generation API call: {e}")
            print(traceback.format_exc()) # Print full traceback for debugging
            flash("An error occurred while communicating with the recipe generation service.", "danger")
            # Keep the default failure text

        # --- Step 6: Display Result ---
        return render_template('index.html', recipe=generated_recipe_text, previous_input=user_input)

    # If accessed via GET, just redirect home
    return redirect(url_for('index'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Handles user registration."""
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        if not email or not password:
             flash('Email and password are required.', 'danger')
             return redirect(url_for('register'))

        password_hash = generate_password_hash(password)
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO user (email, password_hash) VALUES (?, ?)", (email, password_hash))
            conn.commit()
            flash('Registration successful! Please login.', 'success')
        except sqlite3.IntegrityError:
            flash('Email already registered.', 'danger')
        except Exception as e:
             flash(f'An error occurred during registration: {e}', 'danger')
        finally:
             conn.close()

        # Redirect appropriately based on success/failure
        if 'Registration successful!' in [msg for cat, msg in get_flashed_messages(with_categories=True)]:
             return redirect(url_for('login'))
        else:
             return redirect(url_for('register'))

    return render_template('register.html')





@app.route('/profile')
def profile():
    """Displays user profile/preferences page."""
    if 'user_id' not in session:
        flash('Please login to view your profile.', 'warning')
        return redirect(url_for('login'))
    return render_template('profile.html', email=session.get('user_email'))


@app.route('/logout')
def logout():
    """Logs the user out."""
    session.pop('user_id', None)
    session.pop('user_email', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


# --- Main Execution ---
if __name__ == '__main__':
    app.run(debug=True) # Keep debug=True for development ONLY
