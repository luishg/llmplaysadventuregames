#!/usr/bin/env python3
"""
Point-and-click adventure game AI Player v6
Automates playing Point-and-click adventure games on Linux using computer vision and AI.
Supports local Ollama models, OpenAI, Anthropic, and Hugging Face models.
Allows dynamic window selection.
"""

import os
import sys
import time
import json
import base64
import subprocess
import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path
import re # Add this import at the top of your file
import threading
import tkinter as tk
from tkinter import ttk  # Add ttk import
from tkinter import scrolledtext  # Correct import for scrolledtext
import queue
from PIL import Image, ImageDraw, ImageFont, ImageTk # Added ImageTk
from grid import add_numbered_grid_to_image, get_cell_coordinates, get_cell_number_from_pixel # Import grid functions
import random
import chat
from chat import get_user_clicks, initialize_twitch, TWITCH_TOKEN, get_recent_user_clicks, is_chat_running, get_chat_stats, start_twitch_bot  # Import TWITCH_TOKEN, new functions

try:
    import ollama
    import pyautogui
    import mss
    # For remote LLMs
    import openai
    import anthropic
    # For Hugging Face models
    import requests
except ImportError as e:
    print(f"[!] Missing required Python package: {e}")
    print("[!] Please install them, e.g., using pip: pip install ollama pyautogui mss pillow openai anthropic requests")
    sys.exit(1)

# --- Setup Logging ---
# Goal: All print() statements go to console for user.
#       logger.info/debug/etc. from our script go ONLY to the session log file.
#       Third-party library logs are minimized on console.

# 1. Clear any existing handlers from the root logger to start fresh
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)
    handler.close()

# 2. Configure our application's logger
logger = logging.getLogger(__name__) # Get our application's logger
logger.setLevel(logging.DEBUG)      # Set its level to capture everything for the file handler
logger.propagate = False           # IMPORTANT: Prevent messages from propagating to the root logger

# 3. Set levels for noisy third-party libraries to reduce their console output
#    This affects how their log messages are handled if they propagate to root or have their own console handlers.
noisy_loggers_to_warn = ["httpx", "httpcore", "openai", "anthropic", "ollama", "mss", "PIL.PngImagePlugin"]
for lib_name in noisy_loggers_to_warn:
    logging.getLogger(lib_name).setLevel(logging.WARNING)

# --- Configuration Constants ---
DEFAULT_GAME_WINDOW_TITLE = "Maniac Mansion"
SESSIONS_DIR = "sessions"
SCREENSHOT_INTERVAL = 3  # Seconds to wait after LLM response before next screenshot
CLICK_INTERVAL = 2       # Seconds between multiple clicks from a single LLM response
CHAT_CHECK_INTERVAL = 5  # Check chat every N iterations
INTERNAL_CROP = {"top": 0, "bottom": 0, "left": 0, "right": 0} # ScummVM padding

# --- API Keys (Load from environment variables) ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")

# Validate API keys
if not OPENAI_API_KEY:
    print("[!] Warning: OPENAI_API_KEY environment variable not set. OpenAI models will not be available.")
if not ANTHROPIC_API_KEY:
    print("[!] Warning: ANTHROPIC_API_KEY environment variable not set. Anthropic models will not be available.")
if not HUGGINGFACE_TOKEN:
    print("[!] Warning: HUGGINGFACE_TOKEN environment variable not set. Hugging Face models will not be available.")

# --- Global LLM Game Context ---
LLM_GAME_CONTEXT = "I'm playing a point-and-click adventure game. I have to explore how the story unfolds through what I see on the screen."

# --- Global variable for selected game window title ---
SELECTED_GAME_WINDOW_TITLE = DEFAULT_GAME_WINDOW_TITLE
SELECTED_GAME_WINDOW_ID = None # Add new global for the selected window's ID

# --- Global variables for LLM context and history ---
LLM_LAST_ACTIONS = []  # List to store last 10 actions
MAX_ACTIONS_HISTORY = 10  # Maximum number of actions to keep in history
LLM_GAME_CONTEXT = "I'm playing a point-and-click adventure game. I have to explore how the story unfolds through what I see on the screen."
TEMP_DESCRIPTIONS = []  # List to store descriptions for context updates
DESCRIPTIONS_BEFORE_UPDATE = 10  # Number of descriptions to collect before updating context
GAME_MAP_GRAPH = "No map data available yet."  # Store the current map graph
GAME_OBJECTIVES = "No objectives identified yet."  # Store the current objectives list

# Game-specific instructions for Maniac Mansion
GAME_INSTRUCTIONS = """Game: Maniac Mansion 2: The day of the tentacle
Story: You must explore, solve puzzles, and find a way to advance the story.

Key Game Elements:

Bottom Left Action Menu when available:
  - The action menu is at the bottom-left of the screen
  - Some common action coordinates (cells numbers):
    - Open: 129
    - Close: 145
    - Look at: 131
    - Push: 135
    - Pull: 151
    - Pick up: 116
    - Use: 119
  - If not action is selected, walking will be the default action if you click any area
  - Tu use specific action, first, select an action/verb (center of the text) from the menu (e.g., "Open", "Use", "Pick Up")
  - Then click on the target object or area to perform the action

Inventory Usage:
  - Inventory is on the bottom right area of the screen
  - First click the action button (e.g., "Use")
  - Then click the item in your inventory
  - Finally click the target object or location two times to confirm action

Movement and Exploration:
  - Click on doors to move between rooms
  - Click on the edges of the screen to move between areas
  - Explore every corner of each room
  - Try different combinations of actions and objects

Important Tips:
  - You can perform multiple actions in sequence, BUT define one clic per action
  - Avoid to repeat the same actions on your recent action list
  - Prioritizeze exploration, looking for exist to new areas (screen edges, stairs, doors)
  - Look for hidden passages and secret areas
  - Pay attention to character reactions
  - Some items can be combined in inventory

Remember: The game requires creative thinking and trying different combinations of actions and objects."""

# Global LLM prompt template
LLM_PROMPT_TEMPLATE = """You are an AI assistant playing an adventure game. Analyze the screenshot and provide a JSON response with the following structure:

{{
    "description": "Brief description of what you see in the scene",
    "action_plan": "Your plan for what to do next",
    "clicks": [
        {{
            "coordinates": 42,  # Cell number where to click
            "reason": "Click Open action on menu"
        }},
        {{
            "coordinates": 156,  # Cell number where to click
            "reason": "Click on the door to enter the room after Clicking Open"
        }}
    ]
}}

IMPORTANT - COORDINATE SYSTEM FOR CLICKING:
The image has grid overlay with numbered cells (ignore the grid and numbers to play and describe the scene as you see it)
- Cells are numbered from left to right, top to bottom
- When you want to click somewhere:
  1. Look at the grid overlay and find the cell number closest to where you want to click
  2. Use that cell number as the "coordinates" value
  3. If the exact location is between cells, choose the closest cell number

Game Context:
{game_context}

Game Instructions:
{game_instructions}

Recent Actions:
{recent_actions}"""

def update_action_history(description, action_plan, clicks):
    """Updates the action history with the latest action."""
    global LLM_LAST_ACTIONS
    
    # Create a formatted string for this action
    action_text = f"Action: {action_plan}\n"
    if clicks:
        action_text += "Clicks:\n"
        for click in clicks:
            coords = click.get('coordinates', [0, 0])
            reason = click.get('reason', 'No reason')
            action_text += f"- {reason} at coordinates {coords}\n"
    
    # Add the new action to the list
    LLM_LAST_ACTIONS.append(action_text)
    
    # Keep only the last MAX_ACTIONS_HISTORY actions
    if len(LLM_LAST_ACTIONS) > MAX_ACTIONS_HISTORY:
        LLM_LAST_ACTIONS = LLM_LAST_ACTIONS[-MAX_ACTIONS_HISTORY:]

def get_llm_prompt_text(image_width, image_height):
    """Get the formatted LLM prompt with current context and instructions."""
    global LLM_GAME_CONTEXT, GAME_INSTRUCTIONS, LLM_LAST_ACTIONS
    
    # Format the prompt template with current values
    prompt = LLM_PROMPT_TEMPLATE.format(
        game_context=LLM_GAME_CONTEXT,
        game_instructions=GAME_INSTRUCTIONS,
        recent_actions=json.dumps(LLM_LAST_ACTIONS, indent=2)
    )
    
    return prompt

def check_x11_tools():
    """Checks if required X11 command-line tools are installed."""
    tools = ["xdotool", "xprop"]
    missing_tools = []
    for tool in tools:
        try:
            if tool == "xdotool":
                subprocess.run([tool, "--version"], capture_output=True, check=True, text=True, timeout=5)
            elif tool == "xprop":
                subprocess.run(["which", tool], capture_output=True, check=True, text=True, timeout=5)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            missing_tools.append(tool)
    
    if missing_tools:
        logger.error(f"Missing or non-functional required X11 tools: {', '.join(missing_tools)}")
        print(f"[!] Please install/check: sudo dnf install {' '.join(missing_tools)}")
        if "xprop" in missing_tools and "xdotool" not in missing_tools:
            print("[!] xprop is often part of a package like 'xorg-x11-utils'.")
        return False
    logger.info("Required X11 tools (xdotool, xprop) found and responsive.")
    return True

