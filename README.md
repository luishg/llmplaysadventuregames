# LLM Plays Adventure Games

An AI system that uses Large Language Models (LLMs) to play point-and-click adventure games on Linux. The system captures game screenshots, analyzes them using various LLMs (local or remote), and performs actions based on the analysis.

## Key Features

- **Multi-LLM Support**: Works with local Ollama models, OpenAI, Anthropic, and Hugging Face models
- **Dynamic Window Selection**: Automatically detects and targets game windows
- **Live Status Display**: Real-time visualization of AI's analysis and actions
- **Context Memory System**: Maintains game state and strategy across iterations
- **Session Logging**: Saves screenshots and LLM responses for analysis
- **Grid-Based Navigation**: Uses a numbered cell system for precise interaction
- **Long-term Strategy Development**: Updates game context, map, and objectives every 10 iterations
- **Twitch Chat Integration**: Allows viewers to provide hints and suggestions through chat
- **Real-time Chat Monitoring**: Displays chat messages and user suggestions in a dedicated window
- **User Command Execution**: Processes and executes valid commands from chat users

## New Features

### Improved Navigation System
- Replaced pixel-based coordinates with a numbered cell grid system
- Makes it easier for LLMs to understand and interact with the game
- More accurate than pixel counting for click actions
- Based on the [GridGPT](https://github.com/quinny1187/GridGPT) approach

### Enhanced Context Memory
Every 10 iterations, the system pauses to update three key components:

1. **Game Map**: 
   - Tracks discovered rooms and their connections
   - Maintains a persistent map of the game world
   - Updates based on the last 10 screen descriptions

2. **Game Objectives**:
   - Maintains a prioritized list of goals
   - Tracks completed and active objectives
   - Includes discovered clues and hints

3. **Game Context**:
   - Summarizes recent actions
   - Reduces repetition in future actions
   - Improves action variation

### Improved Text Capture
- Extended game screen text duration
- Takes snapshots every 3 seconds
- Ensures dialogue and important text is captured

### Twitch Chat Integration
The system now includes a powerful Twitch chat integration that allows viewers to participate in the gameplay:

1. **Chat Command System**:
   - Users can provide hints and suggestions through chat
   - Commands are processed every 5 iterations
   - Supports both cell-based and pixel-based coordinates
   - Valid commands are displayed in real-time

2. **Chat Monitor Window**:
   - Real-time display of chat messages
   - Shows connection status and statistics
   - Lists recent user suggestions
   - Tracks executed commands

3. **Command Processing**:
   - Validates user commands before execution
   - Converts pixel coordinates to cell numbers
   - Maintains a history of executed commands
   - Provides feedback on command execution

4. **Safety Features**:
   - Command validation and sanitization
   - Rate limiting for command execution
   - Error handling for invalid commands
   - Connection status monitoring

## Performance

The system has demonstrated impressive capabilities:
- Successfully explores game environments
- Discovers hidden passages and items
- Understands game mechanics and puzzles
- Maintains context across different game areas

For example, with GPT-4.1, the system:
- Explored the main hall and office
- Discovered the passage behind the clock
- Found Fred's lab
- Identified the need for a diamond to power the time machine

## Potential Applications

This system could serve as a benchmark for evaluating LLM performance in adventure games:
- Tests spatial reasoning
- Evaluates puzzle-solving abilities
- Measures context retention
- Assesses strategic planning

Complex games like "Day of the Tentacle" (with multiple timelines) could provide excellent test cases for evaluating LLM capabilities.

## Requirements

- Linux system with X11
- Python 3.8+
- Required Python packages (see requirements.txt)
- X11 tools (xdotool, xprop)
- Optional: Ollama for local LLM support
- Optional: API keys for remote LLM services

## Installation

1. Clone the repository
2. Install required packages:
    ```bash
    pip install -r requirements.txt
    ```
3. Install X11 tools:
        ```bash
   sudo dnf install xdotool xorg-x11-utils
        ```
4. (Optional) Set up Ollama for local LLM support
5. (Optional) Configure API keys for remote LLM services

## Usage

1. Run the script:
    ```bash
    python play.py
    ```
2. Select the game window when prompted
3. Choose an LLM model
4. Watch the AI play!

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

```
MIT License

Copyright (c) 2025 Luis Hernandez @luishg

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```