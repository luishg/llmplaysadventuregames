#!/usr/bin/env python3
"""
Point-and-click adventure game AI Player v6
Automates playing Point-and-click adventure games on Linux using computer vision and AI.
Supports local Ollama models, OpenAI, and Anthropic.
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
import queue
from PIL import Image, ImageDraw, ImageFont, ImageTk # Added ImageTk

try:
    import ollama
    import pyautogui
    import mss
    # For remote LLMs
    import openai
    import anthropic
except ImportError as e:
    print(f"[!] Missing required Python package: {e}")
    print("[!] Please install them, e.g., using pip: pip install ollama pyautogui mss pillow openai anthropic")
    sys.exit(1)

# --- Configuration Constants ---
DEFAULT_GAME_WINDOW_TITLE = "Maniac Mansion"
SESSIONS_DIR = "sessions"
SCREENSHOT_INTERVAL = 4  # Seconds to wait after LLM response before next screenshot
CLICK_INTERVAL = 2       # Seconds between multiple clicks from a single LLM response
INTERNAL_CROP = {"top": 0, "bottom": 0, "left": 0, "right": 0} # ScummVM padding

# --- API Keys (PLACEHOLDERS - VERY IMPORTANT: Use environment variables or secure config) ---
# It's highly recommended to load these from environment variables or a secure config file.
# Example: OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_API_KEY = "s" # Placeholder from your prompt
ANTHROPIC_API_KEY = "" # Placeholder from your prompt

# --- Global LLM Game Context ---
LLM_GAME_CONTEXT = "I'm playing a point-and-click adventure game. I have to explore how the story unfolds through what I see on the screen."

# --- Global variable for selected game window title ---
SELECTED_GAME_WINDOW_TITLE = DEFAULT_GAME_WINDOW_TITLE
SELECTED_GAME_WINDOW_ID = None # Add new global for the selected window's ID

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

# FileHandler will be added directly to 'logger' in create_session_directory.
# No StreamHandler is added to 'logger', so its messages (info, debug) don't go to console.
# The root logger is left unconfigured by our script after clearing its handlers.

# --- Helper Functions ---

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
    if OPENAI_API_KEY and OPENAI_API_KEY.startswith("sk-") and len(OPENAI_API_KEY) > 20: # Added length check
        logger.info("OpenAI API key found, adding OpenAI models.")
        providers.append({"provider_name": "OpenAI (Remote)", "model_id": "gpt-4o", "display_name": "OpenAI: gpt-4o", "type": "openai"})
        providers.append({"provider_name": "OpenAI (Remote)", "model_id": "gpt-4-turbo", "display_name": "OpenAI: gpt-4-turbo", "type": "openai"}) # gpt-4-turbo also supports vision
    else:
        logger.warning(f"OpenAI API key is missing, a placeholder, or invalid. Skipping OpenAI models.")

    # Anthropic
    # Using latest known official model IDs as of mid-2024.
    # The model names "claude-opus-4-20250514" and "claude-sonnet-4-20250514" are not standard.
    # "claude-3-5-haiku-20241022" is also not a standard public model name.
    # Claude 3.5 Sonnet is the latest in the Sonnet series. Haiku is part of Claude 3 series.
    if ANTHROPIC_API_KEY and ANTHROPIC_API_KEY.startswith("sk-ant-") and len(ANTHROPIC_API_KEY) > 20: # Added length check
        logger.info("Anthropic API key found, adding Anthropic models.")
        providers.append({"provider_name": "Anthropic (Remote)", "model_id": "claude-3-opus-20240229", "display_name": "Anthropic: Claude 3 Opus", "type": "anthropic"})
        providers.append({"provider_name": "Anthropic (Remote)", "model_id": "claude-3-5-sonnet-20240620", "display_name": "Anthropic: Claude 3.5 Sonnet", "type": "anthropic"})
        providers.append({"provider_name": "Anthropic (Remote)", "model_id": "claude-3-haiku-20240307", "display_name": "Anthropic: Claude 3 Haiku", "type": "anthropic"})
    else:
        logger.warning(f"Anthropic API key is missing, a placeholder, or invalid. Skipping Anthropic models.")
    
    if not providers:
        logger.error("CRITICAL: No LLM providers could be configured (neither local Ollama nor remote). Please check your Ollama setup and API key configurations in the script.")
        
    return providers

def select_llm_model(providers_list):
    """Prompts the user to select an LLM from the combined list."""
    if not providers_list:
        logger.error("No LLM providers or models were found/configured to select from. Cannot proceed.")
        return None
        
    print("\nAvailable LLM Models (Local & Remote):")
    for idx, model_info in enumerate(providers_list):
        print(f"  {idx + 1}: {model_info['display_name']}")
    
    while True:
        try:
            selection = input(f"Select model number (1-{len(providers_list)}): ")
            selected_idx = int(selection) - 1
            if 0 <= selected_idx < len(providers_list):
                chosen_model = providers_list[selected_idx]
                logger.info(f"User selected LLM: {chosen_model['display_name']}")
                return chosen_model 
            else:
                print("[!] Invalid selection. Please enter a number from the list.")
        except ValueError:
            print("[!] Invalid input. Please enter a number.")
        except Exception as e: # Catch any other unexpected error during selection
            logger.error(f"Error during model selection: {e}", exc_info=True)
            return None

def find_game_window_details(title_to_find, id_to_find=None):
    """
    Find the game window and return its details.
    Prioritizes id_to_find if provided and valid. Otherwise, searches by title_to_find.
    Simplified to use main window geometry and apply INTERNAL_CROP directly.
    Console output is minimized; details are logged at DEBUG level.
    """
    final_window_id = None

    if id_to_find:
        try:
            temp_geom_cmd = ["xdotool", "getwindowgeometry", "--shell", id_to_find]
            subprocess.run(temp_geom_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=2)
            final_window_id = id_to_find
            # Changed from INFO to DEBUG for less console noise
            logger.debug(f"Validated provided window ID: {id_to_find} for title query '{title_to_find}'.")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Provided window ID {id_to_find} (for '{title_to_find}') seems invalid or window closed: {e}. Falling back to search by title.")
            final_window_id = None

    if not final_window_id:
        logger.debug(f"Searching for window by title: '{title_to_find}'")
        found_by_name = False
        try:
            exact_regex = f"^{re.escape(title_to_find)}$"
            search_cmd = ["xdotool", "search", "--onlyvisible", "--name", exact_regex]
            try:
                result = subprocess.run(search_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=3)
                window_ids = [wid for wid in result.stdout.strip().split("\n") if wid]
                if window_ids:
                    final_window_id = window_ids[0]
                    found_by_name = True
                    # Changed from INFO to DEBUG
                    logger.debug(f"Found window by exact literal title match (ID: {final_window_id}).")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                logger.debug(f"Exact literal title match for '{title_to_find}' failed or timed out.")

            if not found_by_name:
                logger.debug(f"No exact literal window match for '{title_to_find}'. Trying partial literal match (substring)...")
                partial_escaped_regex = re.escape(title_to_find)
                search_cmd = ["xdotool", "search", "--onlyvisible", "--name", partial_escaped_regex]
                try:
                    result = subprocess.run(search_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=3)
                    window_ids = [wid for wid in result.stdout.strip().split("\n") if wid]
                    if window_ids:
                        final_window_id = window_ids[0]
                        found_by_name = True
                        # Changed from INFO to DEBUG
                        logger.debug(f"Found window by partial literal title match (substring) (ID: {final_window_id}).")
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e_partial_literal:
                    logger.warning(f"Partial literal title match for '{title_to_find}' failed or found no windows: {e_partial_literal}.")
            
            if not found_by_name and title_to_find != re.escape(title_to_find):
                 logger.debug(f"Literal title searches failed for '{title_to_find}'. Trying raw title as regex for partial match...")
                 try:
                    search_cmd = ["xdotool", "search", "--onlyvisible", "--name", title_to_find]
                    result = subprocess.run(search_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=3)
                    window_ids = [wid for wid in result.stdout.strip().split("\n") if wid]
                    if window_ids:
                        final_window_id = window_ids[0]
                        found_by_name = True
                        # Changed from INFO to DEBUG
                        logger.debug(f"Found window by raw title (as regex) partial match (ID: {final_window_id}).")
                 except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e_raw_partial:
                    logger.warning(f"Raw title (as regex) partial match for '{title_to_find}' also failed or found no windows: {e_raw_partial}")

            if not final_window_id:
                logger.error(f"Could not find window by title '{title_to_find}' after all search attempts.")
                return None
        except Exception as e_search: 
            logger.error(f"Unexpected error during window search by name for '{title_to_find}': {e_search}", exc_info=True)
            return None
            
    if not final_window_id:
        logger.error(f"Critical: Failed to identify target window ID for title query '{title_to_find}'.")
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
        
        base_x = geometry["X"]
        base_y = geometry["Y"]
        base_width = geometry["WIDTH"]
        base_height = geometry["HEIGHT"]

        logger.debug(f"Window {final_window_id} raw geometry: X={base_x}, Y={base_y}, W={base_width}, H={base_height}")
        logger.debug(f"Applying INTERNAL_CROP: Top={INTERNAL_CROP['top']}, Bottom={INTERNAL_CROP['bottom']}, Left={INTERNAL_CROP['left']}, Right={INTERNAL_CROP['right']}")

        final_x = base_x + INTERNAL_CROP["left"]
        final_y = base_y + INTERNAL_CROP["top"]
        final_width = base_width - (INTERNAL_CROP["left"] + INTERNAL_CROP["right"])
        final_height = base_height - (INTERNAL_CROP["top"] + INTERNAL_CROP["bottom"])
        
        if final_width <= 0 or final_height <= 0:
            logger.error(f"Invalid window dimensions after INTERNAL_CROP: W={final_width}xH={final_height}. Window ID: {final_window_id}.")
            logger.error(f"Base geometry was W={base_width}, H={base_height}. Check INTERNAL_CROP settings.")
            return None
            
        # Changed from INFO to DEBUG - this detailed info is good for logs, but console gets general success/failure from main_loop
        logger.debug(f"Final capture region for window {final_window_id}: X={final_x}, Y={final_y}, W={final_width}, H={final_height}")
        return {
            "left": final_x, "top": final_y, "width": final_width, "height": final_height,
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

def draw_grid_on_image(image, image_dimensions):
    """Draws a reference grid on the image.
    (0,0) for LLM is bottom-left. X increases right, Y increases up.
    - X-axis lines (vertical) are GREEN. GREEN X-coordinate labels appear at the TOP and BOTTOM edges, next to each line.
    - Y-axis lines (horizontal) are RED. RED Y-coordinate labels (value from bottom) appear at the LEFT and RIGHT edges, next to each line.
    """
    if not image: return None
    image_with_grid = image.copy().convert("RGBA")
    draw = ImageDraw.Draw(image_with_grid)
    
    width = image_dimensions["width"]
    height = image_dimensions["height"]
    
    x_axis_color = (0, 128, 0, 255)  # Dark Green for X-axis lines and text
    y_axis_color = (255, 0, 0, 255)  # Red for Y-axis lines and text

    line_width = 1
    font_size = 14 
    label_offset = 3 # Pixels next to the line
    edge_margin = 3  # Pixels from the very edge of the image for labels

    font = None
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", font_size)
    except IOError:
        logger.warning(f"Font 'DejaVuSans.ttf' not found, using default PIL font for grid.")
        try:
            if hasattr(ImageFont, 'load_default') and callable(getattr(ImageFont, 'load_default')):
                # For Pillow 8.0.0 and newer, size argument is supported in load_default
                if hasattr(Image, 'PILLOW_VERSION') and (Image.PILLOW_VERSION.startswith(('8', '9', '10')) or int(Image.PILLOW_VERSION.split('.')[0]) > 7) :
                     font = ImageFont.load_default(size=font_size)
                else: # Older versions might not support size here, or it's the only way
                     font = ImageFont.load_default() 
            else: # Very old Pillow or unexpected setup
                font = ImageFont.load_default()
        except Exception as e_font_fallback:
            logger.error(f"Could not load default PIL font for grid: {e_font_fallback}. Text labels might be missing or small.")
    
    num_divisions = 8 

    # Draw X-axis grid lines (vertical, GREEN) and labels (top and bottom edges)
    for i in range(1, num_divisions): 
        x_pos = int(width * (i / num_divisions))
        draw.line([(x_pos, 0), (x_pos, height)], fill=x_axis_color, width=line_width)
        
        if font:
            label_text = str(x_pos)
            text_width, text_height = 0, 0 # Default values
            # Use getbbox for modern Pillow, fallback to getsize
            if hasattr(font, "getbbox"): 
                 bbox = font.getbbox(label_text) # (left, top, right, bottom) of the text box
                 text_width = bbox[2] - bbox[0]
                 text_height = bbox[3] - bbox[1] 
            elif hasattr(font, "getsize"): # Older Pillow
                 text_width, text_height = font.getsize(label_text)
            else: # Basic fallback if no text size method
                text_width = font_size * len(label_text) * 0.6 # Rough estimate
                text_height = font_size


            # Determine X position for labels (next to the line)
            label_x_right_of_line = x_pos + label_offset
            label_x_left_of_line = x_pos - text_width - label_offset
            
            chosen_label_x = label_x_right_of_line
            if chosen_label_x + text_width > width - edge_margin: # If right-side label goes off screen
                chosen_label_x = label_x_left_of_line # Try left side
            if chosen_label_x < edge_margin: # If left-side label also goes off screen (or was initially off)
                chosen_label_x = label_x_right_of_line # Revert to right if left is also bad
                if chosen_label_x + text_width > width - edge_margin : # if still bad, clamp
                     chosen_label_x = width - text_width - edge_margin
                if chosen_label_x < edge_margin: # if still bad, clamp
                     chosen_label_x = edge_margin


            # Top edge label
            draw.text((chosen_label_x, edge_margin), label_text, fill=x_axis_color, font=font)
            # Bottom edge label
            draw.text((chosen_label_x, height - text_height - edge_margin), label_text, fill=x_axis_color, font=font)
        
    # Draw Y-axis grid lines (horizontal, RED) and labels (left and right edges, Y from bottom)
    for i in range(1, num_divisions): 
        y_screen_pos = int(height * (i / num_divisions)) # This is position from top of image
        draw.line([(0, y_screen_pos), (width, y_screen_pos)], fill=y_axis_color, width=line_width)
        
        if font:
            label_y_val = height - y_screen_pos # Y-value from bottom
            label_text = str(label_y_val)
            
            text_width, text_height = 0,0
            if hasattr(font, "getbbox"):
                 bbox = font.getbbox(label_text)
                 text_width = bbox[2] - bbox[0]
                 text_height = bbox[3] - bbox[1]
            elif hasattr(font, "getsize"):
                 text_width, text_height = font.getsize(label_text)
            else:
                text_width = font_size * len(label_text) * 0.6
                text_height = font_size

            # Determine Y position for labels (next to the line)
            label_y_below_line = y_screen_pos + label_offset
            label_y_above_line = y_screen_pos - text_height - label_offset

            chosen_label_y = label_y_below_line
            if chosen_label_y + text_height > height - edge_margin: # If label below line goes off screen
                chosen_label_y = label_y_above_line # Try above line
            if chosen_label_y < edge_margin: # If label above line also goes off screen (or was initially off)
                chosen_label_y = label_y_below_line # Revert
                if chosen_label_y + text_height > height - edge_margin: # Clamp
                    chosen_label_y = height - text_height - edge_margin
                if chosen_label_y < edge_margin: # Clamp
                    chosen_label_y = edge_margin


            # Left edge label
            draw.text((edge_margin, chosen_label_y), label_text, fill=y_axis_color, font=font)
            # Right edge label
            draw.text((width - text_width - edge_margin, chosen_label_y), label_text, fill=y_axis_color, font=font)
            
    # No border: The line "draw.rectangle([(0,0), (width-1, height-1)], outline=border_color, width=line_width)" is removed.
    return image_with_grid

def get_llm_prompt_text(image_width, image_height):
    global LLM_GAME_CONTEXT # Access the global context
    return f"""You are an AI playing a classic point-and-click adventure game.
