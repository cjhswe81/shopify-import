import xml.etree.ElementTree as ET
import json
import requests
import os
import time
import urllib.parse
from dotenv import load_dotenv
import re
from datetime import datetime
from PIL import Image
from io import BytesIO
import base64

# ----- BILDVALIDERINGS-CACHE -----
CACHE_FILE = "chevalier_image_imported.json"
VALIDATION_CACHE_FILE = "chevalier_validation_cache.json"

# Bildvaliderings-gränser (Google Shopping-kompatibla)
MAX_SIZE_MB = 16  # Google Shopping max (16MB)
MAX_PIXELS = 8000  # Google Shopping max (64 megapixels ≈ 8000x8000)
RESIZE_MAX_DIMENSION = 1500  # Google Shopping rekommendation (1500x1500)

if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r") as f:
        image_import_cache = json.load(f)
else:
    image_import_cache = {}

if os.path.exists(VALIDATION_CACHE_FILE):
    with open(VALIDATION_CACHE_FILE, "r") as f:
        image_validation_cache = json.load(f)
else:
    image_validation_cache = {}

def resize_image(image_data, max_dimension=RESIZE_MAX_DIMENSION):
    """Resiza och optimera en bild till max dimension"""
    try:
        img = Image.open(BytesIO(image_data))

        # Konvertera RGBA/LA/P till RGB (ta bort alpha channel)
        if img.mode in ('RGBA', 'LA', 'P'):
            # Skapa vit bakgrund
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Resiza med bibehållen aspect ratio
        img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

        # Spara optimerad som JPEG
        output = BytesIO()
        img.save(output, format='JPEG', quality=85, optimize=True)
        output.seek(0)

        return output.getvalue()
    except Exception as e:
        print(f"⚠️ Kunde inte resiza bild: {e}")
        return None

def prepare_image_for_shopify(url):
    """
    Hämtar och förbereder en bild för Shopify.
    Returnerar:
      - {"src": url} om bilden är OK som den är
      - {"attachment": base64_data, "filename": filename} om bilden behövde resizas
      - None om bilden inte kunde hanteras
    """
    if not url:
        return None

    # Kolla cache först
    if url in image_validation_cache:
        cache_entry = image_validation_cache[url]
        if cache_entry.get("valid") and not cache_entry.get("resized"):
            return {"src": url}
        elif cache_entry.get("resized"):
            # Behöver ladda om och resiza varje gång (vi cachar inte base64)
            pass
        elif not cache_entry.get("valid") and cache_entry.get("failed"):
            return None

    try:
        # Försök hämta bilden
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            print(f"⚠️ Bild kunde inte hämtas: {url}")
            image_validation_cache[url] = {"valid": False, "failed": True}
            return None

        image_data = resp.content
        img = Image.open(BytesIO(image_data))
        width, height = img.width, img.height
        size_mb = len(image_data) / (1024 * 1024)

        # Kolla om bilden behöver resizas
        needs_resize = (
            size_mb > MAX_SIZE_MB or
            width > MAX_PIXELS or
            height > MAX_PIXELS
        )

        if needs_resize:
            print(f"📐 Resizar bild: {url} ({width}x{height}, {size_mb:.1f}MB) → max {RESIZE_MAX_DIMENSION}x{RESIZE_MAX_DIMENSION}")
            resized_data = resize_image(image_data)

            if resized_data:
                # Konvertera till base64 för Shopify attachment
                base64_data = base64.b64encode(resized_data).decode('utf-8')

                # Extrahera filnamn från URL
                filename = os.path.basename(urllib.parse.urlparse(url).path)
                if not filename.lower().endswith(('.jpg', '.jpeg')):
                    filename = os.path.splitext(filename)[0] + '.jpg'

                # Cacha att denna bild behöver resizas
                image_validation_cache[url] = {
                    "valid": True,
                    "resized": True,
                    "original_size": f"{width}x{height}",
                    "original_mb": round(size_mb, 1)
                }

                return {
                    "attachment": base64_data,
                    "filename": filename
                }
            else:
                print(f"⚠️ Kunde inte resiza bild: {url}")
                image_validation_cache[url] = {"valid": False, "failed": True}
                return None
        else:
            # Bilden är OK som den är
            image_validation_cache[url] = {
                "valid": True,
                "width": width,
                "height": height,
                "size_mb": round(size_mb, 1)
            }
            return {"src": url}

    except Exception as e:
        print(f"⚠️ Bildproblem: {url} - {e}")
        image_validation_cache[url] = {"valid": False, "failed": True}
        return None

