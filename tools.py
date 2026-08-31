import json
from typing import Dict, Any, Tuple, List
import os
from pathlib import Path
from jinja2 import Template


def _normalize_product(product: str) -> str:
    product = product.strip().lower()

    aliases = {
        "pocket square": "pocket_squares",
        "pocket squares": "pocket_squares",
        "pocket_square": "pocket_squares",
        "bouquet": "bouquets",
        "bouquets": "bouquets",
        "boutonniere": "boutonnieres",
        "boutonnieres": "boutonnieres",
    }

    return aliases.get(product, product.replace(" ", "_"))

def get_profile(type: str, product: str, campaign_event: str) -> Dict[str, Any]:
    """
    Use this function first to retrieve the profiles for a particular
    product and campaign/event combination.

    Call this function twice:

    1) With type="email_profiles" to learn which content blocks are
       available and when each should be used.

    2) With type="image_profiles" to learn which images are available
       and when each should be used.

    Args:
        type: The profile type to retrieve. Must be one of
            "email_profiles" or "image_profiles".
        product: The product to retrieve the profile for.
        campaign_event: The campaign/event to retrieve the profile for.
    """
    project_root = os.path.dirname(os.path.abspath(__file__))
    product = _normalize_product(product)
    campaign_event = campaign_event.strip().lower().replace(" ", "_")

    filename = os.path.join(
        project_root,
        "storehouse",
        type,
        f"{product}_{campaign_event}.json",
    )

    with open(filename, "r") as f:
        return json.load(f)


def list_block_types_subtypes() -> Dict[str, List[str]]:
    """
    List all type/subtype combinations in the storehouse.
    """
    block_types_subtypes = {}
    project_root = os.path.dirname(os.path.abspath(__file__))
    blocks_dir = f"{project_root}/storehouse/blocks"
    for block_type in os.listdir(blocks_dir):
        block_types_subtypes[block_type] = []
        for block_subtype in os.listdir(f"{blocks_dir}/{block_type}"):
            block_types_subtypes[block_type].append(block_subtype)
    return block_types_subtypes


def get_block(block_type: str, block_subtype: str) -> Tuple[Dict[str, Any], str]:
    """
    Get an HTML block to be used in an email.

    Args:
        block_type: The type of block to get. Must be one of "headers", "heroes", "content", "products", "ctas", "footers".
        block_subtype: The subtype of block to get. Must be one of "centered_logo", "logo_with_tagline", "minimal_logo", "full_image", "split_image_left", "split_image_right", "image_left", "image_right", "centered_text", "single_feature", "two_column", "three_column", "banner_cta", "centered_button", "text_link".
    """
    project_root = os.path.dirname(os.path.abspath(__file__))
    filename_json = f"{project_root}/storehouse/blocks/{block_type}/{block_subtype}/block.json"
    filename_html = f"{project_root}/storehouse/blocks/{block_type}/{block_subtype}/block.html"
    config = open(filename_json, "r").read()
    html_block = open(filename_html, "r").read()
    config = json.loads(config)
    return config, html_block


def get_image(image_id: str) -> str:
    """
    Get the local path for an image in the storehouse.

    image_id MUST be one of the exact image IDs returned by the matching
    image profile. Do not invent, modify, pluralize, singularize, or
    otherwise construct an image ID. The file extension is discovered
    automatically.
    """
    project_root = Path(__file__).resolve().parent
    image_dir = project_root / "storehouse" / "assets" / "images"
    matches = list(image_dir.glob(f"{image_id}.*"))

    if not matches:
        raise FileNotFoundError(
            f"No image found with id '{image_id}'"
        )

    return str(matches[0])


def list_images() -> List[str]:
    """
    List all images in the storehouse.
    """
    project_root = os.path.dirname(os.path.abspath(__file__))
    images_dir = f"{project_root}/storehouse/assets/images"
    return os.listdir(images_dir)


def list_logos() -> List[str]:
    """
    List all logos in the storehouse.
    """
    project_root = os.path.dirname(os.path.abspath(__file__))
    logos_dir = f"{project_root}/storehouse/assets/logos"
    return os.listdir(logos_dir)


def get_logo() -> str:
    """
    Return the local path for the primary Serenity Blooms logo.

    There is only one primary logo. Do not construct or provide a logo ID;
    call this function with no arguments. The file extension is discovered
    automatically.
    """
    project_root = Path(__file__).resolve().parent
    logo_dir = project_root / "storehouse" / "assets" / "logos"
    matches = list(logo_dir.glob("logo_01.*"))

    if not matches:
        raise FileNotFoundError("Primary logo not found.")

    return str(matches[0])

def get_brand() -> dict:
    """
    Return the Serenity Blooms brand guidelines.

    Use this information to guide visual, typography, color,
    image, logo, copy, and email-design decisions.
    """

    project_root = os.path.dirname(
        os.path.abspath(__file__)
    )

    filename = os.path.join(
        project_root,
        "storehouse",
        "assets",
        "brand_profile.json",
    )

    with open(filename, "r") as f:
        return json.load(f)


def render_block(config: Dict[str, Any], html_block: str) -> str:
    """
    Render an HTML block with a given configuration.
    """
    template = Template(html_block)
    return template.render(**config)


def stitch_email(rendered_blocks: List[str]) -> str:
    """
    Stitch together a list of rendered blocks into a complete email.
    """
    return "\n".join(rendered_blocks)
