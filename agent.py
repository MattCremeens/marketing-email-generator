from tools import *
from google.adk.agents import Agent


GEMINI_MODEL = "gemini-3.5-flash-lite"


root_agent = Agent(
    name="test_agent",        
    model=GEMINI_MODEL,
    description=(
    "You can create cool emails."
    ),
    instruction=(
    """Create a cool marketing email as an HTML string.

Asset IDs are authoritative. When selecting an image, use an image_id
exactly as returned by the matching image profile. Never invent, modify,
pluralize, singularize, or otherwise construct an image ID.

Do not construct logo IDs. Use get_logo() with no arguments to retrieve
the primary Serenity Blooms logo.
"""
    ),
        
    tools=[get_profile, 
           list_block_types_subtypes, 
           get_block, 
           get_image, 
           get_logo, 
           list_images, 
           list_logos, 
           render_block, 
           stitch_email],
)