def is_image_imported(url):
    return image_import_cache.get(url, False)

def mark_image_imported(url):
    image_import_cache[url] = True
    with open(CACHE_FILE, "w") as f:
        json.dump(image_import_cache, f)

def save_image_import_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(image_import_cache, f)

def save_validation_cache():
    with open(VALIDATION_CACHE_FILE, "w") as f:
        json.dump(image_validation_cache, f)

import atexit
@atexit.register
def save_all_caches():
    save_image_import_cache()
    save_validation_cache()

# ----- RESTEN AV SCRIPTET -----
load_dotenv()
SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL")
SHOPIFY_API_KEY = os.getenv("SHOPIFY_API_KEY")

if not SHOPIFY_STORE_URL or not SHOPIFY_API_KEY:
    print("❌ Error: SHOPIFY_STORE_URL or SHOPIFY_API_KEY missing in .env file!")
    exit()

def get_identifier_from_xml_url(url):
    parsed_url = urllib.parse.urlparse(url)
    basename = os.path.basename(parsed_url.path)
    identifier, _ = os.path.splitext(basename)
    return identifier.lower()

def get_base_without_hash(filename):
    """
    Remove hash/UUID suffixes from filenames.
    Handles multiple formats:
    - image_1_e450759a-fd73-4409-a7f2-6410c82dee8e -> image
    - image_4f68b42b-9d99-41c0-ba7b-ee8caa2acee7 -> image
    - image-123 -> image-123 (unchanged)
    """
    # Keep removing suffix parts that look like hashes/UUIDs
    while True:
        parts = filename.rsplit("_", 1)
        if len(parts) == 2:
            suffix = parts[1]
            # UUID pattern: 8-4-4-4-12 (e.g., 4f68b42b-9d99-41c0-ba7b-ee8caa2acee7)
            # Hash pattern: long alphanumeric string (>= 32 chars typically)
            # Simple numeric: _1, _2, etc (handled separately)

            # Check for UUID pattern (has 4 dashes and is 36 chars)
            if "-" in suffix and len(suffix) >= 32:
                dash_count = suffix.count("-")
                # UUID has exactly 4 dashes
                if dash_count >= 3:  # UUID or similar hash
                    filename = parts[0]
                    continue

            # Check for simple hash (long alphanumeric, no dashes, >= 16 chars)
            elif len(suffix) >= 16 and suffix.isalnum():
                filename = parts[0]
                continue

            # Check for simple numeric suffix _1, _2, etc
            elif suffix.isdigit() and len(suffix) <= 2:
                filename = parts[0]
                continue

        break

    return filename.lower()

def create_handle(title):
    handle = title.lower()
    handle = re.sub(r"\.", "-", handle)
    handle = re.sub(r"[^\w\s-]", "", handle)
    handle = re.sub(r"\s+", "-", handle.strip())
    handle = re.sub(r"-+", "-", handle)
    return handle

SHOPIFY_API_ENDPOINT = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products.json"
SHOPIFY_SMART_COLLECTIONS_ENDPOINT = (
    f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/smart_collections.json"
)

xml_url = "https://www.chevalier.se/pricecomparison/hyperdrive.xml?IncludeHiddenProducts=false"

try:
    response = requests.get(xml_url)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    print("✅ Successfully fetched XML data.")
except requests.exceptions.RequestException as e:
    print(f"❌ Error fetching XML: {e}")
    exit()

