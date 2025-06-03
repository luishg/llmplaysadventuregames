from PIL import Image, ImageDraw, ImageFont
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

def add_numbered_grid_to_image(image, cell_size=40):
    """
    Adds a numbered grid overlay to the image.
    Grid is designed for 640x480 resolution with 16x12 cells (40x40 pixels each).
    Cells are numbered from 1 to 192 (16x12).
    
    Args:
        image: PIL Image to add grid to
        cell_size: Size of each grid cell in pixels (default 40 for 640x480)
    
    Returns:
        PIL Image with grid overlay
    """
    if not image:
        logger.error("No image provided to add grid")
        return None
        
    # Create a copy of the image to draw on
    grid_image = image.copy().convert("RGBA")
    
    # Create a separate transparent layer for the grid
    grid_layer = Image.new("RGBA", grid_image.size, (0, 0, 0, 0))  # Fully transparent
    draw = ImageDraw.Draw(grid_layer)
    
    # Grid colors
    line_color = (255, 255, 255, 40)  # Semi-transparent white (47% opacity)
    text_color = (255, 255, 255, 180)  # White for numbers (slightly transparent)
    shadow_color = (0, 0, 0, 100)  # Semi-transparent black for text shadow
    
    # Calculate grid dimensions
    width, height = grid_image.size
    cell_size = 40  # Size of each cell in pixels
    num_cols = width // cell_size
    num_rows = height // cell_size
    
    # Try multiple font options
    font = None
    font_size = 14  # Slightly larger font for better visibility
    font_paths = [
        "arial.ttf"  # Current directory
    ]
    
    for font_path in font_paths:
        try:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, font_size)
                break
        except Exception as e:
            logger.debug(f"Failed to load font {font_path}: {e}")
    
    if not font:
        logger.warning("No system fonts found, using default font")
        font = ImageFont.load_default()
    
    # Draw grid lines
    # Draw vertical lines
    for x in range(0, width, cell_size):
        draw.line([(x, 0), (x, height)], fill=line_color, width=1)
    
    # Draw horizontal lines
    for y in range(0, height, cell_size):
        draw.line([(0, y), (width, y)], fill=line_color, width=1)
    
    # Add cell numbers
    cell_number = 1
    for row in range(num_rows):
        for col in range(num_cols):
            # Calculate cell boundaries
            x1 = col * cell_size
            y1 = row * cell_size
            x2 = x1 + cell_size
            y2 = y1 + cell_size
            
            # Get text size for centering
            number_str = str(cell_number)
            if hasattr(font, "getbbox"):
                bbox = font.getbbox(number_str)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            else:
                text_width = len(number_str) * 8
                text_height = font_size
            
            # Center text in cell
            text_x = x1 + (cell_size - text_width) // 2
            text_y = y1 + (cell_size - text_height) // 2
            
            # Draw text shadow first (slightly offset)
            draw.text((text_x + 2, text_y + 2), number_str, fill=shadow_color, font=font)
            # Draw the actual text
            draw.text((text_x, text_y), number_str, fill=text_color, font=font)
            
            cell_number += 1
    
    # Composite the grid layer onto the image
    grid_image = Image.alpha_composite(grid_image, grid_layer)
    
    return grid_image

def get_cell_coordinates(cell_number, image_width=None, image_height=None, cell_size=40):
    """
    Converts a cell number to pixel coordinates based on the actual grid dimensions.
    
    Args:
        cell_number: Integer representing the cell number (1-based)
        image_width: Width of the image in pixels
        image_height: Height of the image in pixels
        cell_size: Size of each grid cell in pixels (default 40)
        
    Returns:
        Tuple of (x, y) pixel coordinates for center of cell
    """
    if not isinstance(cell_number, int) or cell_number < 1:
        logger.error(f"Invalid cell number: {cell_number}. Must be a positive integer.")
        return None
    
    # Convert to 0-based index
    cell_idx = cell_number - 1
    
    # Calculate number of columns and rows based on image dimensions
    if image_width and image_height:
        num_cols = image_width // cell_size
        num_rows = image_height // cell_size
    else:
        # Default to 16x12 grid if dimensions not provided
        num_cols = 16
        num_rows = 12
    
    # Calculate row and column
    row = cell_idx // num_cols
    col = cell_idx % num_cols
    
    # Validate if the cell number is within bounds
    if row >= num_rows or col >= num_cols:
        logger.error(f"Cell number {cell_number} is outside grid bounds (max: {num_cols * num_rows})")
        return None
    
    # Calculate center of cell
    x = (col * cell_size) + (cell_size // 2)
    y = (row * cell_size) + (cell_size // 2)
    
    return (x, y)

def get_cell_number_from_pixel(x: int, y: int, image_width: int, image_height: int) -> Optional[int]:
    """
    Convert pixel coordinates to a cell number.
    Returns None if coordinates are outside the image bounds.
    """
    if not (0 <= x < image_width and 0 <= y < image_height):
        return None

    # Calculate cell size
    cell_size = 40  # Same as in add_numbered_grid_to_image
    cells_per_row = image_width // cell_size
    cells_per_col = image_height // cell_size

    # Calculate cell coordinates
    cell_x = x // cell_size
    cell_y = y // cell_size

    # Calculate cell number (1-based)
    cell_number = (cell_y * cells_per_row) + cell_x + 1

    # Check if cell number is valid
    if 1 <= cell_number <= (cells_per_row * cells_per_col):
        return cell_number

    return None 