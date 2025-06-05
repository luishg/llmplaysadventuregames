import re
import time
from datetime import datetime, timedelta
import twitchio
from twitchio.ext import commands
from typing import List, Tuple, Optional, Dict
import os
import asyncio
import threading

# Twitch channel configuration
TWITCH_CHANNEL = "PointAndClickAI"
TWITCH_TOKEN = os.getenv("TWITCH_TOKEN", "")  # Get token from environment variable

# Security configuration
GRID_SIZE = 100  # Maximum number of cells in the grid
SCREEN_WIDTH = 1920  # Maximum screen width
SCREEN_HEIGHT = 1080  # Maximum screen height

# Global bot instance and message storage
_global_bot = None
_chat_messages = []
_bot_running = False
_bot_thread = None
_last_processed_timestamp = None  # Track the last processed message timestamp
_last_processed_message_id = None  # Track the last processed message ID
_last_check_timestamp = None  # Track when we last checked for clicks

class ClickParser:
    def __init__(self):
        # Pattern for cell numbers (e.g., "click 42", "click(42)", etc.)
        self.cell_pattern = re.compile(r'click\s*\(?(\d+)\)?', re.IGNORECASE)
        # Pattern for pixel coordinates (e.g., "click (123, 456)", "click 123,456", etc.)
        self.pixel_pattern = re.compile(r'click\s*\(?(\d+)\s*,\s*(\d+)\)?', re.IGNORECASE)

    def validate_cell(self, cell: int) -> bool:
        """Validate if a cell number is within valid range."""
        if not isinstance(cell, int):
            print(f"[CHAT] Invalid cell number type: {type(cell)}")
            return False
        if cell < 1 or cell > GRID_SIZE:
            print(f"[CHAT] Cell number {cell} out of range (1-{GRID_SIZE})")
            return False
        return True

    def validate_pixel(self, x: int, y: int) -> bool:
        """Validate if pixel coordinates are within valid range."""
        if not isinstance(x, int) or not isinstance(y, int):
            print(f"[CHAT] Invalid coordinate types: x={type(x)}, y={type(y)}")
            return False
        if x < 0 or x >= SCREEN_WIDTH or y < 0 or y >= SCREEN_HEIGHT:
            print(f"[CHAT] Coordinates ({x}, {y}) out of range (0-{SCREEN_WIDTH-1}, 0-{SCREEN_HEIGHT-1})")
            return False
        return True

    def parse_message(self, message: str) -> List[Dict]:
        """Parse a message for click commands and return a list of click objects."""
        clicks = []
        
        # First check for pixel coordinates
        pixel_matches = self.pixel_pattern.finditer(message)
        for match in pixel_matches:
            x, y = map(int, match.groups())
            if self.validate_pixel(x, y):
                clicks.append({
                    "type": "pixel",
                    "coordinates": [x, y],
                    "reason": f"User suggested click at pixel coordinates ({x}, {y})"
                })
            else:
                print(f"[CHAT] Rejected invalid pixel coordinates: ({x}, {y})")
        
        # Then check for cell numbers
        cell_matches = self.cell_pattern.finditer(message)
        for match in cell_matches:
            cell = int(match.group(1))
            if self.validate_cell(cell):
                clicks.append({
                    "type": "cell",
                    "coordinates": cell,
                    "reason": f"User suggested click on cell {cell}"
                })
            else:
                print(f"[CHAT] Rejected invalid cell number: {cell}")
        
        return clicks

class TwitchChatBot(commands.Bot):
    def __init__(self):
        super().__init__(token=TWITCH_TOKEN, prefix='!', initial_channels=[TWITCH_CHANNEL])
        self.click_parser = ClickParser()

    async def event_ready(self):
        print(f"[CHAT] Bot connected to {TWITCH_CHANNEL}")
        print("[CHAT] Listening for click commands: 'click 42' or 'click (123, 456)'")

    async def event_message(self, message):
        global _chat_messages
        
        if message.echo:
            return

        # Store message with timestamp
        message_data = {
            'id': message.id,  # Add message ID
            'user': message.author.name,
            'content': message.content,
            'timestamp': datetime.now(),
            'clicks': self.click_parser.parse_message(message.content)
        }
        
        # Add to global message buffer
        _chat_messages.append(message_data)
        
        # Keep only last 100 messages to prevent memory issues
        if len(_chat_messages) > 100:
            _chat_messages.pop(0)
        
        # Print if contains clicks
        if message_data['clicks']:
            print(f"[CHAT] {message.author.name}: {message.content} -> {len(message_data['clicks'])} clicks")