def group_products(xml_root):
    groups = {}
    for product in xml_root.findall("product"):
        title_elem = product.find("name")
        if title_elem is not None and title_elem.text:
            title = title_elem.text.strip()
            handle = create_handle(title)
        else:
            handle = "no-title"
        groups.setdefault(handle, []).append(product)
    return groups

def determine_product_categories(first):
    allowed = {
        "dam": "Dam",
        "handskar": "Handskar",
        "mössor och kepsar": "Mössor och Kepsar",
        "accessoarer": "Accessoarer",
        "herr": "Herr",
        "jackor": "Jackor",
        "t-shirts": "T-shirts",
        "byxor": "Byxor",
        "regnkläder": "Regnkläder",
        "västar": "Västar",
        "väskor": "Väskor",
        "skor": "Skor",
        "tröjor": "Tröjor",
        "skjortor": "Skjortor",
        "tweed": "Tweed",
        "underställ": "Underställ",
        "shorts": "Shorts",
    }
    extra_map = {
        "huvudbonader": "Mössor och Kepsar",
        "barnkläder": "Barn och Ungdom",
        "kängor": "Skor",
    }
    final_categories = []
    for cat in first.findall("categories/category"):
        text = cat.text
        if text:
            parts = [p.strip() for p in text.split(">")]
            for part in parts:
                lower_part = part.lower()
                candidate = extra_map.get(lower_part) or allowed.get(lower_part)
                if candidate and candidate not in final_categories:
                    final_categories.append(candidate)
    return final_categories

def extract_group_product_data(products):
    first = products[0]
    title_elem = first.find("name")
    title = title_elem.text.strip() if title_elem is not None else "No title"

    desc_elem = first.find("description")
    if desc_elem is not None and desc_elem.text:
        description = desc_elem.text.strip()
    else:
        html_desc_elem = first.find("html-description")
        description = (
            html_desc_elem.text.strip()
            if html_desc_elem is not None and html_desc_elem.text
            else ""
        )

    vendor = "Chevalier"
    product_categories = determine_product_categories(first)

    genders = set()
    for cat in first.findall("categories/category"):
        cat_text = cat.text
        if cat_text:
            lower_text = cat_text.lower()
            if "herr" in lower_text or "män" in lower_text:
                genders.add("Herr")
            if "dam" in lower_text or "kvinnor" in lower_text:
                genders.add("Dam")
    if not genders and "Accessoarer" not in product_categories:
        genders = {"Herr", "Dam"}
    gender_list = sorted(genders)

    variants = []
    image_urls = set()
    variant_image_map = {}

    for prod in products:
        sku_elem = prod.find("sku")
        sku = sku_elem.text.strip() if sku_elem is not None and sku_elem.text else ""
        sku_lower = sku.lower()

        color = (
            prod.find("sub-name").text.strip()
            if prod.find("sub-name") is not None and prod.find("sub-name").text
            else ""
        )
        size = (
            prod.find("SIZE").text.strip()
            if prod.find("SIZE") is not None and prod.find("SIZE").text
            else ""
        )

        variant = {
            "sku": sku,
            "option1": color,
            "option2": size,
            "price": (
                prod.find("price-with-vat").text.replace(",", ".").strip()
                if prod.find("price-with-vat") is not None
                and prod.find("price-with-vat").text
                else "0.00"
            ),
            "barcode": (
                prod.find("gtin-ean").text.strip()
                if prod.find("gtin-ean") is not None and prod.find("gtin-ean").text
                else ""
            ),
            "inventory_quantity": (
                int(prod.find("stock-level").text.strip())
                if prod.find("stock-level") is not None
                and prod.find("stock-level").text.isdigit()
                else 0
            ),
            "inventory_management": "shopify",
            "inventory_policy": "deny",
        }
        variants.append(variant)

        images = prod.findall("images/image")
        if images:
            first_image_url = images[0].text.strip() if images[0].text else None
            if first_image_url:
                variant_image_map[sku_lower] = first_image_url
                color_key = color.lower()
                if color_key and color_key not in variant_image_map:
                    variant_image_map[color_key] = first_image_url
            for img in images:
                url = img.text.strip() if img.text else None
                if url and not is_image_imported(url):
                    image_urls.add(url)

    colors = list({v["option1"] for v in variants})
    sizes = list({v["option2"] for v in variants})
    options = [
        {"name": "Color", "position": 1, "values": colors},
        {"name": "Size", "position": 2, "values": sizes},
    ]

    handle = create_handle(title)
    primary_tag = f"handle:{handle}"
    all_tags = [primary_tag] + product_categories + sorted(gender_list)
    tags = ", ".join(all_tags)

    # Förbered bilder (kan vara resizade)
    images = []
    original_urls = []  # Spara original-URLer för att märka som importerade
    for url in image_urls:
        image_data = prepare_image_for_shopify(url)
        if image_data:
            images.append(image_data)
            original_urls.append(url)  # Spara original-URL

    product_data = {
        "title": title,
        "handle": handle,
        "body_html": description,
        "vendor": vendor,
        "tags": tags,
        "options": options,
        "variants": variants,
        "images": images,
        "original_image_urls": original_urls,  # Inkludera original-URLer
        "variant_image_map": variant_image_map,
        "published_scope": "global",
    }
    return product_data