def create_session_directory():
    """Creates a timestamped session directory and sets up file logging for the session."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = Path(SESSIONS_DIR) / f"session_{timestamp}"
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        
        # Remove any pre-existing file handlers from our specific logger instance
        # to prevent duplicate log entries if this function were ever called multiple times (unlikely here).
        for handler in list(logger.handlers): # Iterate over a copy
            if isinstance(handler, logging.FileHandler):
                logger.removeHandler(handler)
                handler.close()

        file_handler = logging.FileHandler(session_dir / "play_session.log")
        file_handler.setLevel(logging.INFO) # Log INFO and above from our script to file
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(funcName)s - %(message)s'))
        logger.addHandler(file_handler) # Add handler ONLY to our specific logger
        logger.info(f"Logging session to file: {session_dir / 'play_session.log'}") # This goes to file only

    except OSError as e:
        print(f"[!] Error: Could not create session directory {session_dir}: {e}")
        logger.error(f"Failed to create session directory {session_dir}: {e}") 
        return None
    return session_dir

def get_available_windows():
    """Uses xdotool to get a list of all visible windows."""
    try:
        search_cmd = ["xdotool", "search", "--onlyvisible", "--name", ".*"] 
        result = subprocess.run(search_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=5)
        window_ids = [wid for wid in result.stdout.strip().split("\n") if wid]
        
        windows = []
        if not window_ids:
            logger.warning("xdotool search found no visible windows.")
            return []

        for wid in window_ids:
            try:
                name_cmd = ["xdotool", "getwindowname", wid]
                name_result = subprocess.run(name_cmd, stdout=subprocess.PIPE, text=True, check=True, timeout=2)
                name = name_result.stdout.strip()
                if name: 
                    windows.append({"id": wid, "name": name})
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                logger.debug(f"Could not get name for window ID {wid}: {e}")
        return windows
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error(f"Error listing windows with xdotool: {e}")
        return []
    except FileNotFoundError:
        logger.error("xdotool command not found. Please ensure it's installed and in PATH.")
        return []

def select_target_window():
    """Prompts the user to select a target window from a list."""
    global SELECTED_GAME_WINDOW_TITLE, SELECTED_GAME_WINDOW_ID # Ensure ID is global
    print("\nDetecting open windows...")
    windows = get_available_windows()

    if not windows:
        print(f"[!] No windows found or xdotool error. Using default title: '{DEFAULT_GAME_WINDOW_TITLE}'")
        logger.warning(f"Window selection: No windows listed by xdotool or error occurred. Defaulting to title: '{DEFAULT_GAME_WINDOW_TITLE}'")
        SELECTED_GAME_WINDOW_TITLE = DEFAULT_GAME_WINDOW_TITLE
        SELECTED_GAME_WINDOW_ID = None # No ID for default
        return

    print("\nAvailable windows to target:")
    for idx, window in enumerate(windows):
        print(f"  {idx + 1}: {window['name']} (ID: {window['id']})")
    print(f"  {len(windows) + 1}: Use default title '{DEFAULT_GAME_WINDOW_TITLE}'")

    while True:
        try:
            selection = input(f"Select window number (1-{len(windows) + 1}): ")
            selected_idx = int(selection) - 1
            if 0 <= selected_idx < len(windows):
                SELECTED_GAME_WINDOW_TITLE = windows[selected_idx]['name']
                SELECTED_GAME_WINDOW_ID = windows[selected_idx]['id'] # Store the selected window's ID
                logger.info(f"Targeting window: '{SELECTED_GAME_WINDOW_TITLE}' (ID: {SELECTED_GAME_WINDOW_ID})")
                break
            elif selected_idx == len(windows):
                SELECTED_GAME_WINDOW_TITLE = DEFAULT_GAME_WINDOW_TITLE
                SELECTED_GAME_WINDOW_ID = None # Reset ID if default title is chosen
                logger.info(f"Targeting default window title: '{DEFAULT_GAME_WINDOW_TITLE}' (will search by name)")
                break
            else:
                print("[!] Invalid selection. Please enter a number from the list.")
        except ValueError:
            print("[!] Invalid input. Please enter a number.")
    return

def configure_huggingface_token():
    """Configure Hugging Face token."""
    global HUGGINGFACE_TOKEN
    print("\n=== Hugging Face Configuration ===")
    print("To use Hugging Face models, you need to:")
    print("1. Create an account at https://huggingface.co")
    print("2. Get your token from https://huggingface.co/settings/tokens")
    print("3. Accept the model terms at https://huggingface.co/google/gemma-3-27b-it")
    
    current_token = HUGGINGFACE_TOKEN if HUGGINGFACE_TOKEN else "Not configured"
    print(f"\nCurrent token: {current_token}")
    
    while True:
        choice = input("\nEnter new token (or press Enter to keep current): ").strip()
        if not choice:
            break
        if choice.startswith("hf_"):
            HUGGINGFACE_TOKEN = choice
            save_config()
            print("[✓] Hugging Face token updated")
            break
        else:
            print("[!] Invalid token format. Token should start with 'hf_'")

def get_llm_providers():
    """Returns a list of available LLM providers and their models."""
    providers = []
    # Ollama (Local)
    try:
        ollama_models = ollama.list().get('models', [])
        if ollama_models:
            for model_info in ollama_models:
                providers.append({
                    "provider_name": "Ollama (Local)",
                    "model_id": model_info.get('model'),
                    "display_name": f"Ollama: {model_info.get('model')}",
                    "type": "ollama"
                })
            logger.info(f"Found {len(ollama_models)} Ollama model(s).")
        else:
            logger.warning("No Ollama models found locally.")
    except Exception as e:
        logger.warning(f"Could not list Ollama models: {e}. Ensure Ollama is running and accessible.")

    # OpenAI
    if OPENAI_API_KEY and OPENAI_API_KEY.startswith("sk-") and len(OPENAI_API_KEY) > 20:
        logger.info("OpenAI API key found, adding OpenAI models.")
        providers.append({"provider_name": "OpenAI (Remote)", "model_id": "gpt-4.1-mini", "display_name": "OpenAI: GPT-4.1 Mini", "type": "openai"})
    else:
        logger.warning(f"OpenAI API key is missing, a placeholder, or invalid. Skipping OpenAI models.")

    # Anthropic
    if ANTHROPIC_API_KEY and ANTHROPIC_API_KEY.startswith("sk-ant-") and len(ANTHROPIC_API_KEY) > 20:
        logger.info("Anthropic API key found, adding Anthropic models.")
        providers.append({"provider_name": "Anthropic (Remote)", "model_id": "claude-3-opus-20240229", "display_name": "Anthropic: Claude 3 Opus", "type": "anthropic"})
        providers.append({"provider_name": "Anthropic (Remote)", "model_id": "claude-3-sonnet-20240229", "display_name": "Anthropic: Claude 3 Sonnet", "type": "anthropic"})
    else:
        logger.warning(f"Anthropic API key is missing, a placeholder, or invalid. Skipping Anthropic models.")

    # Hugging Face
    if HUGGINGFACE_TOKEN and HUGGINGFACE_TOKEN.startswith("hf_"):
        logger.info("Hugging Face token found, adding Hugging Face models.")
        providers.append({
            "provider_name": "Hugging Face (Remote)",
            "model_id": "google/gemma-3-27b-it",
            "display_name": "Hugging Face: Gemma 3 27B",
            "type": "huggingface"
        })
        providers.append({
            "provider_name": "Hugging Face (Remote)",
            "model_id": "Salesforce/blip2-opt-2.7b",
            "display_name": "Hugging Face: BLIP-2 OPT 2.7B",
            "type": "huggingface"
        })
        providers.append({
            "provider_name": "Hugging Face (Remote)",
            "model_id": "microsoft/git-base-coco",
            "display_name": "Hugging Face: GIT Base COCO",
            "type": "huggingface"
        })
    else:
        logger.warning("Hugging Face token is missing or invalid. Skipping Hugging Face models.")
    
    if not providers:
        logger.error("CRITICAL: No LLM providers could be configured. Please check your setup and API keys.")
        
    return providers

def select_llm_model(providers_list):
    """Prompts the user to select an LLM from the combined list."""
    while True:
        print("\n=== Model Selection Menu ===")
        print("1. Select Local Model (Ollama)")
        print("2. Select Remote Model (OpenAI/Anthropic)")
        print("3. Select Hugging Face Model")
        print("4. Back to Main Menu")

        choice = input("\nSelect an option (1-4): ").strip()

        if choice == "1":
            selected_model = show_ollama_models()
            if selected_model:
                return selected_model
        elif choice == "2":
            selected_model = show_remote_models()
            if selected_model:
                return selected_model
        elif choice == "3":
            selected_model = show_huggingface_models()
            if selected_model:
                return selected_model
        elif choice == "4":
            return None
        else:
            print("[!] Invalid option. Please try again.")

def find_game_window_details(title_to_find, id_to_find=None):
    """
    Find the game window and return its details.
    Prioritizes id_to_find if provided and valid. Otherwise, searches by title_to_find.
    Returns coordinates for the exact content area of the window.
    """
    final_window_id = None

    if id_to_find:
        try:
            temp_geom_cmd = ["xdotool", "getwindowgeometry", "--shell", id_to_find]
            subprocess.run(temp_geom_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=2)
            final_window_id = id_to_find
            logger.debug(f"Validated provided window ID: {id_to_find} for title query '{title_to_find}'.")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Provided window ID {id_to_find} (for '{title_to_find}') seems invalid or window closed: {e}. Falling back to search by title.")
            final_window_id = None

    if not final_window_id:
        logger.debug(f"Searching for window by title: '{title_to_find}'")
        found_by_name = False

        # First, let's list all visible windows to help with debugging
        try:
            list_cmd = ["xdotool", "search", "--onlyvisible", "--name", ".*"]
            list_result = subprocess.run(list_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=3)
            all_windows = list_result.stdout.strip().split("\n")
            logger.debug(f"All visible windows: {all_windows}")
            
            # Get names of all windows for debugging
            for wid in all_windows:
                if wid:
                    try:
                        name_cmd = ["xdotool", "getwindowname", wid]
                        name_result = subprocess.run(name_cmd, stdout=subprocess.PIPE, text=True, check=True, timeout=2)
                        name = name_result.stdout.strip()
                        logger.debug(f"Window ID {wid}: '{name}'")
                    except Exception as e:
                        logger.debug(f"Could not get name for window {wid}: {e}")
        except Exception as e:
            logger.debug(f"Error listing windows: {e}")

        # Try different search strategies
        search_strategies = [
            # 1. Exact match
            (f"^{re.escape(title_to_find)}$", "exact match"),
            # 2. Case-insensitive exact match
            (f"(?i)^{re.escape(title_to_find)}$", "case-insensitive exact match"),
            # 3. Contains match
            (re.escape(title_to_find), "contains match"),
            # 4. Case-insensitive contains match
            (f"(?i){re.escape(title_to_find)}", "case-insensitive contains match"),
            # 5. Raw title as regex
            (title_to_find, "raw title as regex")
        ]

        for pattern, strategy in search_strategies:
            if found_by_name:
                break
                
            try:
                logger.debug(f"Trying {strategy} with pattern: '{pattern}'")
                search_cmd = ["xdotool", "search", "--onlyvisible", "--name", pattern]
                result = subprocess.run(search_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=3)
                window_ids = [wid for wid in result.stdout.strip().split("\n") if wid]
                
                if window_ids:
                    final_window_id = window_ids[0]
                    found_by_name = True
                    logger.debug(f"Found window by {strategy} (ID: {final_window_id})")
                    
                    # Verify the window name
                    try:
                        name_cmd = ["xdotool", "getwindowname", final_window_id]
                        name_result = subprocess.run(name_cmd, stdout=subprocess.PIPE, text=True, check=True, timeout=2)
                        actual_name = name_result.stdout.strip()
                        logger.debug(f"Matched window name: '{actual_name}'")
                    except Exception as e:
                        logger.debug(f"Could not verify window name: {e}")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                logger.debug(f"Search with {strategy} failed: {e}")

        if not final_window_id:
            logger.error(f"Could not find window by title '{title_to_find}' after trying all search strategies.")
            return None

    try:
        logger.debug(f"Getting geometry for window ID {final_window_id} (Original title query was: '{title_to_find}').")
        geom_cmd = ["xdotool", "getwindowgeometry", "--shell", final_window_id]
        geom_result = subprocess.run(geom_cmd, stdout=subprocess.PIPE, text=True, check=True, timeout=3)
        
        geometry = {}
        for line in geom_result.stdout.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                geometry[key.strip()] = int(value.strip())
        
        # Use the window geometry directly, assuming it's the content area
        content_x = geometry["X"]
        content_y = geometry["Y"]
        content_width = geometry["WIDTH"]
        content_height = geometry["HEIGHT"]

        logger.debug(f"Window {final_window_id} content area: X={content_x}, Y={content_y}, W={content_width}, H={content_height}")
        
        if content_width <= 0 or content_height <= 0:
            logger.error(f"Invalid content area dimensions: W={content_width}xH={content_height}. Window ID: {final_window_id}.")
            return None
            
        return {
            "left": content_x,
            "top": content_y,
            "width": content_width,
            "height": content_height,
            "window_id": final_window_id, 
            "original_x": geometry["X"], 
            "original_y": geometry["Y"]
        }
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error(f"Error executing xdotool getwindowgeometry for determined window ID {final_window_id} (Title query: '{title_to_find}'): {e}")
        if hasattr(e, 'stderr') and e.stderr: logger.error(f"Stderr: {e.stderr.strip()}")
        return None
    except Exception as e: 
        logger.error(f"Unexpected error getting details for window ID {final_window_id}: {e}", exc_info=True)
        return None

def capture_screenshot_of_region(window_details):
    if not window_details:
        logger.error("capture_screenshot_of_region: No window details provided.")
        return None
    
    region_to_capture = {
        "left": window_details["left"],
        "top": window_details["top"],
        "width": window_details["width"],
        "height": window_details["height"]
    }

    try:
        with mss.mss() as sct:
            sct_img = sct.grab(region_to_capture)
            img = Image.frombytes("RGB", (sct_img.width, sct_img.height), sct_img.rgb, "raw", "RGB")
            # Changed from INFO to DEBUG for cleaner console
            logger.debug(f"Screenshot captured for region: L{region_to_capture['left']}, T{region_to_capture['top']}, W{region_to_capture['width']}, H{region_to_capture['height']}")
            return img
    except mss.exception.ScreenShotError as e:
        logger.error(f"MSS Screenshot Error: {e}. Region: {region_to_capture}")
        logger.error("Ensure the window is visible, not minimized, and the region is valid.")
        return None
    except Exception as e:
        logger.error(f"General error capturing screenshot: {e}. Region: {region_to_capture}", exc_info=True)
        return None


def get_ollama_llm_analysis(model_id, base64_image_raw, image_width, image_height):
    prompt_text = get_llm_prompt_text(image_width, image_height)
    response = ollama.generate(
        model=model_id,
        prompt=prompt_text,
        images=[base64_image_raw],
        format="json", 
        stream=False
    )
    return response['response']

def get_openai_llm_analysis(model_id, base64_image_data_url, image_width, image_height):
    if not (OPENAI_API_KEY and OPENAI_API_KEY.startswith("sk-") and len(OPENAI_API_KEY) > 20):
        logger.error("OpenAI API key not configured or invalid.")
        return None, None, 0
    
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    # System prompt can remain general, as the detailed context is now in the user prompt
    #system_prompt = "You are an AI agent playing the game Maniac Mansion. Analyze the provided game screenshot and decide on the best next action. The image has a reference grid. Output your response in JSON format with 'description', 'action_plan', and 'clicks' (list of [x,y] coordinates relative to the image, using the grid)."
    user_prompt_text = get_llm_prompt_text(image_width, image_height) 

    try:
        # Calculate token size
        text_tokens = len(user_prompt_text.split())  # Rough estimate of text tokens
        image_tokens = len(base64_image_data_url) // 4  # Rough estimate of image tokens (base64 encoded)
        total_tokens = text_tokens + image_tokens

        # Ensure the image data URL is properly formatted
        if not base64_image_data_url.startswith("data:image/"):
            base64_image_data_url = f"data:image/png;base64,{base64_image_data_url}"

        # First verify the model is available
        try:
            models = client.models.list()
            available_models = [model.id for model in models.data]
            if model_id not in available_models:
                logger.error(f"OpenAI model {model_id} not available. Available models: {available_models}")
                print(f"[!] OpenAI model {model_id} not available. Please check your API key permissions.")
                return None, None, total_tokens
        except Exception as e:
            logger.error(f"Error checking OpenAI model availability: {e}")
            print(f"[!] Error checking OpenAI model availability: {e}")
            return None, None, total_tokens

        response = client.chat.completions.create(
            model=model_id, 
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": base64_image_data_url,
                                "detail": "high"  # Changed from "auto" to "high" for better image quality
                            }
                        }
                    ]
                }
            ],
            max_tokens=600 
        )
        return response.choices[0].message.content, None, total_tokens
    except openai.AuthenticationError as e:
        logger.error(f"OpenAI Authentication Error: {e}")
        print(f"[!] OpenAI Authentication Error: Please check your API key.")
        return None, None, total_tokens
    except openai.RateLimitError as e:
        logger.error(f"OpenAI Rate Limit Error: {e}")
        print(f"[!] OpenAI Rate Limit Error: Please try again later.")
        return None, None, total_tokens
    except openai.APIError as e:
        logger.error(f"OpenAI API Error: {e}")
        print(f"[!] OpenAI API Error: {e}")
        return None, None, total_tokens
    except Exception as e:
        logger.error(f"Error calling OpenAI API ({model_id}): {e}", exc_info=True)
        print(f"[!] Error calling OpenAI API: {e}")
        return None, None, total_tokens

def get_anthropic_llm_analysis(model_id, base64_image_raw, image_width, image_height):
    if not (ANTHROPIC_API_KEY and ANTHROPIC_API_KEY.startswith("sk-ant-")):
        logger.error("Anthropic API key not configured or invalid.")
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    # System prompt can remain general
    #system_prompt = "You are an AI agent playing the game Maniac Mansion. Analyze the provided game screenshot and decide on the best next action. The image has a reference grid. Output your response in JSON format with 'description', 'action_plan', and 'clicks' (list of [x,y] coordinates relative to the image, using the grid)."
    user_prompt_text = get_llm_prompt_text(image_width, image_height) 

    try:
        response = client.messages.create(
            model=model_id, 
            max_tokens=1024,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png", # Assuming PNG from PIL save
                                "data": base64_image_raw,
                            },
                        },
                        {"type": "text", "text": user_prompt_text},
                    ],
                }
            ],
        )
        if response.content and isinstance(response.content, list) and response.content[0].type == "text":
            return response.content[0].text, None, 0
        else:
            logger.error(f"Unexpected Anthropic API response format ({model_id}): {response.content}")
            return None, None, 0
    except Exception as e:
        logger.error(f"Error calling Anthropic API ({model_id}): {e}", exc_info=True)
        return None, None, 0

def get_huggingface_llm_analysis(model_id, base64_image_raw, image_width, image_height):
    """Get analysis from Hugging Face model using their Inference API."""
    if not (HUGGINGFACE_TOKEN and HUGGINGFACE_TOKEN.startswith("hf_")):
        logger.error("Hugging Face token not configured or invalid.")
        return None

    try:
        # For Gemma models, we need to use a different endpoint
        if "gemma" in model_id.lower():
            API_URL = "https://tm1qnykyjdg8whed.us-east-1.aws.endpoints.huggingface.cloud"
            # Resize and compress image to reduce token count
            try:
                # Convert base64 to PIL Image
                image_data = base64.b64decode(base64_image_raw)
                img = Image.open(BytesIO(image_data))
                
                # Calculate new dimensions while maintaining aspect ratio
                max_width = 640
                max_height = 480
                width_ratio = max_width / img.width
                height_ratio = max_height / img.height
                ratio = min(width_ratio, height_ratio)  # Use the smaller ratio to fit within bounds
                
                new_width = int(img.width * ratio)
                new_height = int(img.height * ratio)
                
                # Resize with LANCZOS for better quality
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                # Convert to RGB if needed
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Compress the image with more aggressive settings
                buffered = BytesIO()
                img.save(buffered, format="JPEG", quality=30, optimize=True)
                base64_image_raw = base64.b64encode(buffered.getvalue()).decode('utf-8')
                print(f"Compressed image size: {len(base64_image_raw)} bytes")
                print(f"New image dimensions: {new_width}x{new_height} (max 640x480)")
            except Exception as e:
                logger.error(f"Error processing image: {e}")
                return None
        else:
            API_URL = f"https://api-inference.huggingface.co/models/{model_id}"

        headers = {
            "Authorization": f"Bearer {HUGGINGFACE_TOKEN}",
            "Content-Type": "application/json"
        }

        # Prepare the prompt text
        prompt_text = get_llm_prompt_text(image_width, image_height)

        # For Gemma models, we need to format the input differently
        if "gemma" in model_id.lower():
            # Format for Gemma models - send as a single string input
            payload = {
                "inputs": f"{prompt_text}\n<image>{base64_image_raw}</image>",
                "parameters": {
                    "max_new_tokens": 512,
                    "return_full_text": False,
                    "do_sample": True,
                    "temperature": 0.7
                }
            }
        else:
            # Original format for other models
            payload = {
                "inputs": {
                    "text": prompt_text,
                    "image": base64_image_raw
                },
                "parameters": {
                    "max_new_tokens": 200,
                    "return_full_text": False
                }
            }

        # Debug logging - Print to console for immediate visibility
        print("\n=== Hugging Face API Debug Info ===")
        print(f"Model ID: {model_id}")
        print(f"API URL: {API_URL}")
        print(f"Headers: {json.dumps(headers, indent=2)}")
        print(f"Prompt text length: {len(prompt_text)}")
        print(f"Image data length: {len(base64_image_raw)}")
        print(f"Payload structure: {json.dumps({**payload, 'inputs': 'TEXT_AND_IMAGE_DATA'}, indent=2)}")
        print("================================\n")

        # Make the API request
        response = requests.post(API_URL, headers=headers, json=payload)
        
        # Log response details
        print("\n=== API Response Debug Info ===")
        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {json.dumps(dict(response.headers), indent=2)}")
        
        if response.status_code != 200:
            print(f"Error Response: {response.text}")
            logger.error(f"API Error: {response.status_code} - {response.text}")
            print(f"[!] Hugging Face API Error: {response.status_code}")
            
            if response.status_code == 401:
                print("[!] Authentication failed. Please check your Hugging Face token.")
            elif response.status_code == 403:
                print("[!] Access denied. Please accept the model terms at https://huggingface.co/google/gemma-3-27b-it")
            elif response.status_code == 400:
                print("[!] Bad request. The model might not support the current input format.")
                print("\nDetailed Error Information:")
                print(f"Request URL: {API_URL}")
                print(f"Request Headers: {json.dumps(headers, indent=2)}")
                print(f"Request Payload Structure: {json.dumps({**payload, 'inputs': 'TEXT_AND_IMAGE_DATA'}, indent=2)}")
                print(f"Response Text: {response.text}")
            return None

        # Parse the response
        result = response.json()
        print(f"Response Body: {json.dumps(result, indent=2)}")
        print("================================\n")
        
        # Different models return different response formats
        if isinstance(result, list) and len(result) > 0:
            # Some models return a list with the generated text
            generated_text = result[0].get("generated_text", "")
            print(f"Generated text from list response: {generated_text}")
            return generated_text
        elif isinstance(result, dict):
            # Some models return a dictionary
            generated_text = result.get("generated_text", "")
            print(f"Generated text from dict response: {generated_text}")
            return generated_text
        else:
            print(f"Unexpected response format: {result}")
            logger.error(f"Unexpected response format from Hugging Face API: {result}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"\n[!] Network error when calling Hugging Face API: {e}")
        logger.error(f"Error calling Hugging Face API ({model_id}): {e}", exc_info=True)
        return None
    except Exception as e:
        print(f"\n[!] Unexpected error with Hugging Face API: {e}")
        logger.error(f"Unexpected error with Hugging Face API ({model_id}): {e}", exc_info=True)
        return None

def get_llm_analysis(selected_model_info, original_image, image_dimensions_for_llm):
    if not original_image or not image_dimensions_for_llm:
        logger.error("get_llm_analysis: No image or dimensions provided.")
        return None, original_image

    # Use the new grid.py function to add the numbered grid
    image_with_grid = add_numbered_grid_to_image(original_image)
    if not image_with_grid: 
        logger.error("Failed to draw grid on image, using original image for LLM if possible.")
        image_to_process = original_image
    else:
        image_to_process = image_with_grid
    
    buffered = BytesIO()
    try:
        image_to_process.save(buffered, format="PNG")
    except Exception as e:
        logger.error(f"Failed to save image to buffer: {e}", exc_info=True)
        return None, image_with_grid 

    img_bytes_raw = buffered.getvalue()
    base64_encoded_image_raw = base64.b64encode(img_bytes_raw).decode('utf-8')
    base64_image_data_url = f"data:image/png;base64,{base64_encoded_image_raw}" 

    # Calculate token size
    prompt_text = get_llm_prompt_text(image_dimensions_for_llm['width'], image_dimensions_for_llm['height'])
    text_tokens = len(prompt_text.split())  # Rough estimate of text tokens
    image_tokens = len(base64_encoded_image_raw) // 4  # Rough estimate of image tokens (base64 encoded)
    total_tokens = text_tokens + image_tokens

    # Changed from INFO to DEBUG for cleaner console
    logger.debug(f"Image with grid prepared ({image_dimensions_for_llm['width']}x{image_dimensions_for_llm['height']}). Calling LLM: {selected_model_info['display_name']}")
    logger.debug(f"Token size: {total_tokens} (Text: {text_tokens}, Image: {image_tokens})")
    
    response_content_str = None
    try:
        model_type = selected_model_info['type']
        model_id = selected_model_info['model_id']
        
        if model_type == "ollama":
            response_content_str = get_ollama_llm_analysis(model_id, base64_encoded_image_raw, image_dimensions_for_llm['width'], image_dimensions_for_llm['height'])
        elif model_type == "openai":
            response_content_str, _, _ = get_openai_llm_analysis(model_id, base64_image_data_url, image_dimensions_for_llm['width'], image_dimensions_for_llm['height'])
        elif model_type == "anthropic":
            response_content_str, _, _ = get_anthropic_llm_analysis(model_id, base64_encoded_image_raw, image_dimensions_for_llm['width'], image_dimensions_for_llm['height'])
        elif model_type == "huggingface":
            response_content_str = get_huggingface_llm_analysis(model_id, base64_encoded_image_raw, image_dimensions_for_llm['width'], image_dimensions_for_llm['height'])
        else:
            logger.error(f"Unknown model type: {model_type}")
            # This print is an error message, important for console
            print(f"[!] Unknown LLM model type: {model_type}")
            return None, image_with_grid

        if not response_content_str:
            logger.error(f"LLM ({selected_model_info['display_name']}) did not return any content.")
            # This print is important user feedback
            print(f"[!] LLM ({selected_model_info['display_name']}) did not return any content.")
            return None, image_with_grid

        parsed_json = None
        try:
            temp_response_str = response_content_str.strip()
            if temp_response_str.startswith("```json"):
                temp_response_str = temp_response_str[7:]
            if temp_response_str.endswith("```"):
                temp_response_str = temp_response_str[:-3]
            
            parsed_json = json.loads(temp_response_str.strip())
        except json.JSONDecodeError as je:
            logger.error(f"Failed to parse LLM JSON response: {je}")
            model_display_name = selected_model_info.get('display_name', 'Unknown Model') 
            raw_response_summary = response_content_str[:200] + "..." if len(response_content_str) > 200 else response_content_str 
            logger.error(f"LLM Raw Response ({model_display_name}): {raw_response_summary}")
            # This print is important user feedback
            print(f"[!] Failed to parse JSON response from {model_display_name}.")
        
        return parsed_json, image_with_grid, total_tokens
            
    except Exception as e:
        logger.error(f"Error in LLM analysis dispatcher ({selected_model_info['display_name']}): {e}", exc_info=True)
        # This print is important user feedback
        print(f"[!] Error during LLM analysis with {selected_model_info['display_name']}.")
        return None, image_with_grid, total_tokens

def execute_clicks(click_list, window_details):
    """Executes clicks. LLM provides click objects with cell numbers and a reason."""
    if not click_list or not window_details:
        if not click_list: 
            print("  No clicks were planned by the LLM for execution.") 
        return
    
    content_height = window_details["height"] 
    content_width = window_details["width"]
    content_left = window_details["left"]
    content_top = window_details["top"]

    try:
        for idx, click_obj in enumerate(click_list, 1): 
            if not (isinstance(click_obj, dict) and 
                    "coordinates" in click_obj and 
                    isinstance(click_obj["coordinates"], int) and 
                    click_obj["coordinates"] > 0 and
                    "reason" in click_obj): 
                logger.warning(f"  Skipping invalid click object format from LLM: {click_obj}")
                print(f"  [!] Invalid click data for click {idx}. Skipping.")
                continue

            cell_number = click_obj["coordinates"]
            click_reason = click_obj.get("reason", "No reason") 

            # Get pixel coordinates from cell number using grid.py with actual image dimensions
            coords = get_cell_coordinates(
                cell_number,
                image_width=content_width,
                image_height=content_height,
                cell_size=40  # Using the same cell size as in grid.py
            )
            if not coords:
                logger.error(f"Invalid cell number: {cell_number}")
                continue

            # Convert to screen coordinates
            screen_x = content_left + coords[0]
            screen_y = content_top + coords[1]
            
            # Validate if the click is within the window bounds
            if (screen_x < content_left or screen_x > content_left + content_width or
                screen_y < content_top or screen_y > content_top + content_height):
                logger.warning(f"Click coordinates ({screen_x}, {screen_y}) outside window bounds. Skipping.")
                print(f"  [!] Click {idx} would be outside window bounds. Skipping.")
                continue
            
            print(f"  > Clicking for: '{click_reason}' (Cell: {cell_number} -> Screen: {screen_x},{screen_y})")
            
            pyautogui.click(screen_x, screen_y)
            logger.debug(f"    pyautogui: Clicked at screen ({screen_x}, {screen_y}) for reason: '{click_reason}' (Cell: {cell_number})")
            
            if idx < len(click_list):
                 logger.debug(f"    Waiting {CLICK_INTERVAL}s before next click in batch.")
                 time.sleep(CLICK_INTERVAL)
                 
    except Exception as e:
        logger.error(f"Unexpected error executing clicks with pyautogui: {e}", exc_info=True)
        print(f"  [!] Error during click execution: {e}")

def save_session_data(session_path, iteration_count, screenshot_img_to_save, llm_data):
    if not screenshot_img_to_save:
        logger.warning(f"Iteration {iteration_count}: No screenshot image provided to save.")
        return
    try:
        timestamp = datetime.now().strftime("%H%M%S_%f")[:-3] 
        
        screenshot_filename = f"iter_{iteration_count:04d}_shot_{timestamp}.png"
        screenshot_img_to_save.save(session_path / screenshot_filename)
        # Changed from INFO to DEBUG for cleaner console
        logger.debug(f"Saved screenshot: {session_path / screenshot_filename}")
        
        if llm_data:
            llm_filename = f"iter_{iteration_count:04d}_llm_{timestamp}.json"
            with open(session_path / llm_filename, 'w') as f:
                json.dump(llm_data, f, indent=2)
            # Changed from INFO to DEBUG for cleaner console
            logger.debug(f"Saved LLM data: {session_path / llm_filename}")
        else:
            # Changed from INFO to DEBUG
            logger.debug(f"Iteration {iteration_count}: No LLM data to save for this iteration.")
            
    except Exception as e:
        logger.error(f"Error saving session data for iteration {iteration_count}: {e}", exc_info=True)

def print_iteration_summary(llm_response, window_details):
    """Prints a formatted summary of the LLM's analysis and planned clicks to the console."""
    # Main header for the LLM's response section
    print("\n" + "--- LLM Analysis & Action Plan ---")
    if llm_response and isinstance(llm_response, dict):
        print(f"  Description: {llm_response.get('description', 'N/A')}")
        print(f"  Action Plan: {llm_response.get('action_plan', 'N/A')}")
        
        click_list_llm = llm_response.get('clicks')
        if click_list_llm and isinstance(click_list_llm, list) and click_list_llm:
            print("\n  Planned Clicks:")
            if not window_details:
                logger.error("print_iteration_summary: window_details is None, cannot calculate screen coordinates.")
                print("    [!] Window details missing, cannot display screen coordinates for planned clicks.")
                for idx, click_obj in enumerate(click_list_llm, 1):
                    if isinstance(click_obj, dict) and "coordinates" in click_obj and "reason" in click_obj:
                        print(f"    {idx}. Cell: {click_obj['coordinates']}, Reason: {click_obj['reason']}")
                    else:
                        print(f"    {idx}. Invalid click object format: {click_obj}")
                print("-" * 40) # Footer for this section
                return

            for idx, click_obj in enumerate(click_list_llm, 1): 
                # Fix: Check for cell number (integer) format that execute_clicks expects
                if isinstance(click_obj, dict) and "coordinates" in click_obj and isinstance(click_obj["coordinates"], int) and click_obj["coordinates"] > 0 and "reason" in click_obj:
                    cell_number = click_obj["coordinates"]
                    click_reason = click_obj.get("reason", "N/A")

                    # Get pixel coordinates from cell number using grid.py
                    coords = get_cell_coordinates(
                        cell_number,
                        image_width=window_details["width"],
                        image_height=window_details["height"],
                        cell_size=40  # Using the same cell size as in grid.py
                    )
                    
                    if coords:
                        img_x_llm, img_y_llm = coords
                        # Convert to screen coordinates
                        screen_x = window_details["left"] + img_x_llm
                        screen_y = window_details["top"] + img_y_llm
                        
                        print(f"    {idx}. {click_reason} -> Cell: {cell_number} -> Image: ({img_x_llm},{img_y_llm}) -> Screen: ({screen_x},{screen_y})")
                    else:
                        print(f"    {idx}. {click_reason} -> Cell: {cell_number} (INVALID CELL NUMBER)")
                else:
                    print(f"    {idx}. Invalid click object format from LLM: {click_obj}")
        elif click_list_llm == []: 
            print("\n  Planned Clicks: None.") # Simpler
        else: 
            print("\n  Planned Clicks: None or invalid format.") # Simpler
    else:
        print("  Description: LLM Response not available or failed to parse.")
        print("  Action Plan: N/A")
        print("\n  Planned Clicks: None.")
    print("-" * 40) # Footer for the whole summary

class StatusWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("Game Status")
        self.root.geometry("700x900")  # Increased size for better readability
        self.closed = False

        # Create update queue
        self.update_queue = queue.Queue()
        
        # Create main frame with scrollbar
        main_frame = ttk.Frame(root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create sections
        self.create_status_section(main_frame)
        self.create_game_section(main_frame)
        self.create_chat_section(main_frame)
        
        # Initialize chat connection status
        self.chat_connected = False
        self.update_chat_status()
        
        # Set up window close handler
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Start polling for updates
        self.poll_updates()
        
    def poll_updates(self):
        """Poll the update queue and process any pending updates."""
        try:
            while not self.update_queue.empty():
                update_data = self.update_queue.get_nowait()
                self._process_update(update_data)
        except queue.Empty:
            pass
        
        if not self.closed:
            self.root.after(100, self.poll_updates)
            
    def _process_update(self, update_data):
        try:
            # Update iteration counter
            self.iteration_label.config(text=f"Iteration: {update_data['iteration']}")
            
            # Update LLM info (name only)
            self.llm_name_label.config(text=f"LLM: {update_data['llm_name']}")
            
            # Update game info
            self.game_name_label.config(text=update_data['game_name'])
            
            # Update screenshot if provided
            if update_data.get('image'):
                # Convert PIL Image to PhotoImage
                photo = ImageTk.PhotoImage(update_data['image'])
                self.screenshot_label.configure(image=photo)
                self.screenshot_label.image = photo  # Keep a reference!
            else:
                self.screenshot_label.configure(image='')
            
            # Update vision description
            self.vision_text.config(state=tk.NORMAL)
            self.vision_text.delete(1.0, tk.END)
            self.vision_text.insert(tk.END, update_data['status'])
            self.vision_text.config(state=tk.DISABLED)
            
            # Update action plan
            self.plan_text.config(state=tk.NORMAL)
            self.plan_text.delete(1.0, tk.END)
            self.plan_text.insert(tk.END, update_data['action'])
            self.plan_text.config(state=tk.DISABLED)
            
            # Update clicks
            self.clicks_text.config(state=tk.NORMAL)
            self.clicks_text.delete(1.0, tk.END)
            self.clicks_text.insert(tk.END, update_data['clicks_info'])
            self.clicks_text.config(state=tk.DISABLED)
            
            # Update chat data if provided
            if update_data.get('chat_data'):
                username, timestamp, clicks = update_data['chat_data']
                # Format timestamp
                if timestamp:
                    time_str = timestamp.strftime("%H:%M:%S")
                    self.chat_timestamp_label.config(text=f"Last Check: {time_str}")
                
                if clicks:
                    # Check if clicks is a list of dictionaries (actual click objects) or a string (status message)
                    if isinstance(clicks, list) and all(isinstance(click, dict) and 'reason' in click for click in clicks):
                        # Process actual click objects
                        suggestions_text = f"User: {username}\n"
                        for i, click in enumerate(clicks, 1):
                            suggestions_text += f"{i}. {click['reason']}\n"
                        self.chat_suggestions.config(text=suggestions_text, foreground="black")
                    elif isinstance(clicks, str):
                        # Handle string status messages
                        self.chat_suggestions.config(text=f"Status: {clicks}", foreground="blue")
                    else:
                        # Fallback for unexpected format
                        self.chat_suggestions.config(text="Invalid suggestion format", foreground="red")
                else:
                    self.chat_suggestions.config(text="No suggestions found", foreground="gray")
            else:
                self.chat_suggestions.config(text="No suggestions yet", foreground="gray")
                self.chat_timestamp_label.config(text="Last Check: Never")
                
        except Exception as e:
            print(f"Error processing update: {e}")
            logger.error(f"Error processing update: {e}")

    def update_status(self, iteration, llm_name, game_name, status, action, clicks_info, context, image, clicks, image_size, total_tokens, chat_data=None):
        """Queue an update to the status window."""
        if not self.closed:
            update_data = {
                'iteration': iteration,
                'llm_name': llm_name,
                'game_name': game_name,
                'status': status,
                'action': action,
                'clicks_info': clicks_info,
                'context': context,
                'image': image,
                'clicks': clicks,
                'image_size': image_size,
                'total_tokens': total_tokens,
                'chat_data': chat_data
            }
            self.update_queue.put(update_data)
        
    def on_close(self):
        """Handle window close event"""
        print("Status window closed by user.")
        logger.info("Status window closed by user.")
        self.closed = True
        if hasattr(self.root, 'quit'):
            self.root.quit()

    def create_status_section(self, parent):
        # Status section
        status_frame = ttk.LabelFrame(parent, text="General Information", padding="5")
        status_frame.pack(fill=tk.X, pady=5)
        
        # Status info frame
        status_info_frame = ttk.Frame(status_frame)
        status_info_frame.pack(fill=tk.X, pady=2)
        
        # Iteration counter
        ttk.Label(status_info_frame, text="Iteration:").pack(side=tk.LEFT)
        self.iteration_label = ttk.Label(status_info_frame, text="0")
        self.iteration_label.pack(side=tk.LEFT, padx=5)
        
        # LLM name
        ttk.Label(status_info_frame, text="LLM:").pack(side=tk.LEFT, padx=(10,0))
        self.llm_name_label = ttk.Label(status_info_frame, text="N/A")
        self.llm_name_label.pack(side=tk.LEFT, padx=5)
        
    def create_game_section(self, parent):
        # Game section
        game_frame = ttk.LabelFrame(parent, text="Last Iteration")
        game_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Game name
        game_info_frame = ttk.Frame(game_frame)
        game_info_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(game_info_frame, text="Game:").pack(side=tk.LEFT)
        self.game_name_label = ttk.Label(game_info_frame, text="N/A")
        self.game_name_label.pack(side=tk.LEFT, padx=5)
        
        # Screenshot display - increased size to match LLM input
        self.screenshot_label = ttk.Label(game_frame)
        self.screenshot_label.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Vision Description
        vision_frame = ttk.LabelFrame(game_frame, text="Vision Description")
        vision_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.vision_text = scrolledtext.ScrolledText(vision_frame, wrap=tk.WORD, height=4)
        self.vision_text.pack(fill=tk.X, padx=5, pady=5)
        self.vision_text.config(state=tk.DISABLED)
        
        # Action Plan
        plan_frame = ttk.LabelFrame(game_frame, text="Action Plan")
        plan_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.plan_text = scrolledtext.ScrolledText(plan_frame, wrap=tk.WORD, height=3)
        self.plan_text.pack(fill=tk.X, padx=5, pady=5)
        self.plan_text.config(state=tk.DISABLED)
        
        # Action Clicks
        clicks_frame = ttk.LabelFrame(game_frame, text="Action Clicks")
        clicks_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.clicks_text = scrolledtext.ScrolledText(clicks_frame, wrap=tk.WORD, height=3)
        self.clicks_text.pack(fill=tk.X, padx=5, pady=5)
        self.clicks_text.config(state=tk.DISABLED)
        
    def create_chat_section(self, parent):
        # Chat section
        chat_frame = ttk.LabelFrame(parent, text="Chat Integration", padding="5")
        chat_frame.pack(fill=tk.X, pady=5)
        
        # Chat status and timestamp
        chat_info_frame = ttk.Frame(chat_frame)
        chat_info_frame.pack(fill=tk.X, pady=2)
        
        self.chat_status_label = ttk.Label(chat_info_frame, text="Chat: Not connected")
        self.chat_status_label.pack(side=tk.LEFT)
        
        self.chat_timestamp_label = ttk.Label(chat_info_frame, text="Last Check: Never")
        self.chat_timestamp_label.pack(side=tk.RIGHT)
        
        # Chat suggestions
        self.chat_suggestions = ttk.Label(chat_frame, text="No suggestions yet", wraplength=400)
        self.chat_suggestions.pack(fill=tk.X, pady=2)
        
    def update_chat_status(self):
        """Update the chat connection status"""
        if not chat.TWITCH_TOKEN:  # Use chat.TWITCH_TOKEN instead of TWITCH_TOKEN
            status_text = "Chat Status: Disabled (No token set)"
            status_color = "red"
        elif is_chat_running():  # Use the new is_chat_running() function
            status_text = "Chat Status: Connected"
            status_color = "green"
        else:
            status_text = "Chat Status: Disconnected"
            status_color = "red"
        
        self.chat_status_label.config(text=status_text, foreground=status_color)

def show_model_menu():
    """Show the model selection menu."""
    while True:
        print("\n=== Model Selection Menu ===")
        print("1. Select Local Model (Ollama)")
        print("2. Select Remote Model (OpenAI/Anthropic)")
        print("3. Select Hugging Face Model")
        print("4. Back to Main Menu")

        choice = input("\nSelect an option (1-4): ").strip()

        if choice == "1":
            show_ollama_models()
        elif choice == "2":
            show_remote_models()
        elif choice == "3":
            show_huggingface_models()
        elif choice == "4":
            break
        else:
            print("[!] Invalid option. Please try again.")

def show_huggingface_models():
    """Show available Hugging Face models."""
    if not (HUGGINGFACE_TOKEN and HUGGINGFACE_TOKEN.startswith("hf_")):
        print("[!] Hugging Face token not configured. Please configure it first.")
        return None

    print("\n=== Available Hugging Face Models ===")
    models = [
        {
            "id": "google/gemma-3-27b-it",
            "name": "Gemma 3 27B",
            "description": "Google's latest Gemma model, instruction-tuned for better performance"
        },
        {
            "id": "google/gemma-3n-E4B-it-litert-preview",
            "name": "Gemma 3n E4B",
            "description": "Google's efficient Gemma 3n model, optimized for edge devices"
        },
        {
            "id": "Salesforce/blip2-opt-2.7b",
            "name": "BLIP-2 OPT 2.7B",
            "description": "Salesforce's BLIP-2 model for image understanding"
        },
        {
            "id": "microsoft/git-base-coco",
            "name": "GIT Base COCO",
            "description": "Microsoft's GIT model for image-text understanding"
        }
    ]

    for idx, model in enumerate(models, 1):
        print(f"\n{idx}. {model['name']}")
        print(f"   ID: {model['id']}")
        print(f"   Description: {model['description']}")

    while True:
        try:
            selection = input(f"\nSelect model number (1-{len(models)}) or press Enter to go back: ").strip()
            if not selection:
                return None
            
            selected_idx = int(selection) - 1
            if 0 <= selected_idx < len(models):
                selected_model = models[selected_idx]
                print(f"\n[✓] Selected Hugging Face model: {selected_model['name']}")
                return {
                    "provider_name": "Hugging Face (Remote)",
                    "model_id": selected_model["id"],
                    "display_name": f"Hugging Face: {selected_model['name']}",
                    "type": "huggingface"
                }
            else:
                print("[!] Invalid selection. Please enter a number from the list.")
        except ValueError:
            print("[!] Invalid input. Please enter a number.")

def show_ollama_models():
    """Show available Ollama models."""
    try:
        models = ollama.list().get('models', [])
        if not models:
            print("[!] No Ollama models found. Please install some models first.")
            return

        print("\n=== Available Ollama Models ===")
        for idx, model in enumerate(models, 1):
            print(f"{idx}. {model.get('model', 'Unknown')}")

        while True:
            try:
                selection = input(f"\nSelect model number (1-{len(models)}) or press Enter to go back: ").strip()
                if not selection:
                    return
                
                selected_idx = int(selection) - 1
                if 0 <= selected_idx < len(models):
                    selected_model = models[selected_idx]
                    print(f"\n[✓] Selected Ollama model: {selected_model.get('model')}")
                    return {
                        "provider_name": "Ollama (Local)",
                        "model_id": selected_model.get('model'),
                        "display_name": f"Ollama: {selected_model.get('model')}",
                        "type": "ollama"
                    }
                else:
                    print("[!] Invalid selection. Please enter a number from the list.")
            except ValueError:
                print("[!] Invalid input. Please enter a number.")
    except Exception as e:
        print(f"[!] Error listing Ollama models: {e}")
        return None

def show_remote_models():
    """Show available remote models (OpenAI/Anthropic)."""
    print("\n=== Available Remote Models ===")
    models = []

    # OpenAI Models
    if OPENAI_API_KEY and OPENAI_API_KEY.startswith("sk-") and len(OPENAI_API_KEY) > 20:
        models.append({
            "provider_name": "OpenAI (Remote)",
            "model_id": "gpt-4.1",
            "display_name": "OpenAI: GPT-4.1",
            "type": "openai"
        })
        models.append({
            "provider_name": "OpenAI (Remote)",
            "model_id": "gpt-4.1-mini",
            "display_name": "OpenAI: GPT-4.1 Mini",
            "type": "openai"
        })

    # Anthropic Models
    if ANTHROPIC_API_KEY and ANTHROPIC_API_KEY.startswith("sk-ant-") and len(ANTHROPIC_API_KEY) > 20:
        models.append({
            "provider_name": "Anthropic (Remote)",
            "model_id": "claude-3-opus-20240229",
            "display_name": "Anthropic: Claude 3 Opus",
            "type": "anthropic"
        })
        models.append({
            "provider_name": "Anthropic (Remote)",
            "model_id": "claude-3-sonnet-20240229",
            "display_name": "Anthropic: Claude 3 Sonnet",
            "type": "anthropic"
        })

    if not models:
        print("[!] No remote models available. Please configure API keys first.")
        return

    for idx, model in enumerate(models, 1):
        print(f"{idx}. {model['display_name']}")

    while True:
        try:
            selection = input(f"\nSelect model number (1-{len(models)}) or press Enter to go back: ").strip()
            if not selection:
                return
            
            selected_idx = int(selection) - 1
            if 0 <= selected_idx < len(models):
                selected_model = models[selected_idx]
                print(f"\n[✓] Selected remote model: {selected_model['display_name']}")
                return selected_model
            else:
                print("[!] Invalid selection. Please enter a number from the list.")
        except ValueError:
            print("[!] Invalid input. Please enter a number.")

class ContextMemoryWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Context Memory")
        self.root.geometry("800x600")
        self.closed = False
        
        # Create main frame
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Create sections
        self.create_context_section(main_frame)
        self.create_map_section(main_frame)
        self.create_objectives_section(main_frame)
        
        # Set up update polling
        self.update_queue = queue.Queue()
        self.root.after(100, self.poll_updates)
        
        # Set up close handler
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Store the last update data
        self.last_update = None

    def create_context_section(self, parent):
        # Context section
        context_frame = ttk.LabelFrame(parent, text="Game Context")
        context_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create text widget with scrollbar
        self.context_text = scrolledtext.ScrolledText(context_frame, wrap=tk.WORD, height=10)
        self.context_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.context_text.config(state=tk.DISABLED)

    def create_map_section(self, parent):
        # Map section
        map_frame = ttk.LabelFrame(parent, text="Game Map")
        map_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create text widget with scrollbar
        self.map_text = scrolledtext.ScrolledText(map_frame, wrap=tk.WORD, height=10)
        self.map_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.map_text.config(state=tk.DISABLED)

    def create_objectives_section(self, parent):
        # Objectives section
        objectives_frame = ttk.LabelFrame(parent, text="Game Objectives")
        objectives_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create text widget with scrollbar
        self.objectives_text = scrolledtext.ScrolledText(objectives_frame, wrap=tk.WORD, height=10)
        self.objectives_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.objectives_text.config(state=tk.DISABLED)

    def update_context(self, game_instructions, last_actions, game_context, game_map=None, game_objectives=None):
        """Update the context window with new information."""
        if not self.closed:
            update_data = {
                'game_context': game_context,
                'game_map': game_map,
                'game_objectives': game_objectives
            }
            self.update_queue.put(update_data)

    def poll_updates(self):
        """Poll for updates from the queue."""
        try:
            while True:
                try:
                    update_data = self.update_queue.get_nowait()
                    if update_data:
                        # Update context
                        self.context_text.config(state=tk.NORMAL)
                        self.context_text.delete(1.0, tk.END)
                        self.context_text.insert(tk.END, update_data['game_context'])
                        self.context_text.config(state=tk.DISABLED)
                        
                        # Update map
                        self.map_text.config(state=tk.NORMAL)
                        self.map_text.delete(1.0, tk.END)
                        if update_data.get('game_map'):
                            self.map_text.insert(tk.END, update_data['game_map'])
                        else:
                            self.map_text.insert(tk.END, "No map data available")
                        self.map_text.config(state=tk.DISABLED)
                        
                        # Update objectives
                        self.objectives_text.config(state=tk.NORMAL)
                        self.objectives_text.delete(1.0, tk.END)
                        if update_data.get('game_objectives'):
                            self.objectives_text.insert(tk.END, update_data['game_objectives'])
                        else:
                            self.objectives_text.insert(tk.END, "No objectives available")
                        self.objectives_text.config(state=tk.DISABLED)
                        
                        # Store the last update
                        self.last_update = update_data
                except queue.Empty:
                    break
        except Exception as e:
            print(f"Error in poll_updates: {e}")
            logger.error(f"Error in poll_updates: {e}")
        
        # Schedule next poll if window is still open
        if not self.closed:
            self.root.after(100, self.poll_updates)

    def on_close(self):
        print("Context memory window closed by user.")
        logger.info("Context memory window closed by user.")
        self.closed = True
        if hasattr(self.root, 'quit'):
            self.root.quit()

class ChatMonitorWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Twitch Chat Monitor")
        self.root.geometry("500x600")
        self.root.attributes('-topmost', True)
        self.closed = False
        
        # Create main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Chat status section
        status_frame = ttk.LabelFrame(main_frame, text="Chat Connection Status", padding="5")
        status_frame.pack(fill=tk.X, pady=5)
        
        self.connection_status = ttk.Label(status_frame, text="Checking connection...", font=("Arial", 10, "bold"))
        self.connection_status.pack(fill=tk.X, pady=2)
        
        self.stats_label = ttk.Label(status_frame, text="Stats: Loading...", font=("Arial", 9))
        self.stats_label.pack(fill=tk.X, pady=2)
        
        # Chat messages section
        messages_frame = ttk.LabelFrame(main_frame, text="Recent Chat Messages", padding="5")
        messages_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Create text widget with scrollbar
        text_frame = ttk.Frame(messages_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        self.messages_text = tk.Text(text_frame, wrap=tk.WORD, font=("Arial", 9), state=tk.DISABLED)
        messages_scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=self.messages_text.yview)
        self.messages_text.configure(yscrollcommand=messages_scrollbar.set)
        self.messages_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        messages_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Auto-scroll to bottom
        self.auto_scroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(messages_frame, text="Auto-scroll to bottom", 
                       variable=self.auto_scroll).pack(anchor="w", pady=2)
        
        # Commands section
        commands_frame = ttk.LabelFrame(main_frame, text="Valid Commands", padding="5")
        commands_frame.pack(fill=tk.X, pady=5)
        
        commands_text = (
            "Valid chat commands:\n"
            "• click 42 - Click on cell number 42\n"
            "• click (123, 456) - Click at pixel coordinates\n"
            "• Click commands are processed every 5 iterations"
        )
        ttk.Label(commands_frame, text=commands_text, font=("Arial", 9), 
                 foreground="darkblue").pack(anchor="w")
        
        # Recent clicks section
        recent_frame = ttk.LabelFrame(main_frame, text="Recent User Clicks", padding="5")
        recent_frame.pack(fill=tk.X, pady=5)
        
        self.recent_clicks_label = ttk.Label(recent_frame, text="No recent clicks", 
                                           font=("Arial", 9), foreground="gray")
        self.recent_clicks_label.pack(fill=tk.X, pady=2)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_queue = queue.Queue()
        self.last_message_count = 0
        
        # Start monitoring
        self.monitor_chat()
        self.poll_updates()
        
    def monitor_chat(self):
        """Monitor chat messages and update display"""
        try:
            if is_chat_running():
                # Get chat statistics
                stats = get_chat_stats()
                stats_text = (f"Connected | Messages: {stats['total_messages']} | "
                            f"Users: {stats['unique_users']} | Recent: {stats['recent_activity']}")
                
                self.update_queue.put(("status", "Chat Status: Connected ✓", "green"))
                self.update_queue.put(("stats", stats_text, "black"))
                
                # Get recent messages
                if hasattr(chat, '_chat_messages') and chat._chat_messages:
                    messages = chat._chat_messages[-50:]  # Last 50 messages
                    if len(messages) > self.last_message_count:
                        # New messages available
                        new_messages = messages[self.last_message_count:]
                        for msg in new_messages:
                            timestamp = msg['timestamp'].strftime("%H:%M:%S")
                            user = msg['user']
                            content = msg['content']
                            has_clicks = len(msg['clicks']) > 0
                            
                            # Format message
                            if has_clicks:
                                formatted_msg = f"[{timestamp}] {user}: {content} 🎯\n"
                                self.update_queue.put(("message", formatted_msg, "darkgreen"))
                            else:
                                formatted_msg = f"[{timestamp}] {user}: {content}\n"
                                self.update_queue.put(("message", formatted_msg, "black"))
                        
                        self.last_message_count = len(messages)
                
                # Get recent user clicks
                username, timestamp, clicks = get_recent_user_clicks()
                if username and clicks:
                    time_str = timestamp.strftime("%H:%M:%S") if timestamp else "Unknown"
                    clicks_text = f"Last: {username} at {time_str} ({len(clicks)} clicks)"
                    self.update_queue.put(("recent_clicks", clicks_text, "darkgreen"))
                else:
                    self.update_queue.put(("recent_clicks", "No recent clicks", "gray"))
                    
            else:
                self.update_queue.put(("status", "Chat Status: Disconnected ✗", "red"))
                self.update_queue.put(("stats", "Not connected to chat", "red"))
                
        except Exception as e:
            self.update_queue.put(("status", f"Chat Status: Error - {str(e)}", "red"))
        
        # Schedule next update
        if not self.closed:
            self.root.after(2000, self.monitor_chat)  # Update every 2 seconds
    
    def poll_updates(self):
        """Process queued updates"""
        try:
            while not self.update_queue.empty():
                update_type, text, color = self.update_queue.get_nowait()
                
                if update_type == "status":
                    self.connection_status.config(text=text, foreground=color)
                elif update_type == "stats":
                    self.stats_label.config(text=text, foreground=color)
                elif update_type == "message":
                    self.messages_text.config(state=tk.NORMAL)
                    self.messages_text.insert(tk.END, text)
                    self.messages_text.config(state=tk.DISABLED)
                    
                    # Auto-scroll to bottom if enabled
                    if self.auto_scroll.get():
                        self.messages_text.see(tk.END)
                        
                    # Keep only last 1000 lines to prevent memory issues
                    lines = self.messages_text.get("1.0", tk.END).count('\n')
                    if lines > 1000:
                        self.messages_text.config(state=tk.NORMAL)
                        self.messages_text.delete("1.0", "100.0")  # Delete first 100 lines
                        self.messages_text.config(state=tk.DISABLED)
                        
                elif update_type == "recent_clicks":
                    self.recent_clicks_label.config(text=text, foreground=color)
                    
        except queue.Empty:
            pass
        
        if not self.closed:
            self.root.after(100, self.poll_updates)
    
    def on_close(self):
        """Handle window close event"""
        print("Chat monitor window closed by user.")
        logger.info("Chat monitor window closed by user.")
        self.closed = True
        if hasattr(self.root, 'quit'):
            self.root.quit()

def get_strategy_update_prompt(descriptions, current_context):
    """Generate a prompt for the LLM to update the game strategy."""
    return f"""You are an AI playing Maniac Mansion. Review the following sequence of observations and the current game context to formulate a mid-term strategy.

Current Game Context:
{current_context}

Recent Observations (in chronological order):
{chr(10).join(f"{i+1}. {desc}" for i, desc in enumerate(descriptions))}

Based on these observations and the current context, formulate a new game strategy that:
1. Summarizes what we've learned about the game state
2. Identifies any patterns or recurring elements
3. Suggests a focused approach for the next phase of gameplay
4. Updates our understanding of the game's mechanics and puzzles

Output your response in this format:
```json
{{
    "summary": "Brief summary of what we've learned",
    "patterns": "Key patterns or recurring elements noticed",
    "strategy": "Specific strategy for the next phase",
    "mechanics": "Updated understanding of game mechanics"
}}
```"""

def update_game_context(selected_model_info, descriptions, current_context):
    """Update the game context based on accumulated descriptions."""
    global LLM_GAME_CONTEXT
    
    try:
        prompt = get_strategy_update_prompt(descriptions, current_context)
        
        if selected_model_info['type'] == "ollama":
            response = ollama.generate(
                model=selected_model_info['model_id'],
                prompt=prompt,
                format="json",
                stream=False
            )
            strategy_json = json.loads(response['response'])
        elif selected_model_info['type'] == "openai":
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=selected_model_info['model_id'],
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You are an AI playing Maniac Mansion, analyzing game progress to update strategy."},
                    {"role": "user", "content": prompt}
                ]
            )
            strategy_json = json.loads(response.choices[0].message.content)
        elif selected_model_info['type'] == "anthropic":
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=selected_model_info['model_id'],
                max_tokens=1024,
                system="You are an AI playing Maniac Mansion, analyzing game progress to update strategy.",
                messages=[{"role": "user", "content": prompt}]
            )
            strategy_json = json.loads(response.content[0].text)
        else:
            logger.error(f"Unsupported model type for context update: {selected_model_info['type']}")
            return False

        # Update the global context with the new strategy
        new_context = f"""Current Game State:
{strategy_json['summary']}

Identified Patterns:
{strategy_json['patterns']}

Current Strategy:
{strategy_json['strategy']}

Game Mechanics Understanding:
{strategy_json['mechanics']}"""

        LLM_GAME_CONTEXT = new_context
        logger.info("Game context updated with new strategy")
        return True

    except Exception as e:
        logger.error(f"Error updating game context: {e}", exc_info=True)
        return False