def validate_twitch_token() -> bool:
    """Validate and fix the Twitch token format."""
    global TWITCH_TOKEN
    
    if not TWITCH_TOKEN:
        print("[CHAT] No TWITCH_TOKEN environment variable set")
        print("[CHAT] Get a token from: https://twitchapps.com/tmi/")
        return False
    
    # Auto-fix token format by adding oauth: prefix if missing
    if not TWITCH_TOKEN.startswith("oauth:"):
        print(f"[CHAT] Adding 'oauth:' prefix to token")
        TWITCH_TOKEN = f"oauth:{TWITCH_TOKEN}"
    
    if len(TWITCH_TOKEN) < 36:  # oauth: + 30 char token
        print("[CHAT] TWITCH_TOKEN seems too short, check if it's complete")
        print("[CHAT] Expected format: oauth:your_token_here")
        return False
    
    print(f"[CHAT] Token format looks valid (length: {len(TWITCH_TOKEN)})")
    return True

def start_twitch_bot():
    """Start the Twitch bot in a separate thread with its own event loop."""
    global _global_bot, _bot_running
    
    # Validate token first
    if not validate_twitch_token():
        return False
    
    def run_bot():
        global _bot_running, _global_bot
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            print("[CHAT] Creating bot instance in thread...")
            # Create the bot instance inside the thread with the correct event loop
            _global_bot = TwitchChatBot()
            print("[CHAT] Bot instance created, starting connection...")
            _bot_running = True
            loop.run_until_complete(_global_bot.start())
        except Exception as e:
            print(f"[CHAT] Error: {e}")
            _bot_running = False
            _global_bot = None
        finally:
            loop.close()
    
    try:
        print("[CHAT] Starting bot thread...")
        # Don't create the bot here, create it inside the thread
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        
        # Give bot more time to connect and initialize
        print("[CHAT] Waiting for bot to initialize...")
        time.sleep(5)
        
        if _bot_running and _global_bot:
            print("[CHAT] Twitch bot started successfully")
            return True
        else:
            print("[CHAT] Failed to start Twitch bot")
            return False
            
    except Exception as e:
        print(f"[CHAT] Error starting bot: {e}")
        return False

def get_recent_user_clicks(max_age_minutes: int = 5) -> Tuple[Optional[str], Optional[datetime], List[Dict]]:
    """
    Get clicks from the most recent user who posted click commands.
    Returns up to 4 most recent clicks from the user, ordered from oldest to newest.
    Only returns clicks from messages after the last check timestamp.
    
    Args:
        max_age_minutes: Maximum age of messages to consider (default: 5 minutes)
    
    Returns:
        Tuple of (username, timestamp of first click message, list of up to 4 click objects)
    """
    global _chat_messages, _last_processed_message_id, _last_check_timestamp
    
    # Check if we have any messages at all
    if not _chat_messages:
        print("[CHAT] No messages in chat history")
        return None, None, []
    
    # Filter messages that are not too old
    cutoff_time = datetime.now() - timedelta(minutes=max_age_minutes)
    print(f"[CHAT] Checking messages since {cutoff_time.strftime('%H:%M:%S')}")
    
    # If we have a last check timestamp, use it as the minimum time
    if _last_check_timestamp:
        print(f"[CHAT] Last check was at {_last_check_timestamp.strftime('%H:%M:%S')}")
        cutoff_time = max(cutoff_time, _last_check_timestamp)
    
    # Go through messages from newest to oldest to find the last user with clicks
    for msg in reversed(_chat_messages):
        # Skip messages that are too old
        if msg['timestamp'] < cutoff_time:
            print(f"[CHAT] Skipping old message from {msg['timestamp'].strftime('%H:%M:%S')}")
            continue
            
        # Skip if we've already processed this message
        if msg.get('id') == _last_processed_message_id:
            print(f"[CHAT] Skipping already processed message from {msg['timestamp'].strftime('%H:%M:%S')}")
            continue
            
        # If this message has clicks, this is our user
        if msg['clicks']:
            username = msg['user']
            first_click_timestamp = msg['timestamp']
            print(f"[CHAT] Found message with clicks from {username} at {first_click_timestamp.strftime('%H:%M:%S')}")
            
            # Collect clicks from this user's messages, ordered from oldest to newest
            user_clicks = []
            for user_msg in _chat_messages:  # Process in chronological order
                # Skip messages before the first click message
                if user_msg['timestamp'] < first_click_timestamp:
                    continue
                    
                # Skip messages that are too old
                if user_msg['timestamp'] < cutoff_time:
                    continue
                    
                # Only collect clicks from the same user
                if user_msg['user'] == username and user_msg['clicks']:
                    user_clicks.extend(user_msg['clicks'])
                    print(f"[CHAT] Added {len(user_msg['clicks'])} clicks from {username} at {user_msg['timestamp'].strftime('%H:%M:%S')}")
                    
                # Limit to 4 clicks maximum
                if len(user_clicks) >= 4:
                    print(f"[CHAT] Reached maximum of 4 clicks for {username}")
                    break
            
            # If we found clicks, mark this message as processed and return them
            if user_clicks:
                selected_clicks = user_clicks[:4]  # Take up to 4 clicks
                print(f"[CHAT] Returning {len(selected_clicks)} clicks from {username} (oldest to newest)")
                _last_processed_message_id = msg.get('id')  # Mark this message as processed
                _last_check_timestamp = datetime.now()  # Update last check timestamp
                return username, first_click_timestamp, selected_clicks
    
    print("[CHAT] No recent clicks found in any messages")
    _last_check_timestamp = datetime.now()  # Update last check timestamp even if no clicks found
    return None, None, []