def find_product_by_handle(product_title):
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }
    handle = create_handle(product_title)
    search_url = (
        f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products.json?handle={handle}"
    )
    response = requests.get(search_url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        products = data.get("products", [])
        if products:
            return products[0]["id"]
    return None

def update_product(product_id, product_data):
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }
    product_url = (
        f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products/{product_id}.json"
    )
    current_resp = requests.get(product_url, headers=headers)
    if current_resp.status_code != 200:
        print(f"❌ Failed to fetch current product {product_id}: {current_resp.text}")
        return None
    current_product = current_resp.json().get("product", {})
    current_variants = current_product.get("variants", [])
    current_map = {
        variant.get("sku", "").strip().lower(): variant for variant in current_variants
    }

    for new_var in product_data.get("variants", []):
        new_sku = new_var.get("sku", "").strip().lower()
        if new_sku in current_map:
            current_map[new_sku]["price"] = new_var["price"]
            # NOTE: inventory_quantity cannot be updated via Products API
            # It will be updated separately via update_inventory_levels()
            current_map[new_sku]["barcode"] = new_var.get(
                "barcode", current_map[new_sku].get("barcode", "")
            )
        else:
            current_variants.append(new_var)

    current_images = current_product.get("images", [])
    for image in product_data.get("images", []):
        # Kan vara antingen {"src": url} eller {"attachment": ..., "filename": ...}
        if image.get("src"):
            # URL-baserad bild
            src = image.get("src")
            base = get_base_without_hash(os.path.splitext(os.path.basename(src))[0])
            duplicate_found = any(
                base == get_base_without_hash(os.path.splitext(os.path.basename(existing_image.get("src", "") or ""))[0])
                for existing_image in current_images
            )
            if not duplicate_found:
                current_images.append({"src": src})
        elif image.get("attachment"):
            # Resizad bild som attachment
            filename = image.get("filename", "image.jpg")
            base = get_base_without_hash(os.path.splitext(filename)[0])
            duplicate_found = any(
                base == get_base_without_hash(os.path.splitext(os.path.basename(existing_image.get("src", "") or ""))[0])
                for existing_image in current_images
            )
            if not duplicate_found:
                current_images.append(image)

    updated_data = {
        "id": product_id,
        "variants": current_variants,
        "images": current_images,
    }
    payload = {"product": updated_data}
    update_resp = requests.put(product_url, json=payload, headers=headers)
    if update_resp.status_code == 200:
        print(f"✅ Successfully updated product: {product_data['title']} (ID: {product_id}) with updated price/inventory, variants and images.")
        return product_id
    else:
        print(
            f"❌ Failed to update product: {product_data['title']} (ID: {product_id})"
        )
        print(update_resp.text)
        return None

