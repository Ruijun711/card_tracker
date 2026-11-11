"""Conservative fallback to catalog art for unambiguous printed card numbers.

Catalog entries with multiple variants are deliberately excluded: the catalog
seed does not contain rarity, so matching those would risk showing wrong art.
"""
from collections import defaultdict

def catalog_image_index(db):
    groups=defaultdict(list)
    for r in db.execute('SELECT card_number,image_url FROM encyclopedia WHERE image_url != ?', ('',)):
        groups[r['card_number'].strip().upper()].append(r['image_url'])
    return {k: urls[0] for k,urls in groups.items() if len(urls)==1}

def choose_card_image(scope,item_id,number,remote,overrides,catalog_images):
    from catalog import resolved_image
    if overrides.get((scope,item_id)):
        return resolved_image(scope,item_id,remote,overrides)
    return remote or catalog_images.get((number or '').strip().upper(),'')