def get_map_update_prompt(descriptions, current_map):
    """Generate a prompt for the LLM to update the game map."""
    return f"""You are an AI playing Monkey Island 2. Review the following sequence of observations and the current map to update the game's room connections.

Current Map:
{current_map}

Recent Observations (in chronological order):
{chr(10).join(f"{i+1}. {desc}" for i, desc in enumerate(descriptions))}

Based on these observations and the current map, create an updated map that:
1. Lists all discovered rooms/locations. Please group similar room descriptions into a single room, do not create multiple rooms for the same location.
2. Shows how rooms are connected (e.g., "Room A connects to Room B via door")
3. Includes any special notes about rooms (e.g., "Room C has a locked chest")
4. Maintains previous map information while adding new discoveries triying to create a mental map of the game

Output your response in this format:
```json
{{
    "rooms": [
        {{
            "name": "Room Name",
            "connections": ["Connected to Room X via door", "Connected to Room Y via passage"],
            "notes": "Special features or important items in this room"
        }}
    ],
    "map_summary": "Brief summary of the current game world structure"
}}
```"""

def get_objectives_update_prompt(descriptions, current_objectives):
    """Generate a prompt for the LLM to update the game objectives."""
    return f"""You are an AI playing a graphic adventure game. Review the following sequence of observations and current objectives to update the game's long term goals.

Current Objectives:
{current_objectives}

Recent Observations (in chronological order):
{chr(10).join(f"{i+1}. {desc}" for i, desc in enumerate(descriptions))}

Based on these observations and current objectives, create an updated list of objectives that:
1. Includes both immediate and long-term goals
2. Prioritizes objectives based on available information
3. Notes any completed objectives
4. Maintains previous objectives while adding new ones
5. Includes any clues or hints found

Output your response in this format:
```json
{{
    "objectives": [
        {{
            "priority": "High/Medium/Low",
            "description": "Clear description of the objective",
            "status": "Active/Completed/Blocked",
            "clues": ["Clue 1", "Clue 2"]
        }}
    ],
    "summary": "Brief summary of current game progress and next steps"
}}
```"""