def assign_variant_images(product_id, variant_image_map):
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }
    product_url = (
        f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products/{product_id}.json"
    )
    product_resp = requests.get(product_url, headers=headers)
    if product_resp.status_code != 200:
        print(
            f"❌ Failed to fetch product {product_id} for variant image assignment: {product_resp.text}"
        )
        return
    product_data = product_resp.json().get("product", {})

    image_mapping = {}
    for img in product_data.get("images", []):
        src = img.get("src", "")
        base = os.path.splitext(os.path.basename(src))[0].lower()
        image_mapping[base] = img.get("id")

    updated_variants = []
    for variant in product_data.get("variants", []):
        sku = variant.get("sku", "").strip().lower()
        color_in_variant = variant.get("option1", "").strip().lower()
        assigned_image_url = variant_image_map.get(sku) or variant_image_map.get(
            color_in_variant
        )
        if assigned_image_url:
            feed_identifier = os.path.splitext(
                os.path.basename(urllib.parse.urlparse(assigned_image_url).path)
            )[0].lower()
            found_image_id = None
            for shopify_identifier, shopify_image_id in image_mapping.items():
                if feed_identifier in shopify_identifier:
                    found_image_id = shopify_image_id
                    break
            if found_image_id:
                variant["image_id"] = found_image_id
        updated_variants.append(variant)

    update_payload = {"product": {"id": product_id, "variants": updated_variants}}
    update_resp = requests.put(product_url, json=update_payload, headers=headers)
    if update_resp.status_code != 200:
        print(
            f"❌ Failed to update variant image assignments for product {product_id}: {update_resp.text}"
        )

def update_inventory_levels(product_id, product_data):
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }
    locations_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/locations.json"
    loc_resp = requests.get(locations_url, headers=headers)
    if loc_resp.status_code != 200:
        print(f"❌ CRITICAL: Failed to fetch locations for inventory update!")
        print(f"   Status code: {loc_resp.status_code}")
        print(f"   Error: {loc_resp.text}")
        print(f"   ⚠️  Inventory levels will NOT be updated! Check API permissions (read_locations scope required)")
        return
    locations = loc_resp.json().get("locations", [])
    if not locations:
        print("❌ CRITICAL: No locations found! Inventory levels will NOT be updated!")
        return
    location_id = locations[0]["id"]
    print(f"📍 Using location ID: {location_id} for inventory updates")

    product_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products/{product_id}.json"
    prod_resp = requests.get(product_url, headers=headers)
    if prod_resp.status_code != 200:
        print(f"❌ Failed to fetch product {product_id} for inventory update: {prod_resp.text}")
        return
    product = prod_resp.json().get("product", {})
    shopify_variants = product.get("variants", [])

    sku_to_inventory_item_id = {}
    for variant in shopify_variants:
        sku = variant.get("sku", "").lower()
        inventory_item_id = variant.get("inventory_item_id")
        if sku and inventory_item_id:
            sku_to_inventory_item_id[sku] = inventory_item_id

    for variant in product_data.get("variants", []):
        sku = variant.get("sku", "").lower()
        desired_qty = variant.get("inventory_quantity", 0)
        inventory_item_id = sku_to_inventory_item_id.get(sku)
        if not inventory_item_id:
            print(f"❌ No inventory_item_id found for SKU: {sku}")
            continue
        update_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/inventory_levels/set.json"
        payload = {
            "location_id": location_id,
            "inventory_item_id": inventory_item_id,
            "available": desired_qty
        }
        inv_resp = requests.post(update_url, json=payload, headers=headers)
        if inv_resp.status_code == 200:
            print(f"✅ Inventory for SKU {sku} updated to {desired_qty}")
        else:
            print(f"❌ Failed to update inventory for SKU {sku}: {inv_resp.text}")
        time.sleep(0.6)