def is_chat_running() -> bool:
    """Check if the chat bot is running."""
    return _bot_running

def get_chat_stats() -> Dict:
    """Get statistics about the chat."""
    global _chat_messages
    
    if not _chat_messages:
        return {
            'total_messages': 0,
            'messages_with_clicks': 0,
            'unique_users': 0,
            'recent_activity': 0,
            'last_user_with_clicks': None
        }
    
    # Messages in last 5 minutes
    cutoff_time = datetime.now() - timedelta(minutes=5)
    recent_messages = [msg for msg in _chat_messages if msg['timestamp'] > cutoff_time]
    
    # Find the last user who sent clicks
    last_user_with_clicks = None
    for msg in reversed(_chat_messages):
        if msg['clicks']:
            last_user_with_clicks = msg['user']
            break
    
    return {
        'total_messages': len(_chat_messages),
        'messages_with_clicks': len([msg for msg in _chat_messages if msg['clicks']]),
        'unique_users': len(set(msg['user'] for msg in _chat_messages)),
        'recent_activity': len(recent_messages),
        'last_user_with_clicks': last_user_with_clicks
    }

# Legacy function for compatibility
def initialize_twitch():
    """Legacy function - use start_twitch_bot() instead."""
    print("[CHAT] Using legacy initialize_twitch() - consider updating to start_twitch_bot()")
    return start_twitch_bot()

def get_user_clicks() -> Tuple[Optional[str], Optional[datetime], List[Dict]]:
    """Legacy function - use get_recent_user_clicks() instead."""
    return get_recent_user_clicks()

def set_security_parameters(grid_size: int = 100, screen_width: int = 1920, screen_height: int = 1080):
    """Set the security parameters for click validation."""
    global GRID_SIZE, SCREEN_WIDTH, SCREEN_HEIGHT
    
    # Validate parameters
    if grid_size < 1:
        print(f"[CHAT] Invalid grid size: {grid_size}. Must be at least 1.")
        return False
        
    if screen_width < 1 or screen_height < 1:
        print(f"[CHAT] Invalid screen dimensions: {screen_width}x{screen_height}. Must be positive.")
        return False
        
    # Update global parameters
    GRID_SIZE = grid_size
    SCREEN_WIDTH = screen_width
    SCREEN_HEIGHT = screen_height
    
    print(f"[CHAT] Security parameters set: Grid={GRID_SIZE} cells, Screen={SCREEN_WIDTH}x{SCREEN_HEIGHT}")
    return True

if __name__ == "__main__":
    print("=== Twitch Chat Test ===")
    if not TWITCH_TOKEN:
        print("Error: TWITCH_TOKEN not set!")
        print("Set it with: export TWITCH_TOKEN='oauth:your_token_here'")
        exit(1)
    
    print("Starting Twitch bot...")
    if start_twitch_bot():
        print("Bot started! Monitoring chat for 60 seconds...")
        print("Send messages like 'click 42' or 'click (123, 456)' in chat to test")
        try:
            time.sleep(60)
            stats = get_chat_stats()
            print(f"\nChat stats: {stats}")
            
            user, timestamp, clicks = get_recent_user_clicks()
            if clicks:
                print(f"Last user with clicks: {user}")
                print(f"Their clicks: {[click['reason'] for click in clicks]}")
            else:
                print("No recent clicks found")
        except KeyboardInterrupt:
            print("\nStopped by user")
    else:
        print("Failed to start bot")