def update_game_map(selected_model_info, descriptions, current_map):
    """Update the game map based on accumulated descriptions."""
    global GAME_MAP_GRAPH
    
    try:
        prompt = get_map_update_prompt(descriptions, current_map)
        
        if selected_model_info['type'] == "ollama":
            response = ollama.generate(
                model=selected_model_info['model_id'],
                prompt=prompt,
                format="json",
                stream=False
            )
            map_json = json.loads(response['response'])
        elif selected_model_info['type'] == "openai":
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=selected_model_info['model_id'],
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You are an AI playing Monkey Island 2, analyzing game progress to update the map."},
                    {"role": "user", "content": prompt}
                ]
            )
            map_json = json.loads(response.choices[0].message.content)
        elif selected_model_info['type'] == "anthropic":
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=selected_model_info['model_id'],
                max_tokens=1024,
                system="You are an AI playing Monkey Island 2, analyzing game progress to update the map.",
                messages=[{"role": "user", "content": prompt}]
            )
            map_json = json.loads(response.content[0].text)
        else:
            logger.error(f"Unsupported model type for map update: {selected_model_info['type']}")
            return False

        # Format the map data for display
        map_text = "Game Map:\n\n"
        for room in map_json['rooms']:
            map_text += f"Room: {room['name']}\n"
            map_text += "Connections:\n"
            for conn in room['connections']:
                map_text += f"- {conn}\n"
            if room['notes']:
                map_text += f"Notes: {room['notes']}\n"
            map_text += "\n"
        map_text += f"\nMap Summary:\n{map_json['map_summary']}"

        GAME_MAP_GRAPH = map_text
        logger.info("Game map updated successfully")
        return True

    except Exception as e:
        logger.error(f"Error updating game map: {e}", exc_info=True)
        return False