Your primary goal is to analyze the CURRENT GAME SCREEN IMAGE provided and decide the next best action to advance. Base your decisions on what you see in THIS <image> and the overall CONTEXT.

CONTEXT:
{LLM_GAME_CONTEXT}

The image has a reference grid to help you identify click coordinates:
- Coordinate system: (0,0) is at the BOTTOM-LEFT corner of the image.
- X-axis: GREEN vertical lines. Labels are GREEN, at TOP and BOTTOM edges. X increases to the right (0 to {image_width-1}).
- Y-axis: RED horizontal lines. Labels are RED, at LEFT and RIGHT edges. Y values are distance from BOTTOM, increasing upwards (0 to {image_height-1}).
Image size: {image_width}x{image_height}.

Your Task:
1.  **Describe Scene:** Briefly describe what you see in the CURRENT game screen. Do NOT describe the grid.
2.  **Action Plan:** State your overall goal for this turn, considering the CONTEXT.
3.  **Clicks:** For EACH click required for your action plan:
    a.  `coordinates`: An `[x,y]` pair. `x` is distance from left, `y` is distance from BOTTOM. Use the GREEN (X) and RED (Y) grid labels on the image for accuracy.
    b.  `reason`: A short, direct explanation for THIS specific click (e.g., "Click on key to pick it up", "Select 'Open' verb", "Click on door with 'Open' selected").