def send_to_shopify(product_data):
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }
    existing_id = find_product_by_handle(product_data["title"])
    if existing_id:
        prod_id = update_product(existing_id, product_data)
    else:
        response = requests.post(
            SHOPIFY_API_ENDPOINT, json={"product": product_data}, headers=headers
        )
        if response.status_code == 201:
            prod_id = response.json()["product"]["id"]
            print(f"✅ Successfully added product: {product_data['title']} (ID: {prod_id})")
        else:
            print(f"❌ Failed to add product: {product_data['title']}")
            print(f"Error: {response.text}")
            prod_id = None

    if prod_id:
        # Märk bilder som importerade (använd original-URLer)
        for url in product_data.get("original_image_urls", []):
            mark_image_imported(url)

        update_inventory_levels(prod_id, product_data)
        if "variant_image_map" in product_data:
            assign_variant_images(prod_id, product_data["variant_image_map"])
        # Ensure product is published globally (visible on all sales channels)
        ensure_global_publication(prod_id)
    return prod_id

def ensure_global_publication(product_id):
    """
    Ensure product is published with global scope, making it visible on all sales channels
    including Online Store, Google & YouTube, Facebook & Instagram, etc.
    """
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }

    # Update product to have global published_scope
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products/{product_id}.json"
    payload = {
        "product": {
            "id": product_id,
            "published_scope": "global",
            "status": "active"
        }
    }

    resp = requests.put(url, json=payload, headers=headers)

    if resp.status_code == 200:
        print(f"   📢 Product published globally (visible on all sales channels)")
    else:
        print(f"   ⚠️  Could not set global publication: {resp.status_code}")
        if resp.text:
            print(f"      Error: {resp.text}")

def get_existing_smart_collections():
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }
    response = requests.get(SHOPIFY_SMART_COLLECTIONS_ENDPOINT, headers=headers)
    if response.status_code == 200:
        data = response.json()
        return {sc["title"]: sc["id"] for sc in data.get("smart_collections", [])}
    else:
        print("❌ Failed to fetch smart collections")
        print(response.text)
        return {}

def create_smart_collection(title):
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }
    payload = {
        "smart_collection": {
            "title": title,
            "rules": [{"column": "tag", "relation": "equals", "condition": title}],
        }
    }
    response = requests.post(
        SHOPIFY_SMART_COLLECTIONS_ENDPOINT, json=payload, headers=headers
    )
    if response.status_code == 201:
        sc = response.json()["smart_collection"]
        print(f"✅ Created smart collection: {title} (ID: {sc['id']})")
        return sc["id"]
    else:
        print(f"❌ Failed to create smart collection: {title}")
        print(response.text)
        return None

# ----- HUVUDFLÖDE -----
start_time = datetime.now()
print(f"🕐 Script started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")

grouped_products = group_products(root)
grouped_products_list = list(grouped_products.values())
print(f"Found {len(grouped_products_list)} product groups from XML.")

products_data = [extract_group_product_data(group) for group in grouped_products_list]

imported_product_ids = []
for product in products_data:
    prod_id = send_to_shopify(product)
    if prod_id:
        imported_product_ids.append(prod_id)
    time.sleep(1.0)  # För att undvika rate limits

save_image_import_cache()  # Sparar cache ännu en gång efter loopen

unique_tags = set()
for product in products_data:
    tags_str = product.get("tags", "")
    for tag in tags_str.split(","):
        t = tag.strip()
        if t and not t.startswith("group_sku:") and not t.startswith("handle:"):
            unique_tags.add(t)
print("Unika taggar (för smart collections):", unique_tags)

existing_collections = get_existing_smart_collections()
for tag in unique_tags:
    if tag not in existing_collections:
        created_id = create_smart_collection(tag)
        if created_id:
            existing_collections[tag] = created_id
    else:
        print(
            f"Smart collection '{tag}' already exists (ID: {existing_collections[tag]})"
        )

# Calculate and display execution time
end_time = datetime.now()
duration = end_time - start_time
hours, remainder = divmod(duration.total_seconds(), 3600)
minutes, seconds = divmod(remainder, 60)

print(f"\n⏱️ Script completed at: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"📊 Total execution time: {int(hours)}h {int(minutes)}m {int(seconds)}s")
print(f"📦 Products processed: {len(grouped_products_list)}")
print(f"✅ Products imported: {len(imported_product_ids)}")
