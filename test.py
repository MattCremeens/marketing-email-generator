from tools import *


if __name__ == "__main__":
    #profile = get_profile("email_profiles", "bouquets", "homecoming")
    #print(profile)

    #block = get_block("content", "centered_text")
    #rendered_block = render_block({"headline": "I Love Flowers", "body": "Flowers are our friends!"}, block[1])
    #print(rendered_block)
    #image = get_image("bouquet_homecoming_01")
    #print(image)
    blocks = list_block_types_subtypes()
    print(blocks)
    logos = list_logos()
    print(logos)