def update_game_objectives(selected_model_info, descriptions, current_objectives):
    """Update the game objectives based on accumulated descriptions."""
    global GAME_OBJECTIVES
    
    try:
        prompt = get_objectives_update_prompt(descriptions, current_objectives)
        
        if selected_model_info['type'] == "ollama":
            response = ollama.generate(
                model=selected_model_info['model_id'],
                prompt=prompt,
                format="json",
                stream=False
            )
            objectives_json = json.loads(response['response'])
        elif selected_model_info['type'] == "openai":
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=selected_model_info['model_id'],
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You are an AI playing Monkey Island 2, analyzing game progress to update objectives."},
                    {"role": "user", "content": prompt}
                ]
            )
            objectives_json = json.loads(response.choices[0].message.content)
        elif selected_model_info['type'] == "anthropic":
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=selected_model_info['model_id'],
                max_tokens=1024,
                system="You are an AI playing Monkey Island 2, analyzing game progress to update objectives.",
                messages=[{"role": "user", "content": prompt}]
            )
            objectives_json = json.loads(response.content[0].text)
        else:
            logger.error(f"Unsupported model type for objectives update: {selected_model_info['type']}")
            return False

        # Format the objectives data for display
        objectives_text = "Game Objectives:\n\n"
        for obj in objectives_json['objectives']:
            objectives_text += f"[{obj['priority']}] {obj['description']}\n"
            objectives_text += f"Status: {obj['status']}\n"
            if obj['clues']:
                objectives_text += "Clues:\n"
                for clue in obj['clues']:
                    objectives_text += f"- {clue}\n"
            objectives_text += "\n"
        objectives_text += f"\nProgress Summary:\n{objectives_json['summary']}"

        GAME_OBJECTIVES = objectives_text
        logger.info("Game objectives updated successfully")
        return True

    except Exception as e:
        logger.error(f"Error updating game objectives: {e}", exc_info=True)
        return False

# --- Safe Status Window Update Functions ---
def safe_status_update(status_window_ref, iteration, llm_name, game_name, status, action, clicks_info, context, image, clicks, image_size, total_tokens, chat_data=None):
    """Safely update the status window with error handling."""
    try:
        if hasattr(status_window_ref, 'closed') and not status_window_ref.closed:
            status_window_ref.update_status(
                iteration, llm_name, game_name, status, action, clicks_info, 
                context, image, clicks, image_size, total_tokens, chat_data
            )
            print(f"[DEBUG] Status window updated successfully for iteration {iteration}")
            return True
        else:
            print(f"[!] Status window is closed or invalid, cannot update iteration {iteration}")
            return False
    except Exception as e:
        print(f"[!] Error updating status window for iteration {iteration}: {e}")
        logger.error(f"Error updating status window for iteration {iteration}: {e}", exc_info=True)
        return False

def safe_context_update(context_window_ref, game_instructions, last_actions, game_context, game_map=None, game_objectives=None):
    """Safely update the context window with error handling."""
    try:
        if hasattr(context_window_ref, 'closed') and not context_window_ref.closed:
            context_window_ref.update_context(game_instructions, last_actions, game_context, game_map, game_objectives)
            print(f"[DEBUG] Context window updated successfully")
            return True
        else:
            print(f"[!] Context window is closed or invalid, cannot update")
            return False
    except Exception as e:
        print(f"[!] Error updating context window: {e}")
        logger.error(f"Error updating context window: {e}", exc_info=True)
        return False