Output your response in JSON format:
```json
{{
  "description": "Brief description of game scene from the current image.",
  "action_plan": "Your overall intended action for this turn, considering the CONTEXT.",
  "clicks": [
    {{ "coordinates": [x1, y1], "reason": "Reason for click 1 (e.g., Select 'Open' verb)" }},
    {{ "coordinates": [x2, y2], "reason": "Reason for click 2 (e.g., Click on door)" }}
  ]
}}
```
Important:
- Base your analysis on the provided <image> and the CONTEXT.
- Be precise with coordinates. They must be within image bounds (X from 0 to {image_width-1}, Y from 0 to {image_height-1}).
- If no clicks are needed (e.g., just observing), provide an empty list: `"clicks": []`.
"""

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
    if not (OPENAI_API_KEY and OPENAI_API_KEY.startswith("sk-")):
        logger.error("OpenAI API key not configured or invalid.")
        return None
    
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    # System prompt can remain general, as the detailed context is now in the user prompt
    system_prompt = "You are an AI agent playing the game Maniac Mansion. Analyze the provided game screenshot and decide on the best next action. The image has a reference grid. Output your response in JSON format with 'description', 'action_plan', and 'clicks' (list of [x,y] coordinates relative to the image, using the grid)."
    user_prompt_text = get_llm_prompt_text(image_width, image_height) 

    try:
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
                            "image_url": {"url": base64_image_data_url, "detail": "auto"},
                        },
                    ],
                }
            ],
            max_tokens=600 
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error calling OpenAI API ({model_id}): {e}", exc_info=True)
        return None

def get_anthropic_llm_analysis(model_id, base64_image_raw, image_width, image_height):
    if not (ANTHROPIC_API_KEY and ANTHROPIC_API_KEY.startswith("sk-ant-")):
        logger.error("Anthropic API key not configured or invalid.")
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    # System prompt can remain general
    system_prompt = "You are an AI agent playing the game Maniac Mansion. Analyze the provided game screenshot and decide on the best next action. The image has a reference grid. Output your response in JSON format with 'description', 'action_plan', and 'clicks' (list of [x,y] coordinates relative to the image, using the grid)."
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
            return response.content[0].text
        else:
            logger.error(f"Unexpected Anthropic API response format ({model_id}): {response.content}")
            return None
    except Exception as e:
        logger.error(f"Error calling Anthropic API ({model_id}): {e}", exc_info=True)
        return None

def get_llm_analysis(selected_model_info, original_image, image_dimensions_for_llm):
    if not original_image or not image_dimensions_for_llm:
        logger.error("get_llm_analysis: No image or dimensions provided.")
        return None, original_image

    image_with_grid = draw_grid_on_image(original_image, image_dimensions_for_llm)
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

    # Changed from INFO to DEBUG for cleaner console
    logger.debug(f"Image with grid prepared ({image_dimensions_for_llm['width']}x{image_dimensions_for_llm['height']}). Calling LLM: {selected_model_info['display_name']}")
    
    response_content_str = None
    try:
        model_type = selected_model_info['type']
        model_id = selected_model_info['model_id']
        
        if model_type == "ollama":
            response_content_str = get_ollama_llm_analysis(model_id, base64_encoded_image_raw, image_dimensions_for_llm['width'], image_dimensions_for_llm['height'])
        elif model_type == "openai":
            response_content_str = get_openai_llm_analysis(model_id, base64_image_data_url, image_dimensions_for_llm['width'], image_dimensions_for_llm['height'])
        elif model_type == "anthropic":
            response_content_str = get_anthropic_llm_analysis(model_id, base64_encoded_image_raw, image_dimensions_for_llm['width'], image_dimensions_for_llm['height'])
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
        
        return parsed_json, image_with_grid 
            
    except Exception as e:
        logger.error(f"Error in LLM analysis dispatcher ({selected_model_info['display_name']}): {e}", exc_info=True)
        # This print is important user feedback
        print(f"[!] Error during LLM analysis with {selected_model_info['display_name']}.")
        return None, image_with_grid

def execute_clicks(click_list, window_details):
    """Executes clicks. LLM provides click objects with coordinates and a reason."""
    if not click_list or not window_details:
        if not click_list: 
            # This print is for user feedback when no clicks are planned
            print("  No clicks were planned by the LLM for execution.") 
        return
    
    image_height_for_llm = window_details["height"] 
    image_width_for_llm = window_details["width"]

    # The "Executing Clicks:" header will be part of print_iteration_summary or main_loop flow
    try:
        for idx, click_obj in enumerate(click_list, 1): 
            if not (isinstance(click_obj, dict) and 
                    "coordinates" in click_obj and 
                    isinstance(click_obj["coordinates"], list) and 
                    len(click_obj["coordinates"]) == 2 and 
                    all(isinstance(c, (int, float)) for c in click_obj["coordinates"]) and
                    "reason" in click_obj): 
                logger.warning(f"  Skipping invalid click object format from LLM: {click_obj}")
                # This print is important user feedback
                print(f"  [!] Invalid click data for click {idx}. Skipping.")
                continue

            click_coords_llm = click_obj["coordinates"]
            click_reason = click_obj.get("reason", "No reason provided") 

            img_x_llm = int(click_coords_llm[0]) 
            img_y_llm = int(click_coords_llm[1]) 
            
            if not (0 <= img_x_llm < image_width_for_llm and 0 <= img_y_llm < image_height_for_llm):
                logger.warning(f"  LLM Click {idx} ({click_reason}): Coords ({img_x_llm}, {img_y_llm}) are OUTSIDE image bounds ({image_width_for_llm}x{image_height_for_llm}). Skipping.")
                # This print is important user feedback
                print(f"  [!] Click {idx} for '{click_reason}': Coords ({img_x_llm},{img_y_llm}) out of bounds. Skipping.")
                continue

            img_y_offset_from_top = image_height_for_llm - 1 - img_y_llm
            
            screen_x = window_details["left"] + img_x_llm
            screen_y = window_details["top"] + img_y_offset_from_top
            
            # Refined print message for click execution
            print(f"  > Clicking for: '{click_reason}' (Screen: {screen_x},{screen_y})")
            
            pyautogui.click(screen_x, screen_y)
            # Changed from INFO to DEBUG for cleaner console
            logger.debug(f"    pyautogui: Clicked at screen ({screen_x}, {screen_y}) for reason: '{click_reason}' (LLM: {img_x_llm},{img_y_llm})")
            
            if idx < len(click_list):
                 logger.debug(f"    Waiting {CLICK_INTERVAL}s before next click in batch.") # DEBUG, not for console
                 time.sleep(CLICK_INTERVAL)
                 
    except Exception as e:
        logger.error(f"Unexpected error executing clicks with pyautogui: {e}", exc_info=True)
        # This print is important user feedback
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
                        print(f"    {idx}. LLM Coords: {click_obj['coordinates']}, Reason: {click_obj['reason']}")
                    else:
                        print(f"    {idx}. Invalid click object format: {click_obj}")
                print("-" * 40) # Footer for this section
                return

            image_height_for_llm = window_details["height"] 

            for idx, click_obj in enumerate(click_list_llm, 1): 
                if isinstance(click_obj, dict) and "coordinates" in click_obj and isinstance(click_obj["coordinates"], list) and len(click_obj["coordinates"]) == 2 and "reason" in click_obj:
                    click_coords_llm = click_obj["coordinates"]
                    click_reason = click_obj.get("reason", "N/A")

                    img_x_llm = int(click_coords_llm[0])
                    img_y_llm = int(click_coords_llm[1])
                    
                    img_y_offset_from_top = image_height_for_llm - 1 - img_y_llm
                    screen_x = window_details["left"] + img_x_llm
                    screen_y = window_details["top"] + img_y_offset_from_top
                    
                    # Simplified display of planned clicks
                    print(f"    {idx}. {click_reason} -> LLM Coords: ({img_x_llm},{img_y_llm}) -> Screen: ({screen_x},{screen_y})")
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
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Maniac Mansion AI - Live Status")
        self.root.geometry("550x780")  # Increased height for new section
        self.root.attributes('-topmost', True)
        self.closed = False

        # --- Info Frame ---
        info_frame = tk.Frame(self.root, pady=5)
        info_frame.pack(fill="x", padx=10)

        self.step_var = tk.StringVar()
        self.llm_model_var = tk.StringVar()
        self.window_name_var = tk.StringVar()

        tk.Label(info_frame, text="Step:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(info_frame, textvariable=self.step_var, font=("Arial", 10)).grid(row=0, column=1, sticky="w", padx=5)

        tk.Label(info_frame, text="LLM Model:", font=("Arial", 10, "bold")).grid(row=1, column=0, sticky="w")
        tk.Label(info_frame, textvariable=self.llm_model_var, font=("Arial", 10), wraplength=350).grid(row=1, column=1, sticky="w", padx=5)
        
        tk.Label(info_frame, text="Capturing:", font=("Arial", 10, "bold")).grid(row=2, column=0, sticky="w")
        tk.Label(info_frame, textvariable=self.window_name_var, font=("Arial", 10), wraplength=350).grid(row=2, column=1, sticky="w", padx=5)

        separator1 = tk.Frame(self.root, height=2, bd=1, relief=tk.SUNKEN)
        separator1.pack(fill="x", padx=5, pady=5)

        # --- Vision Frame (Text Description + Image) ---
        vision_frame = tk.Frame(self.root)
        vision_frame.pack(fill="x", padx=10)
        tk.Label(vision_frame, text="Vision (LLM Description):", font=("Arial", 10, "bold")).pack(anchor="w")
        self.desc_var = tk.StringVar()
        self.desc_label = tk.Label(vision_frame, textvariable=self.desc_var, wraplength=520, justify="left", font=("Arial", 9))
        self.desc_label.pack(anchor="w", fill="x")

        tk.Label(vision_frame, text="Last Image Sent to LLM (with Clicks):", font=("Arial", 10, "bold"), pady=5).pack(anchor="w") 
        self.image_label = tk.Label(vision_frame) 
        self.image_label.pack(anchor="center", pady=5)
        self.photo_image = None 
        self.max_image_display_width = 520
        self.max_image_display_height = 320

        separator2 = tk.Frame(self.root, height=2, bd=1, relief=tk.SUNKEN)
        separator2.pack(fill="x", padx=5, pady=5)

        # --- Plan Frame ---
        plan_frame = tk.Frame(self.root)
        plan_frame.pack(fill="x", padx=10)
        tk.Label(plan_frame, text="Plan (LLM Action):", font=("Arial", 10, "bold")).pack(anchor="w")
        self.plan_var = tk.StringVar()
        self.plan_label = tk.Label(plan_frame, textvariable=self.plan_var, wraplength=520, justify="left", font=("Arial", 9))
        self.plan_label.pack(anchor="w", fill="x")

        separator3 = tk.Frame(self.root, height=2, bd=1, relief=tk.SUNKEN)
        separator3.pack(fill="x", padx=5, pady=5)
        
        # --- Clicks Frame ---
        clicks_frame = tk.Frame(self.root)
        clicks_frame.pack(fill="x", padx=10) # Changed fill and expand
        tk.Label(clicks_frame, text="Last Clicks & Objectives:", font=("Arial", 10, "bold")).pack(anchor="w")
        self.clicks_text = tk.Text(clicks_frame, wrap=tk.WORD, height=3, font=("Arial", 9), relief=tk.FLAT, bg=self.root.cget('bg')) # Adjusted height
        self.clicks_text.pack(anchor="w", fill="x")
        self.clicks_text.config(state=tk.DISABLED)

        separator4 = tk.Frame(self.root, height=2, bd=1, relief=tk.SUNKEN) # New separator
        separator4.pack(fill="x", padx=5, pady=5)

        # --- Inner Dialogue/Context Frame ---
        context_frame = tk.Frame(self.root)
        context_frame.pack(fill="both", expand=True, padx=10) # Allow this to take remaining space
        tk.Label(context_frame, text="Inner Dialogue (Long Term Context):", font=("Arial", 10, "bold")).pack(anchor="w")
        self.context_var = tk.StringVar()
        self.context_label = tk.Label(context_frame, textvariable=self.context_var, wraplength=520, justify="left", font=("Arial", 9), anchor="nw")
        self.context_label.pack(anchor="w", fill="both", expand=True)


        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_queue = queue.Queue()
        self.poll_updates()

    def update_status(self, step, llm_model, window_name, description, action_plan, clicks_str, game_context, pil_image=None, click_coords_list=None): 
        self.update_queue.put((step, llm_model, window_name, description, action_plan, clicks_str, game_context, pil_image, click_coords_list))

    def _draw_clicks_on_image(self, image, click_coords_list, image_height_llm):
        """Draws circles for each click coordinate on the image."""
        if not image or not click_coords_list:
            return image
        
        # Ensure image is RGBA to handle alpha for fill color
        img_to_draw_on = image.copy()
        if img_to_draw_on.mode != 'RGBA':
            img_to_draw_on = img_to_draw_on.convert('RGBA')

        draw = ImageDraw.Draw(img_to_draw_on)
        radius = 8  # Increased radius for bigger points
        # Bright magenta fill, semi-transparent, with a solid black outline
        fill_color = (255, 0, 255, 180)  # Bright Magenta, semi-transparent
        outline_color = (0, 0, 0, 255)    # Solid Black
        outline_width = 2                 # Thicker outline

        for click_obj in click_coords_list:
            if isinstance(click_obj, dict) and "coordinates" in click_obj:
                coords_llm = click_obj["coordinates"]
                if isinstance(coords_llm, list) and len(coords_llm) == 2:
                    try:
                        x_llm = int(coords_llm[0])
                        y_llm = int(coords_llm[1]) # Y from bottom

                        # Convert Y from LLM (bottom-up) to Pillow (top-down)
                        y_pil = image_height_llm - 1 - y_llm
                        
                        # Define the bounding box for the ellipse (circle)
                        x1 = x_llm - radius
                        y1 = y_pil - radius
                        x2 = x_llm + radius
                        y2 = y_pil + radius
                        draw.ellipse([x1, y1, x2, y2], fill=fill_color, outline=outline_color, width=outline_width)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid click coordinates for drawing: {coords_llm}, error: {e}")
        return img_to_draw_on

    def poll_updates(self):
        try:
            while not self.update_queue.empty():
                step, llm_model, window_name, desc, plan, clicks_str, game_context, pil_image, click_coords_list = self.update_queue.get_nowait() 
                self.step_var.set(str(step))
                self.llm_model_var.set(llm_model or "N/A")
                self.window_name_var.set(window_name or "N/A")
                self.desc_var.set(desc or "N/A")
                self.plan_var.set(plan or "N/A")
                self.context_var.set(game_context or "N/A") # Set the context
                
                self.clicks_text.config(state=tk.NORMAL)
                self.clicks_text.delete("1.0", tk.END)
                self.clicks_text.insert(tk.END, clicks_str or "N/A")
                self.clicks_text.config(state=tk.DISABLED)

                if pil_image:
                    try:
                        img_copy = pil_image.copy() 
                        
                        if click_coords_list:
                            llm_image_height = img_copy.height 
                            img_copy = self._draw_clicks_on_image(img_copy, click_coords_list, llm_image_height)

                        resample_method = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS
                        img_copy.thumbnail((self.max_image_display_width, self.max_image_display_height), resample_method)
                        
                        self.photo_image = ImageTk.PhotoImage(img_copy)
                        self.image_label.config(image=self.photo_image)
                        self.image_label.image = self.photo_image # Keep a reference
                    except Exception as e:
                        logger.error(f"Error processing image for status window: {e}", exc_info=True)
                        self.image_label.config(image='') # Clear image on error
                else:
                    self.image_label.config(image='') # Clear image if None provided
        except queue.Empty:
            pass
        
        if not self.closed:
            self.root.after(100, self.poll_updates)

    def on_close(self):
        print("Status window closed by user.")
        logger.info("Status window closed by user.")
        self.closed = True
        if hasattr(self.root, 'quit'):
            self.root.quit()


# --- Main Application Logic (to be run in a thread) ---
# Renamed main_loop to game_logic_thread_target
def game_logic_thread_target(status_window_ref): # Pass the status window instance
    global SELECTED_GAME_WINDOW_TITLE, SELECTED_GAME_WINDOW_ID, selected_llm_info, LLM_GAME_CONTEXT # Ensure selected_llm_info and LLM_GAME_CONTEXT is accessible

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
        status_window_ref.update_status(0, "N/A", "N/A", "Setup Error", "No LLM models available.", "N/A", LLM_GAME_CONTEXT, None, None)
        return
    
    selected_llm_info = select_llm_model(llm_providers) 
    if not selected_llm_info:
        print("[!] No LLM model selected. Exiting game logic thread.") 
        status_window_ref.update_status(0, "N/A", "N/A", "Setup Error", "No LLM model selected.", "N/A", LLM_GAME_CONTEXT, None, None)
        return

    print(f"Targeting: '{SELECTED_GAME_WINDOW_TITLE}' (ID: {SELECTED_GAME_WINDOW_ID or 'Search by name'})")
    print(f"Using LLM: {selected_llm_info['display_name']}.")
    print("Setup complete. Starting main game loop in background thread...")
    logger.info(f"Setup complete. Using LLM: {selected_llm_info['display_name']}. Targeting window: '{SELECTED_GAME_WINDOW_TITLE}' (ID: {SELECTED_GAME_WINDOW_ID or 'N/A'}).")
    
    iteration_count = 0
    # status_window is now passed as an argument: status_window_ref
    try:
        while not status_window_ref.closed: # Check if the status window has been closed
            iteration_count += 1
            # Console output for iteration progress
            print(f"\n\n{'=' * 20} Iteration: {iteration_count} {'=' * 20}")

            game_window_details = find_game_window_details(SELECTED_GAME_WINDOW_TITLE, SELECTED_GAME_WINDOW_ID)
            current_game_window_name_for_status = SELECTED_GAME_WINDOW_TITLE # Default
            if game_window_details and game_window_details.get('window_id'):
                # Potentially get the actual current name if it can change, or use the selected one
                current_game_window_name_for_status = f"{SELECTED_GAME_WINDOW_TITLE} (ID: {game_window_details.get('window_id')})"
            elif not game_window_details:
                 current_game_window_name_for_status = f"{SELECTED_GAME_WINDOW_TITLE} (Not Found)"

            # Initialize per-iteration variables for status updates
            llm_desc = "N/A"
            llm_plan = "N/A"
            clicks_info_str = "N/A"
            image_to_save_for_session = None 
            raw_click_coords_for_status = None # Initialize here

            if not game_window_details:
                print(f"[!] Game window '{SELECTED_GAME_WINDOW_TITLE}' not found. Retrying in {SCREENSHOT_INTERVAL}s...") 
                llm_desc = "Game window not found." # More specific status
                llm_plan = "Waiting for game window..." # More specific status
                status_window_ref.update_status( # Update status even if window not found
                    iteration_count,
                    selected_llm_info.get('display_name', 'N/A') if 'selected_llm_info' in globals() and selected_llm_info else 'N/A',
                    current_game_window_name_for_status,
                    llm_desc,
                    llm_plan,
                    clicks_info_str, # Stays "N/A"
                    LLM_GAME_CONTEXT, # Pass context
                    None, # No image
                    None  # No clicks (raw_click_coords_for_status is None)
                )
                time.sleep(SCREENSHOT_INTERVAL) 
                if status_window_ref.closed: break
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
                    None  # No clicks (raw_click_coords_for_status is None)
                )
                time.sleep(SCREENSHOT_INTERVAL)
                if status_window_ref.closed: break
                continue
            
            # If we reach here, current_screenshot is valid.
            image_to_save_for_session = current_screenshot # Default to raw screenshot

            image_dimensions_for_llm = {"width": game_window_details["width"], "height": game_window_details["height"]}
            llm_analysis_json, image_processed_for_llm = get_llm_analysis(
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
            else: # llm_analysis_json is None or not a dict
                llm_desc = "LLM analysis failed or no response."
                clicks_info_str = "N/A due to LLM failure."
                # llm_plan remains "N/A"
                # raw_click_coords_for_status remains None

            if clicks_to_perform:
                print("\n  Executing Clicks on Host:") 
                execute_clicks(clicks_to_perform, game_window_details)
            else:
                # This print is handled by execute_clicks if list is empty, or here if no analysis
                if llm_analysis_json and isinstance(llm_analysis_json.get('clicks'), list) and not llm_analysis_json.get('clicks'):
                    pass # execute_clicks will print "No clicks were planned..."
                elif not llm_analysis_json: # If analysis failed entirely
                    print("\n  No clicks planned due to LLM analysis failure.")
                # else: if clicks format was invalid, execute_clicks handles individual skips

            # Update the status window with all information
            status_window_ref.update_status(
                iteration_count,
                selected_llm_info['display_name'],
                current_game_window_name_for_status,
                llm_desc,
                llm_plan,
                clicks_info_str,
                LLM_GAME_CONTEXT, # Pass context
                image_to_save_for_session, # Pass the PIL image (could be None if screenshot failed)
                raw_click_coords_for_status # Now guaranteed to be defined
            )

            print(f"\n--- End of Iteration {iteration_count}. Waiting {SCREENSHOT_INTERVAL}s ---")
            # ... (responsive sleep loop) ...
            for _ in range(SCREENSHOT_INTERVAL * 10): 
                if status_window_ref.closed:
                    break
                time.sleep(0.1)
            if status_window_ref.closed:
                print("Status window closed, exiting game logic loop.")
                break
    # ... (except KeyboardInterrupt, Exception, finally blocks remain similar) ...
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


if __name__ == "__main__":
    Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
    
    if not (OPENAI_API_KEY and OPENAI_API_KEY.startswith("sk-") and len(OPENAI_API_KEY) > 20):
        print("[!] OpenAI API key seems invalid or is a placeholder. OpenAI models may not work.")
    if not (ANTHROPIC_API_KEY and ANTHROPIC_API_KEY.startswith("sk-ant-") and len(ANTHROPIC_API_KEY) > 20):
        print("[!] Anthropic API key seems invalid or is a placeholder. Anthropic models may not work.")
    
    status_window_instance = StatusWindow()
    game_thread = threading.Thread(target=game_logic_thread_target, args=(status_window_instance,), daemon=True)
    game_thread.start()

    try:
        status_window_instance.root.mainloop()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt detected in main thread (Tkinter). Shutting down...")
        logger.info("Keyboard interrupt in main Tkinter thread. Closing status window and signaling game thread.")
        if not status_window_instance.closed:
            status_window_instance.on_close() 
    
    print("Exiting AI Player.")