# --- Main Application Logic (to be run in a thread) ---
# Renamed main_loop to game_logic_thread_target
def game_logic_thread_target(status_window_ref, context_window_ref, chat_monitor_ref, chat_enabled): # Add chat_monitor_ref parameter
    global SELECTED_GAME_WINDOW_TITLE, SELECTED_GAME_WINDOW_ID, selected_llm_info, LLM_GAME_CONTEXT, TEMP_DESCRIPTIONS, LLM_LAST_ACTIONS, GAME_MAP_GRAPH, GAME_OBJECTIVES

    # Initialize global variables if not already set
    if 'LLM_LAST_ACTIONS' not in globals():
        global LLM_LAST_ACTIONS
        LLM_LAST_ACTIONS = []
    if 'TEMP_DESCRIPTIONS' not in globals():
        global TEMP_DESCRIPTIONS
        TEMP_DESCRIPTIONS = []
    if 'GAME_MAP_GRAPH' not in globals():
        global GAME_MAP_GRAPH
        GAME_MAP_GRAPH = "No map data available yet."
    if 'GAME_OBJECTIVES' not in globals():
        global GAME_OBJECTIVES
        GAME_OBJECTIVES = "No objectives identified yet."

    # Store last valid versions of map and objectives
    last_valid_map = GAME_MAP_GRAPH
    last_valid_objectives = GAME_OBJECTIVES

    # Initial console prints for setup are fine here as they happen before GUI typically shows
    print("Starting AI Player setup (in background thread)...") 
    logger.info("Starting AI Player setup...")

    if not check_x11_tools(): 
        return 

    select_target_window() 

    active_session_dir = create_session_directory()
    if not active_session_dir: 
        return
    
    print(f"Initializing AI Player for Maniac Mansion...")
    print(f"Session data will be saved in: {active_session_dir}") 
    logger.info(f"Initializing Maniac Mansion AI Player (PID: {os.getpid()})")
    logger.info(f"Session data will be saved in: {active_session_dir}")

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05 

    llm_providers = get_llm_providers() 
    if not llm_providers:
        print("[!] No LLM models available. Check configuration and Ollama setup. Exiting game logic thread.") 
        safe_status_update(status_window_ref, 0, "N/A", "N/A", "Setup Error", "No LLM models available.", "N/A", LLM_GAME_CONTEXT, None, None, None, None)
        safe_context_update(context_window_ref, GAME_INSTRUCTIONS, LLM_LAST_ACTIONS, LLM_GAME_CONTEXT)
        return
    
    selected_llm_info = select_llm_model(llm_providers) 
    if not selected_llm_info:
        print("[!] No LLM model selected. Exiting game logic thread.") 
        safe_status_update(status_window_ref, 0, "N/A", "N/A", "Setup Error", "No LLM model selected.", "N/A", LLM_GAME_CONTEXT, None, None, None, None)
        safe_context_update(context_window_ref, GAME_INSTRUCTIONS, LLM_LAST_ACTIONS, LLM_GAME_CONTEXT)
        return

    print(f"Targeting: '{SELECTED_GAME_WINDOW_TITLE}' (ID: {SELECTED_GAME_WINDOW_ID or 'Search by name'})")
    print(f"Using LLM: {selected_llm_info['display_name']}.")
    print(f"Chat Integration: {'Enabled' if chat_enabled else 'Disabled'} (initialized at startup)")
    print("Setup complete. Starting main game loop in background thread...")
    logger.info(f"Setup complete. Using LLM: {selected_llm_info['display_name']}. Targeting window: '{SELECTED_GAME_WINDOW_TITLE}' (ID: {SELECTED_GAME_WINDOW_ID or 'N/A'}). Chat: {'Enabled' if chat_enabled else 'Disabled'}.")


    # Test visualization of common coordinates
    print("\n=== Testing Grid System and Random Cells ===")
    game_window_details = find_game_window_details(SELECTED_GAME_WINDOW_TITLE, SELECTED_GAME_WINDOW_ID)
    if game_window_details:
        current_screenshot = capture_screenshot_of_region(game_window_details)
        if current_screenshot:
            # Add the numbered grid overlay
            grid_image = add_numbered_grid_to_image(current_screenshot)
            if grid_image:
                # Test 6 random cells
                test_cells = random.sample(range(1, 193), 6)  # Random 6 cells from 1-192
                test_clicks = []
                
                # Draw big points on the random cells
                img_with_points = grid_image.copy()
                draw = ImageDraw.Draw(img_with_points)
                point_radius = 15  # Bigger radius for better visibility
                
                for cell_number in test_cells:
                    # Get pixel coordinates from cell number
                    coords = get_cell_coordinates(cell_number)
                    if coords:
                        x, y = coords
                        # Draw a filled circle with a black outline
                        draw.ellipse(
                            [x - point_radius, y - point_radius, x + point_radius, y + point_radius],
                            fill=(255, 0, 255, 180),  # Semi-transparent magenta
                            outline=(0, 0, 0, 255),   # Black outline
                            width=2
                        )
                        # Add the cell number
                        draw.text((x + point_radius + 5, y - 10), f"Cell {cell_number}", fill=(0, 0, 0, 255))
                        
                        # Add to test clicks list
                        test_clicks.append({
                            "coordinates": cell_number,
                            "reason": f"Test click on cell {cell_number}"
                        })
                
                # Update status window with the test visualization
                status_window_ref.update_status(
                    0,  # Step 0 for test
                    selected_llm_info['display_name'],
                    f"{SELECTED_GAME_WINDOW_TITLE} (Grid Test)",
                    "Testing grid system with random cells",
                    "Verifying cell number to coordinate mapping",
                    f"Testing cells: {', '.join(map(str, test_cells))}",
                    LLM_GAME_CONTEXT,
                    img_with_points,
                    test_clicks,
                    f"{img_with_points.size[0]}x{img_with_points.size[1]}",
                    None
                )
                
                print(f"Test visualization displayed. Testing cells: {', '.join(map(str, test_cells))}")
                print("Waiting 4 seconds before starting main loop...")
                time.sleep(4)
    
    iteration_count = 0
    try:
        while not status_window_ref.closed and not context_window_ref.closed and not chat_monitor_ref.closed:
            iteration_count += 1
            print(f"\n\n{'=' * 20} Iteration: {iteration_count} {'=' * 20}")

            # Initialize current_game_window_name_for_status early to avoid NameError
            game_window_details = find_game_window_details(SELECTED_GAME_WINDOW_TITLE, SELECTED_GAME_WINDOW_ID)
            current_game_window_name_for_status = SELECTED_GAME_WINDOW_TITLE # Default
            if game_window_details and game_window_details.get('window_id'):
                current_game_window_name_for_status = f"{SELECTED_GAME_WINDOW_TITLE} (ID: {game_window_details.get('window_id')})"
            elif not game_window_details:
                 current_game_window_name_for_status = f"{SELECTED_GAME_WINDOW_TITLE} (Not Found)"

            # Check if this is a context update iteration
            is_context_update_iteration = (iteration_count % DESCRIPTIONS_BEFORE_UPDATE) == 0

            if is_context_update_iteration and TEMP_DESCRIPTIONS:
                print("\n=== THINKING AND CREATING A LONG TERM STRATEGY (UPDATING GAME CONTEXT) ===")
                status_window_ref.update_status(
                    iteration_count,
                    selected_llm_info['display_name'],
                    current_game_window_name_for_status,
                    "Analyzing game progress and updating strategy...",
                    "Creating long-term game plan",
                    "Strategy update in progress",
                    LLM_GAME_CONTEXT,
                    None,
                    None,
                    None,
                    None
                )
                
                # Store current descriptions and actions for all three updates
                current_descriptions = TEMP_DESCRIPTIONS.copy()
                current_actions = LLM_LAST_ACTIONS.copy()
                
                # 1. Update game context
                print("\n1. Updating game context...")
                if update_game_context(selected_llm_info, current_descriptions, LLM_GAME_CONTEXT):
                    print("✓ Game context updated successfully!")
                else:
                    print("✗ Failed to update game context, continuing with current context.")

                # 2. Update game map
                print("\n2. Updating game map...")
                if update_game_map(selected_llm_info, current_descriptions, GAME_MAP_GRAPH):
                    print("✓ Game map updated successfully!")
                    last_valid_map = GAME_MAP_GRAPH  # Store the new valid map
                else:
                    print("✗ Failed to update game map, continuing with current map.")
                    GAME_MAP_GRAPH = last_valid_map  # Restore last valid map

                # 3. Update game objectives
                print("\n3. Updating game objectives...")
                if update_game_objectives(selected_llm_info, current_descriptions, GAME_OBJECTIVES):
                    print("✓ Game objectives updated successfully!")
                    last_valid_objectives = GAME_OBJECTIVES  # Store the new valid objectives
                else:
                    print("✗ Failed to update game objectives, continuing with current objectives.")
                    GAME_OBJECTIVES = last_valid_objectives  # Restore last valid objectives

                # Only clear the accumulated data after all updates are complete
                print("\nClearing accumulated data for next update cycle...")
                TEMP_DESCRIPTIONS = []
                LLM_LAST_ACTIONS = []

                # Update both windows with the latest information
                status_window_ref.update_status(
                    iteration_count,
                    selected_llm_info['display_name'],
                    current_game_window_name_for_status,
                    "Strategy update complete",
                    "Continuing with game exploration",
                    "Ready for next actions",
                    LLM_GAME_CONTEXT,
                    None,
                    None,
                    None,
                    None
                )
                context_window_ref.update_context(GAME_INSTRUCTIONS, LLM_LAST_ACTIONS, LLM_GAME_CONTEXT, GAME_MAP_GRAPH, GAME_OBJECTIVES)

                print("\n=== Strategy Update Complete ===")
                print("Waiting for next game iteration...")
                time.sleep(SCREENSHOT_INTERVAL)  # Give time to read the update messages

            # Don't re-fetch game_window_details if we already have it from above
            if not game_window_details:
                game_window_details = find_game_window_details(SELECTED_GAME_WINDOW_TITLE, SELECTED_GAME_WINDOW_ID)
                # Update the status name again in case it changed
                if game_window_details and game_window_details.get('window_id'):
                    current_game_window_name_for_status = f"{SELECTED_GAME_WINDOW_TITLE} (ID: {game_window_details.get('window_id')})"
                elif not game_window_details:
                     current_game_window_name_for_status = f"{SELECTED_GAME_WINDOW_TITLE} (Not Found)"

            # Initialize per-iteration variables for status updates
            llm_desc = "N/A"
            llm_plan = "N/A"
            clicks_info_str = "N/A"
            image_to_save_for_session = None 
            raw_click_coords_for_status = None
            total_tokens = None

            if not game_window_details:
                print(f"[!] Game window '{SELECTED_GAME_WINDOW_TITLE}' not found. Retrying in {SCREENSHOT_INTERVAL}s...") 
                llm_desc = "Game window not found."
                llm_plan = "Waiting for game window..."
                safe_status_update(
                    status_window_ref,
                    iteration_count,
                    selected_llm_info.get('display_name', 'N/A') if 'selected_llm_info' in globals() and selected_llm_info else 'N/A',
                    current_game_window_name_for_status,
                    llm_desc,
                    llm_plan,
                    clicks_info_str,
                    LLM_GAME_CONTEXT,
                    None,
                    None,
                    None,
                    None
                )
                safe_context_update(context_window_ref, GAME_INSTRUCTIONS, LLM_LAST_ACTIONS, LLM_GAME_CONTEXT)
                time.sleep(SCREENSHOT_INTERVAL) 
                if status_window_ref.closed or context_window_ref.closed or chat_monitor_ref.closed: break
                continue
            
            print(f"Processing game screen from '{SELECTED_GAME_WINDOW_TITLE}' (ID: {game_window_details.get('window_id', 'N/A')})")
            print(f"Sending to LLM: {selected_llm_info['display_name']} for analysis...")
            current_screenshot = capture_screenshot_of_region(game_window_details)

            if not current_screenshot:
                print(f"[!] Failed to capture screenshot. Retrying in {SCREENSHOT_INTERVAL}s...")
                llm_desc = "Failed to capture screenshot."
                # image_to_save_for_session remains None
                # raw_click_coords_for_status remains None
                status_window_ref.update_status(
                    iteration_count,
                    selected_llm_info['display_name'],
                    current_game_window_name_for_status,
                    llm_desc,
                    llm_plan, # Stays "N/A"
                    clicks_info_str, # Stays "N/A"
                    LLM_GAME_CONTEXT, # Pass context
                    None, # No image
                    None, # No clicks
                    None, # No image resolution
                    None  # No token size
                )
                time.sleep(SCREENSHOT_INTERVAL)
                if status_window_ref.closed or context_window_ref.closed or chat_monitor_ref.closed: break
                continue
            
            # If we reach here, current_screenshot is valid.
            image_to_save_for_session = current_screenshot # Default to raw screenshot

            image_dimensions_for_llm = {"width": game_window_details["width"], "height": game_window_details["height"]}
            llm_analysis_json, image_processed_for_llm, total_tokens = get_llm_analysis(
                selected_llm_info, current_screenshot, image_dimensions_for_llm
            )

            if image_processed_for_llm: # If grid/etc. was drawn, use that for saving and status
                image_to_save_for_session = image_processed_for_llm
            
            if image_to_save_for_session: # Should be true if current_screenshot was valid
                save_session_data(active_session_dir, iteration_count, image_to_save_for_session, llm_analysis_json)

            print_iteration_summary(llm_analysis_json, game_window_details)
            
            clicks_to_perform = []
            # raw_click_coords_for_status is already initialized to None
            if llm_analysis_json and isinstance(llm_analysis_json, dict): # Check type
                llm_desc = llm_analysis_json.get('description', 'N/A')
                llm_plan = llm_analysis_json.get('action_plan', 'N/A')
                raw_clicks = llm_analysis_json.get('clicks')
                if isinstance(raw_clicks, list):
                    clicks_to_perform = raw_clicks
                    raw_click_coords_for_status = raw_clicks # Update if clicks are present
                    if clicks_to_perform:
                        click_lines = []
                        for idx, click_obj in enumerate(clicks_to_perform):
                            coords = click_obj.get("coordinates", "[?,?]")
                            reason = click_obj.get("reason", "No reason")
                            click_lines.append(f"{idx+1}. {reason} at {coords}")
                        clicks_info_str = "\n".join(click_lines)
                    else:
                        clicks_info_str = "No clicks planned."
                # If raw_clicks is not a list, clicks_info_str remains "N/A", raw_click_coords_for_status remains None
                
                # Update action history with this iteration's actions
                update_action_history(llm_desc, llm_plan, raw_clicks if isinstance(raw_clicks, list) else [])
            else: # llm_analysis_json is None or not a dict
                llm_desc = "LLM analysis failed or no response."
                clicks_info_str = "N/A due to LLM failure."
                # llm_plan remains "N/A"
                # raw_click_coords_for_status remains None

            # Update the status window with all information before executing clicks
            # Include current chat information for better user awareness
            current_chat_info = None
            if chat_enabled:
                chat_stats = get_chat_stats()
                current_chat_info = (
                    chat_stats.get('last_user_with_clicks'),
                    datetime.now(),
                    f"Next chat check in {CHAT_CHECK_INTERVAL - (iteration_count % CHAT_CHECK_INTERVAL)} iterations"
                )
            
            status_window_ref.update_status(
                iteration_count,
                selected_llm_info['display_name'],
                current_game_window_name_for_status,
                llm_desc,
                llm_plan,
                clicks_info_str,
                LLM_GAME_CONTEXT, # Pass context
                image_to_save_for_session, # Pass the PIL image (could be None if screenshot failed)
                raw_click_coords_for_status, # Now guaranteed to be defined
                f"{image_to_save_for_session.size[0]}x{image_to_save_for_session.size[1]}" if image_to_save_for_session else None, # Pass image resolution
                total_tokens, # Pass token size
                current_chat_info # Pass current chat information
            )
            # Always update context window with current map and objectives
            context_window_ref.update_context(
                GAME_INSTRUCTIONS,
                LLM_LAST_ACTIONS,
                LLM_GAME_CONTEXT,
                GAME_MAP_GRAPH,  # Always pass current map
                GAME_OBJECTIVES  # Always pass current objectives
            )

            if clicks_to_perform:
                print("\n  Executing Clicks on Host:") 
                execute_clicks(clicks_to_perform, game_window_details)
                # Wait for the last click to complete before proceeding
                if len(clicks_to_perform) > 0:
                    print(f"  Waiting {CLICK_INTERVAL}s after last click before next iteration...")
                    time.sleep(CLICK_INTERVAL)
            else:
                # This print is handled by execute_clicks if list is empty, or here if no analysis
                if llm_analysis_json and isinstance(llm_analysis_json.get('clicks'), list) and not llm_analysis_json.get('clicks'):
                    pass # execute_clicks will print "No clicks were planned..."
                elif not llm_analysis_json: # If analysis failed entirely
                    print("\n  No clicks planned due to LLM analysis failure.")
                # else: if clicks format was invalid, execute_clicks handles individual skips

            print(f"\n--- End of Iteration {iteration_count}. Waiting {SCREENSHOT_INTERVAL}s ---")
            # Wait for the full SCREENSHOT_INTERVAL before next iteration
            for _ in range(SCREENSHOT_INTERVAL * 10): 
                if status_window_ref.closed or context_window_ref.closed or chat_monitor_ref.closed:
                    break
                time.sleep(0.1)
            if status_window_ref.closed or context_window_ref.closed or chat_monitor_ref.closed:
                print("One or more windows closed, exiting game logic loop.")
                break

            if llm_analysis_json and isinstance(llm_analysis_json, dict):
                llm_desc = llm_analysis_json.get('description', 'N/A')
                # Store the description for context updates
                if llm_desc != 'N/A':
                    TEMP_DESCRIPTIONS.append(llm_desc)
                    # Keep only the last N descriptions
                    if len(TEMP_DESCRIPTIONS) > DESCRIPTIONS_BEFORE_UPDATE:
                        TEMP_DESCRIPTIONS = TEMP_DESCRIPTIONS[-DESCRIPTIONS_BEFORE_UPDATE:]

            # Update context window again at the end of each iteration to ensure it's always current
            context_window_ref.update_context(
                GAME_INSTRUCTIONS,
                LLM_LAST_ACTIONS,
                LLM_GAME_CONTEXT,
                GAME_MAP_GRAPH,  # Always pass current map
                GAME_OBJECTIVES  # Always pass current objectives
            )


            # Check chat every CHAT_CHECK_INTERVAL iterations
            if chat_enabled and iteration_count % CHAT_CHECK_INTERVAL == 0:
                print("\n=== Checking Twitch Chat for User Suggestions ===")
                
                # Get chat statistics
                chat_stats = get_chat_stats()
                print(f"[CHAT] Stats: {chat_stats['total_messages']} messages, {chat_stats['unique_users']} users, {chat_stats['recent_activity']} recent")
                if chat_stats['last_user_with_clicks']:
                    print(f"[CHAT] Last user with clicks: {chat_stats['last_user_with_clicks']}")

                try:
                    username, timestamp, chat_clicks = get_recent_user_clicks()
                    
                    if chat_clicks and username:
                        print(f"\n[CHAT] Found {len(chat_clicks)} clicks from last user: {username}")
                        print(f"[CHAT] Timestamp: {timestamp.strftime('%H:%M:%S') if timestamp else 'Unknown'}")
                        print("User commands:")
                        for i, click in enumerate(chat_clicks, 1):
                            print(f"  {i}. {click['reason']}")
                        
                        # Update status window with chat suggestions before executing
                        status_window_ref.update_status(
                            iteration_count,
                            selected_llm_info['display_name'],
                            current_game_window_name_for_status,
                            f"Executing clicks from {username}",
                            f"Processing {len(chat_clicks)} user commands from {timestamp.strftime('%H:%M:%S') if timestamp else 'recent'}",
                            f"Chat user: {username} | Commands: {len(chat_clicks)}",
                            LLM_GAME_CONTEXT,
                            image_to_save_for_session,
                            None,
                            f"{image_to_save_for_session.size[0]}x{image_to_save_for_session.size[1]}" if image_to_save_for_session else None,
                            total_tokens,
                            (username, timestamp, chat_clicks)  # Pass chat suggestions
                        )
                        
                        # Convert chat clicks to the format expected by execute_clicks
                        clicks_to_perform = []
                        for click in chat_clicks:
                            if click['type'] == 'cell':
                                clicks_to_perform.append({
                                    'coordinates': click['coordinates'],
                                    'reason': click['reason']
                                })
                            elif click['type'] == 'pixel':
                                # Convert pixel coordinates to cell number
                                x, y = click['coordinates']
                                cell_number = get_cell_number_from_pixel(x, y, game_window_details['width'], game_window_details['height'])
                                if cell_number:
                                    clicks_to_perform.append({
                                        'coordinates': cell_number,
                                        'reason': click['reason']
                                    })
                        
                        # Execute all clicks from the user
                        if clicks_to_perform:
                            print(f"\n[CHAT] Executing {len(clicks_to_perform)} clicks for {username}:")
                            execute_clicks(clicks_to_perform, game_window_details)
                            
                            # Update status window after execution
                            status_window_ref.update_status(
                                iteration_count,
                                selected_llm_info['display_name'],
                                current_game_window_name_for_status,
                                f"✓ Executed {len(clicks_to_perform)} clicks from {username}",
                                f"Completed user commands from {timestamp.strftime('%H:%M:%S') if timestamp else 'recent'}",
                                "\n".join(f"✓ {i+1}. {click['reason']}" for i, click in enumerate(clicks_to_perform)),
                                LLM_GAME_CONTEXT,
                                image_to_save_for_session,
                                clicks_to_perform,
                                f"{image_to_save_for_session.size[0]}x{image_to_save_for_session.size[1]}" if image_to_save_for_session else None,
                                total_tokens,
                                (username, timestamp, chat_clicks)  # Pass chat suggestions
                            )
                            print(f"[CHAT] ✓ All {len(clicks_to_perform)} clicks executed successfully")
                            continue  # Skip LLM analysis for this iteration
                        else:
                            print("[CHAT] ⚠ No valid clicks could be processed from chat commands")
                    else:
                        print("[CHAT] No recent user clicks found")
                        # Update status window to show nothing to execute from chat
                        status_window_ref.update_status(
                            iteration_count,
                            selected_llm_info['display_name'],
                            current_game_window_name_for_status,
                            "Nothing to execute from chat",
                            "No recent user suggestions found",
                            f"Last user: {chat_stats['last_user_with_clicks'] or 'None'} | Next check: {CHAT_CHECK_INTERVAL - (iteration_count % CHAT_CHECK_INTERVAL)} iterations",
                            LLM_GAME_CONTEXT,
                            image_to_save_for_session,
                            None,
                            f"{image_to_save_for_session.size[0]}x{image_to_save_for_session.size[1]}" if image_to_save_for_session else None,
                            total_tokens,
                            None  # No chat suggestions
                        )
                        
                except Exception as e:
                    print(f"[CHAT] ❌ Error checking chat: {e}")
                    # Update chat status in status window
                    status_window_ref.chat_connected = is_chat_running()
                    status_window_ref.update_chat_status()
                    status_window_ref.update_status(
                        iteration_count,
                        selected_llm_info['display_name'],
                        current_game_window_name_for_status,
                        "Chat connection error",
                        "Error while checking for user suggestions",
                        f"Error: {str(e)} | Chat running: {is_chat_running()}",
                        LLM_GAME_CONTEXT,
                        image_to_save_for_session,
                        None,
                        f"{image_to_save_for_session.size[0]}x{image_to_save_for_session.size[1]}" if image_to_save_for_session else None,
                        total_tokens,
                        None  # No chat suggestions
                    )

    except KeyboardInterrupt:
        print("\nKeyboard interrupt detected in game logic thread. Shutting down...") 
        logger.info("Keyboard interrupt detected in game logic thread. Shutting down gracefully...")
    except Exception as e:
        print(f"\n[!!!] An unexpected error occurred in the game logic thread: {e}") 
        logger.critical(f"An unexpected error occurred in the game logic thread: {e}", exc_info=True)
    finally:
        if hasattr(status_window_ref, 'closed') and not status_window_ref.closed: 
            print("Game logic thread finished. Closing status window.")
            status_window_ref.on_close() 
        if hasattr(context_window_ref, 'closed') and not context_window_ref.closed:
            print("Game logic thread finished. Closing context window.")
            context_window_ref.on_close()
        if hasattr(chat_monitor_ref, 'closed') and not chat_monitor_ref.closed:
            print("Game logic thread finished. Closing chat monitor window.")
            chat_monitor_ref.on_close()

        session_path_msg = active_session_dir if 'active_session_dir' in locals() and active_session_dir else SESSIONS_DIR
        print(f"\nAI Player game logic thread stopped. Session data saved in: {session_path_msg}") 
        logger.info(f"AI Player game logic thread stopped. Session data saved in {session_path_msg}")
        if 'active_session_dir' in locals() and active_session_dir: 
            for handler in list(logger.handlers): 
                if isinstance(handler, logging.FileHandler) and hasattr(handler, 'baseFilename') and Path(handler.baseFilename).parent == active_session_dir:
                    logger.removeHandler(handler)
                    handler.close()
                    logger.info(f"Closed session log file handler: {handler.baseFilename}")
                    break

def process_llm_analysis(analysis, window_details):
    """Process the LLM's analysis and perform the corresponding actions."""
    if not analysis:
        logger.error("No analysis to process")
        return False
        
    try:
        # Log the scene description and action plan
        logger.info(f"Scene: {analysis.get('description', 'No description provided')}")
        logger.info(f"Action Plan: {analysis.get('action_plan', 'No action plan provided')}")
        
        # Process each click in the analysis
        clicks = analysis.get('clicks', [])
        if not clicks:
            logger.info("No clicks required for this action")
            return True
            
        for click in clicks:
            cell_number = click.get('coordinates')
            reason = click.get('reason', 'No reason provided')
            
            if not cell_number:
                logger.error("No cell number provided for click")
                continue

            # Get pixel coordinates from cell number using grid.py
            coords = get_cell_coordinates(cell_number)
            if not coords:
                logger.error(f"Invalid cell number: {cell_number}")
                continue
                
            # Convert to screen coordinates
            x = window_details["left"] + coords[0]
            y = window_details["top"] + coords[1]
            
            logger.info(f"Clicking at cell {cell_number} ({x}, {y}): {reason}")
            
            # Perform the click
            pyautogui.click(x, y)
            time.sleep(0.5)  # Small delay between clicks
            
        return True
                 
    except Exception as e:
        logger.error(f"Error processing LLM analysis: {e}", exc_info=True)
        return False

def main():
    """Main function to run the game automation."""
    # Create session directory for logging
    session_dir = create_session_directory()
    if not session_dir:
        print("[!] Failed to create session directory. Exiting.")
        return
        
    # Check for required X11 tools
    if not check_x11_tools():
        print("[!] Required X11 tools not found. Exiting.")
        return
        
    # Create windows
    screenshot_root, screenshot_label = show_screenshot_window()
    status_root, status_text = show_status_window()
    
    # Select target window
    select_target_window()
    
    # Initialize Ollama
    if not initialize_ollama():
        print("[!] Failed to initialize Ollama. Exiting.")
        screenshot_root.destroy()
        status_root.destroy()
        return
        
    # Show model selection menu
    print("\n=== Model Selection Menu ===")
    print("1. Select Local Model (Ollama)")
    print("2. Select Remote Model (OpenAI/Anthropic)")
    print("3. Select Hugging Face Model")
    print("4. Exit")
    
    model_id = None
    while not model_id:
        choice = input("\nSelect an option (1-4): ").strip()
        
        if choice == "1":
            model_id = show_ollama_models()
        elif choice == "2":
            model_id = show_remote_models()
        elif choice == "3":
            model_id = show_huggingface_models()
        elif choice == "4":
            print("[✓] Exiting...")
            screenshot_root.destroy()
            status_root.destroy()
            return
        else:
            print("[!] Invalid option. Please try again.")
    
    if not model_id:
        print("[!] No model selected. Exiting.")
        screenshot_root.destroy()
        status_root.destroy()
        return
        
    # Get window details
    window_details = find_game_window_details(SELECTED_GAME_WINDOW_TITLE, SELECTED_GAME_WINDOW_ID)
    if not window_details:
        print("[!] Failed to get window details. Exiting.")
        screenshot_root.destroy()
        status_root.destroy()
        return
    
    print("\n[✓] Setup complete! Starting game automation...")
    print("[!] Press Ctrl+C to stop at any time.")
    
    # Main game loop
    while True:
        try:
            # Capture screenshot
            screenshot = capture_screenshot_of_region(window_details)
            if screenshot is None:
                print("[!] Failed to capture screenshot. Retrying in 5 seconds...")
                time.sleep(5)
                continue
                
            # Add grid overlay using the grid.py system
            grid_image = add_numbered_grid_to_image(screenshot)
            if grid_image is None:
                print("[!] Failed to add grid overlay. Retrying in 5 seconds...")
                time.sleep(5)
                continue
                
            # Update screenshot window
            update_screenshot_window(screenshot_label, grid_image)
                
            # Convert to base64
            base64_image = convert_image_to_base64(grid_image)
            if not base64_image:
                print("[!] Failed to convert image to base64. Retrying in 5 seconds...")
                time.sleep(5)
                continue
                
            # Get LLM analysis
            analysis = get_ollama_llm_analysis(model_id, base64_image, grid_image.width, grid_image.height)
            if not analysis:
                print("[!] Failed to get LLM analysis. Retrying in 5 seconds...")
                time.sleep(5)
                continue
                
            # Process the analysis
            if not process_llm_analysis(analysis, window_details):
                print("[!] Failed to process LLM analysis. Retrying in 5 seconds...")
                time.sleep(5)
                continue
                
            # Add action to history
            add_action_to_history(analysis)
            
            # Update status window
            update_status_window(status_text, analysis, window_details)
            
            # Wait before next iteration
            time.sleep(2)
            
        except KeyboardInterrupt:
            print("\n[✓] Game automation stopped by user")
            break
        except Exception as e:
            print(f"[!] Unexpected error: {e}")
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
            time.sleep(5)
            continue
            
    # Clean up
    screenshot_root.destroy()
    status_root.destroy()

if __name__ == "__main__":
    Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
    
    # Initialize Twitch chat at startup (before GUI)
    print("=== Initializing Twitch Chat ===")
    chat_enabled = start_twitch_bot()
    if chat_enabled:
        print("[✓] Twitch chat integration enabled")
    else:
        print("[!] Twitch chat integration disabled")
    
    if not (OPENAI_API_KEY and OPENAI_API_KEY.startswith("sk-") and len(OPENAI_API_KEY) > 20):
        print("[!] OpenAI API key seems invalid or is a placeholder. OpenAI models may not work.")
    if not (ANTHROPIC_API_KEY and ANTHROPIC_API_KEY.startswith("sk-ant-") and len(ANTHROPIC_API_KEY) > 20):
        print("[!] Anthropic API key seems invalid or is a placeholder. Anthropic models may not work.")
    
    # Create root window for status window
    status_root = tk.Tk()
    status_window_instance = StatusWindow(status_root)
    context_window_instance = ContextMemoryWindow()  # Create the context memory window
    chat_monitor_instance = ChatMonitorWindow()  # Create the chat monitor window
    
    # Set initial chat status in status window
    status_window_instance.chat_connected = chat_enabled
    status_window_instance.update_chat_status()
    
    # Initial update of context window
    context_window_instance.update_context(GAME_INSTRUCTIONS, LLM_LAST_ACTIONS, LLM_GAME_CONTEXT)
    
    game_thread = threading.Thread(target=game_logic_thread_target, args=(status_window_instance, context_window_instance, chat_monitor_instance, chat_enabled), daemon=True)
    game_thread.start()

    try:
        # Run all three windows' mainloops
        while True:
            # Check if any window is closed
            if status_window_instance.closed or context_window_instance.closed or chat_monitor_instance.closed:
                print("\nOne of the windows was closed. Exiting...")
                logger.info("Window closed by user. Exiting main loop.")
                break
                
            # Update both windows
            status_window_instance.root.update()
            context_window_instance.root.update()
            chat_monitor_instance.root.update()
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print("\nKeyboard interrupt detected in main thread (Tkinter). Shutting down...")
        logger.info("Keyboard interrupt in main Tkinter thread. Closing windows and signaling game thread.")
    finally:
        # Ensure both windows are closed
        if not status_window_instance.closed:
            status_window_instance.on_close() 
        if not context_window_instance.closed:
            context_window_instance.on_close()
        if not chat_monitor_instance.closed:
            chat_monitor_instance.on_close()
        
        # Wait for game thread to finish (with timeout)
        if game_thread.is_alive():
            print("Waiting for game thread to finish...")
            game_thread.join(timeout=5.0)  # Wait up to 5 seconds for thread to finish
    
    print("Exiting AI Player.")
    sys.exit(0)  # Force exit to ensure all threads are terminated