import csv
import os
import re
import time
import urllib.parse
import requests
from ftplib import FTP
from io import StringIO, BytesIO
from dotenv import load_dotenv
from PIL import Image  # Pillow för bildhantering
import json
from datetime import datetime
import base64

# Bildvaliderings-cache
IMAGE_IMPORTED_CACHE_FILE = "deerhunter_image_imported.json"
VALIDATION_CACHE_FILE = "deerhunter_validation_cache.json"
MAX_SIZE_MB = 16  # Google Shopping max (16MB)
MAX_PIXELS = 8000  # Google Shopping max (64 megapixels ≈ 8000x8000)
RESIZE_MAX_DIMENSION = 1500  # Google Shopping rekommendation (1500x1500)

if os.path.exists(IMAGE_IMPORTED_CACHE_FILE):
    with open(IMAGE_IMPORTED_CACHE_FILE, "r") as f:
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

def is_valid_shopify_image(url):
    """Bakåtkompatibel wrapper - kollar om bilden kan användas"""
    result = prepare_image_for_shopify(url)
    return result is not None

def is_image_imported(url):
    return image_import_cache.get(url, False)

def mark_image_imported(url):
    image_import_cache[url] = True
    with open(IMAGE_IMPORTED_CACHE_FILE, "w") as f:
        json.dump(image_import_cache, f)

def save_image_import_cache():
    with open(IMAGE_IMPORTED_CACHE_FILE, "w") as f:
        json.dump(image_import_cache, f)

def save_validation_cache():
    with open(VALIDATION_CACHE_FILE, "w") as f:
        json.dump(image_validation_cache, f)

import atexit
@atexit.register
def save_all_caches():
    save_image_import_cache()
    save_validation_cache()

# -------------------------------------------------------------

# Ladda miljövariabler från .env.dev
load_dotenv()
SHOPIFY_API_KEY = os.getenv("SHOPIFY_API_KEY")
SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL")
FTP_HOST = os.getenv("FTP_HOST")
FTP_USERNAME = os.getenv("FTP_USERNAME")
FTP_PASSWORD = os.getenv("FTP_PASSWORD")
FTP_FILE_PATH = os.getenv("FTP_FILE_PATH")  # ex. "/Deerhunter_Product_Information_SV.csv"

if not SHOPIFY_API_KEY or not SHOPIFY_STORE_URL:
    print("❌ Missing SHOPIFY_API_KEY or SHOPIFY_STORE_URL in .env.dev")
    exit()
if not FTP_HOST or not FTP_USERNAME or not FTP_PASSWORD or not FTP_FILE_PATH:
    print("❌ Missing FTP credentials in .env.dev")
    exit()

SHOPIFY_API_ENDPOINT = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products.json"
SHOPIFY_SMART_COLLECTIONS_ENDPOINT = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/smart_collections.json"

def download_csv_content_from_ftp():
    print(f"DEBUG: Försöker ansluta till FTP: {FTP_HOST}")
    try:
        ftp = FTP(FTP_HOST)
        ftp.login(user=FTP_USERNAME, passwd=FTP_PASSWORD)
        data = []
        ftp.retrbinary(f"RETR {FTP_FILE_PATH}", lambda chunk: data.append(chunk))
        ftp.quit()
        csv_bytes = b"".join(data)
        csv_text = csv_bytes.decode("utf-8-sig")
        print("✅ CSV-filens innehåll hämtat direkt från FTP.")
        return csv_text
    except Exception as e:
        print(f"❌ Fel vid FTP-nedladdning: {e}")
        exit()

def read_csv_data_from_ftp():
    csv_text = download_csv_content_from_ftp()
    csv_file = StringIO(csv_text)
    first_line = csv_file.readline()
    if first_line.strip().lower().startswith("sep="):
        print("DEBUG: Hittade 'sep=' rad, hoppar över den.")
    else:
        csv_file.seek(0)
    reader = csv.DictReader(csv_file, delimiter=";", quotechar='"')
    print(f"DEBUG: CSV Header: {reader.fieldnames}")
    rows = []
    row_index = 0
    for row in reader:
        row_index += 1
        product_name = row.get("Product_Name", "").strip()
        if not product_name:
            print(f"DEBUG: Skipping row #{row_index} p.g.a. saknad Product_Name.")
            continue
        rows.append(row)
    print(f"DEBUG: read_csv_data_from_ftp returnerar {len(rows)} rader.")
    return rows

def create_handle(title):
    handle = title.lower()
    # Normalize Swedish characters to match Shopify's ASCII conversion
    handle = handle.replace("å", "a").replace("ä", "a").replace("ö", "o")
    handle = re.sub(r"½", "", handle)
    handle = re.sub(r"®", "", handle)
    handle = re.sub(r"\.", "-", handle)
    handle = re.sub(r"[^\w\s-]", "", handle)
    handle = re.sub(r"\s+", "-", handle.strip())
    handle = re.sub(r"-+", "-", handle)
    return handle

def group_products(rows):
    groups = {}
    for row in rows:
        product_number = row.get("Product_Number", "").strip()
        if not product_number:
            continue
        groups.setdefault(product_number, []).append(row)
    return groups

def get_base_without_hash(filename):
    """
    Remove hash/UUID suffixes from filenames.
    Handles multiple formats:
    - D_M_F_3733-642_1_e450759a-fd73-4409-a7f2-6410c82dee8e -> d_m_f_3733-642
    - D_M_F_3733-642_4f68b42b-9d99-41c0-ba7b-ee8caa2acee7 -> d_m_f_3733-642
    - D_M_F_3733-642 -> d_m_f_3733-642 (unchanged)
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

def transform_group_to_product(group):
    first = group[0]
    product_name = first.get("Product_Name", "").strip()
    if not product_name:
        return None
    handle = create_handle(product_name)
    body_html = first.get("Description", "").strip()
    vendor = "Deerhunter"
    tags = []
    gender = first.get("Gender", "").strip().lower()
    if gender == "manlig":
        tags.append("Herr")
    elif gender == "kvinlig":
        tags.append("Dam")
    elif gender:
        tags.append(gender.capitalize())
    composition = first.get("Composition", "").strip()
    if composition:
        tags.append(composition)
    series = first.get("Series", "").strip()
    if series:
        tags.append(series)
    name_lower = product_name.lower()
    if "byxa" in name_lower or "byxor" in name_lower:
        tags.append("Byxor")
    if "t-shirt" in name_lower:
        tags.append("T-shirts")
    if "jacka" in name_lower:
        tags.append("Jackor")
    if "mössa" in name_lower or "keps" in name_lower or "cap" in name_lower or "hatt" in name_lower:
        tags.append("Mössor och Kepsar")
    if "piké" in name_lower:
        tags.append("Pike")
    if "bag" in name_lower:
        tags.append("Väskor")
    if "strumpor" in name_lower or "sockor" in name_lower or "socks" in name_lower:
        tags.append("Strumpor")
    if "handske" in name_lower or "vante" in name_lower or "vantar" in name_lower or "glove" in name_lower or "handskar" in name_lower:
        tags.append("Handskar")
    if "shorts" in name_lower:
        tags.append("Shorts")
    if "skjorta" in name_lower:
        tags.append("Skjortor")
    if "tröja" in name_lower or "sweater" in name_lower or "cardigan" in name_lower or "fleece" in name_lower:
        tags.append("Tröjor")
    if "väst" in name_lower:
        tags.append("Västar")
    if "bälte" in name_lower:
        tags.append("Bälten")
    if "skärp" in name_lower:
        tags.append("Skärp")
    if "leggings" in name_lower or "under" in name_lower:
        tags.append("Baslager")

    variants = []
    image_urls = set()
    variant_image_map = {}

    for row in group:
        sku = f"{row.get('Product_Number','').strip()}-{row.get('Colour_Number','').strip()}-{row.get('Size','').strip()}"
        original_price = float((row.get("Retail_Price", "") or "0").replace(",", "."))
        wholesale_price = float((row.get("Wholesale_Price", "") or "0").replace(",", "."))
        is_outlet = row.get("Outlet", "").strip().lower() == "yes"

        if is_outlet:
            # Dynamic pricing based on wholesale/retail ratio
            if wholesale_price > 0 and original_price > 0:
                cost_ratio = wholesale_price / original_price

                # Determine profit multiplier based on margin
                if cost_ratio < 0.20:  # <20% cost (very high margin)
                    target_multiplier = 2.5  # 150% profit margin
                elif cost_ratio < 0.30:  # <30% cost (high margin)
                    target_multiplier = 2.2  # 120% profit margin
                elif cost_ratio < 0.40:  # <40% cost (medium margin)
                    target_multiplier = 2.0  # 100% profit margin
                else:  # >=40% cost (low margin)
                    target_multiplier = 1.8  # 80% profit margin

                # Calculate outlet price
                outlet_price = wholesale_price * target_multiplier

                # Never more than 30% discount (70% of retail)
                max_price = original_price * 0.70
                discounted_price = min(outlet_price, max_price)

                # Round to 2 decimals
                discounted_price = round(discounted_price, 2)
            else:
                # Fallback to 30% discount if data missing
                discounted_price = round(original_price * 0.7, 2)

            price_str = f"{discounted_price:.2f}"
            compare_str = f"{original_price:.2f}"
        else:
            # Non-outlet: keep original price
            price_str = f"{original_price:.2f}"
            compare_str = None

        barcode = row.get("EAN", "").strip()
        stock_val = row.get("Stock", "").strip().lower()
        if stock_val == "nostock":
            inventory_quantity = 0
        elif stock_val == "instock":
            inventory_quantity = 50
        elif stock_val == "lowstock":
            inventory_quantity = 10
        else:
            try:
                inventory_quantity = int(float(stock_val))
            except:
                inventory_quantity = 0

        color = row.get("Colour_Name", "").strip()
        size = row.get("Size", "").strip()

        variant = {
            "sku": sku,
            "option1": color,
            "option2": size,
            "price": price_str,
            "barcode": barcode,
            "inventory_quantity": inventory_quantity,
            "inventory_management": "shopify",
            "inventory_policy": "deny"
        }
        if compare_str:
            variant["compare_at_price"] = compare_str

        variants.append(variant)

        image_fields = ["Image_URL", "Image1", "Image2", "Image3", "Image4", "Image5", "Image6", "Image7"]
        for field in image_fields:
            url = row.get(field, "").strip()
            if url and url not in image_urls and not is_image_imported(url):
                if is_valid_shopify_image(url):
                    image_urls.add(url)
                else:
                    print(f"🚫 Skippad bild pga storlek/problem: {url}")
        if color:
            lower_color = color.lower()
            if lower_color not in variant_image_map:
                for field in image_fields:
                    url = row.get(field, "").strip()
                    if url and url in image_urls:
                        variant_image_map[lower_color] = url
                        break

    # Skip products where all variants have price = 0
    all_prices_zero = all(float(v.get("price", "0")) <= 0 for v in variants)
    if all_prices_zero:
        print(f"⚠️ Skippade produkt (alla varianter har pris = 0 kr): {product_name}")
        return None

    colors = list({v["option1"] for v in variants if v["option1"]})
    sizes = list({v["option2"] for v in variants if v["option2"]})
    options = [
        {"name": "Color", "values": colors},
        {"name": "Size",  "values": sizes}
    ]

    primary_tag = f"handle:{handle}"
    all_tags = [primary_tag] + tags
    all_image_urls = list(image_urls)

    # Förbered första bilden (kan vara resizad)
    images = []
    if all_image_urls:
        first_image = prepare_image_for_shopify(all_image_urls[0])
        if first_image:
            images.append(first_image)

    product_payload = {
        "title": product_name,
        "handle": handle,
        "body_html": body_html,
        "vendor": vendor,
        "tags": ", ".join(all_tags),
        "options": options,
        "variants": variants,
        "images": images,
        "variant_image_map": variant_image_map,
        "all_image_urls": all_image_urls,
        "published_scope": "global"
    }
    return product_payload

def find_product_by_handle(product_title):
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }
    handle = create_handle(product_title)
    search_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products.json?handle={handle}"
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
    product_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products/{product_id}.json"
    current_resp = requests.get(product_url, headers=headers)
    if current_resp.status_code != 200:
        print(f"❌ Failed to fetch current product {product_id}: {current_resp.text}")
        return None
    current_product = current_resp.json().get("product", {})
    current_variants = current_product.get("variants", [])
    current_map = {v.get("sku", "").lower(): v for v in current_variants}
    for new_var in product_data.get("variants", []):
        new_sku = new_var.get("sku", "").lower()
        if new_sku in current_map:
            current_map[new_sku]["price"] = new_var["price"]
            if "compare_at_price" in new_var and new_var["compare_at_price"]:
                current_map[new_sku]["compare_at_price"] = new_var["compare_at_price"]
            else:
                current_map[new_sku].pop("compare_at_price", None)
            # NOTE: inventory_quantity cannot be updated via Products API
            # It will be updated separately via update_inventory_levels()
            current_map[new_sku]["barcode"] = new_var.get("barcode", current_map[new_sku].get("barcode", ""))
            current_map[new_sku]["inventory_management"] = "shopify"
            current_map[new_sku]["inventory_policy"] = "deny"
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
                base == get_base_without_hash(os.path.splitext(os.path.basename(existing.get("src", "") or ""))[0])
                for existing in current_images
            )
            if not duplicate_found:
                current_images.append({"src": src})
        elif image.get("attachment"):
            # Resizad bild som attachment
            filename = image.get("filename", "image.jpg")
            base = get_base_without_hash(os.path.splitext(filename)[0])
            duplicate_found = any(
                base == get_base_without_hash(os.path.splitext(os.path.basename(existing.get("src", "") or ""))[0])
                for existing in current_images
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
        print(f"❌ Failed to update product: {product_data['title']} (ID: {product_id})")
        print(update_resp.text)
        return None

def assign_variant_images(product_id, variant_image_map):
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }
    product_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products/{product_id}.json"
    product_resp = requests.get(product_url, headers=headers)
    if product_resp.status_code != 200:
        print(f"❌ Failed to fetch product {product_id} for variant image assignment: {product_resp.text}")
        return
    product_data = product_resp.json().get("product", {})
    image_mapping = {}
    for img in product_data.get("images", []):
        src = img.get("src", "")
        base = get_base_without_hash(os.path.splitext(os.path.basename(src))[0])
        image_mapping[base] = img.get("id")
    updated_variants = []
    for variant in product_data.get("variants", []):
        sku = variant.get("sku", "").lower()
        color_in_variant = variant.get("option1", "").lower()
        assigned_image_url = variant_image_map.get(sku) or variant_image_map.get(color_in_variant)
        if assigned_image_url:
            feed_identifier = get_base_without_hash(
                os.path.splitext(os.path.basename(urllib.parse.urlparse(assigned_image_url).path))[0]
            )
            found_image_id = image_mapping.get(feed_identifier)
            if found_image_id:
                variant["image_id"] = found_image_id
        updated_variants.append(variant)
    update_payload = {"product": {"id": product_id, "variants": updated_variants}}
    update_resp = requests.put(product_url, json=update_payload, headers=headers)
    if update_resp.status_code != 200:
        print(f"❌ Failed to update variant image assignments for product {product_id}: {update_resp.text}")

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
    error_text = ""
    if existing_id:
        prod_id = update_product(existing_id, product_data)
        if not prod_id:
            error_text = "Kunde inte uppdatera produkt (se logg ovan)"
    else:
        response = requests.post(SHOPIFY_API_ENDPOINT, json={"product": product_data}, headers=headers)
        if response.status_code == 201:
            prod_id = response.json()["product"]["id"]
            print(f"✅ Successfully added product: {product_data['title']} (ID: {prod_id})")
        else:
            print(f"❌ Failed to add product: {product_data['title']}")
            print(f"Error: {response.text}")
            prod_id = None
            error_text = response.text
    if prod_id:
        # Märk bilder som importerade (använd original-URLer)
        for url in product_data.get("all_image_urls", []):
            mark_image_imported(url)

        update_inventory_levels(prod_id, product_data)
        if "variant_image_map" in product_data:
            assign_variant_images(prod_id, product_data["variant_image_map"])
        # Ensure product is published globally (visible on all sales channels)
        ensure_global_publication(prod_id)
    return prod_id, error_text

def upload_additional_images(product_id, image_urls):
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }
    images_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products/{product_id}/images.json"
    resp = requests.get(images_url, headers=headers)
    uploaded_basenames = set()
    if resp.status_code == 200:
        for img in resp.json().get("images", []):
            filename = os.path.splitext(os.path.basename(img.get("src", "")))[0]
            base = get_base_without_hash(filename)
            uploaded_basenames.add(base)
    for url in image_urls[1:]:
        filename = os.path.splitext(os.path.basename(urllib.parse.urlparse(url).path))[0]
        base = get_base_without_hash(filename)
        if base in uploaded_basenames:
            print(f"⏩ Skippade bild (fanns redan basnamn): {url}")
            continue

        # Förbered bilden (kan bli resizad)
        image_data = prepare_image_for_shopify(url)
        if not image_data:
            print(f"❌ Kunde inte förbereda bild: {url}")
            continue

        payload = {"image": image_data}
        resp = requests.post(
            f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products/{product_id}/images.json",
            json=payload,
            headers=headers,
        )
        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {}
        if resp.status_code == 201:
            print(f"✅ Bild uppladdad: {url}")
            uploaded_basenames.add(base)
        elif resp.status_code == 200 and "image" in resp_json:
            print(f"🔄 Bild redan uppladdad (eller exakt samma url): {url}")
        else:
            print(f"❌ Misslyckades med bild: {url} – Fel: {resp_json or resp.text}")
        time.sleep(1.0)

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

# Deprecated - use save_all_caches() instead
def save_image_cache():
    save_all_caches()

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

def get_all_deerhunter_products_from_shopify():
    """
    Fetch all Deerhunter products from Shopify.
    Returns a dict with handle as key and product data as value.
    """
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }

    all_products = {}
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products.json?vendor=Deerhunter&limit=250"

    while url:
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            print(f"❌ Failed to fetch Deerhunter products from Shopify: {resp.status_code}")
            print(resp.text)
            break

        data = resp.json()
        products = data.get("products", [])

        for product in products:
            handle = product.get("handle")
            if handle:
                all_products[handle] = {
                    "id": product.get("id"),
                    "title": product.get("title"),
                    "status": product.get("status")
                }

        # Check for pagination (Link header)
        link_header = resp.headers.get("Link", "")
        url = None
        if "rel=\"next\"" in link_header:
            # Extract next URL from Link header
            parts = link_header.split(",")
            for part in parts:
                if "rel=\"next\"" in part:
                    url = part.split(";")[0].strip("<> ")
                    break

        time.sleep(0.5)  # Rate limiting

    return all_products

def archive_products_not_in_feed(feed_handles, min_feed_size=400):
    """
    Archive (set to draft) Deerhunter products that exist on Shopify but not in CSV feed.

    Safety checks:
    - Only runs if feed has minimum number of products (default 400)
    - Logs all archived products
    - Does not delete, only sets status to "draft"
    """
    if len(feed_handles) < min_feed_size:
        print(f"\n⚠️ SAFETY CHECK: Feed only has {len(feed_handles)} products (minimum {min_feed_size} required)")
        print("   Skipping auto-cleanup to prevent accidental archiving due to feed issues")
        return

    print(f"\n🔍 Checking for products to archive...")
    print(f"   Feed has {len(feed_handles)} products")

    shopify_products = get_all_deerhunter_products_from_shopify()
    print(f"   Shopify has {len(shopify_products)} Deerhunter products")

    # Normalize both sets of handles for comparison (Shopify may normalize Swedish characters)
    # Create normalized version of feed_handles
    feed_handles_normalized = {h.replace("å", "a").replace("ä", "a").replace("ö", "o") for h in feed_handles}

    # Find products on Shopify but not in feed
    to_archive = []
    for handle, product_data in shopify_products.items():
        # Normalize Shopify handle for comparison
        handle_normalized = handle.replace("å", "a").replace("ä", "a").replace("ö", "o")
        if handle_normalized not in feed_handles_normalized and product_data["status"] == "active":
            to_archive.append((handle, product_data))

    if not to_archive:
        print("   ✅ No products need archiving - all Shopify products are in feed")
        return

    print(f"\n📦 Found {len(to_archive)} products to archive:")

    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_API_KEY,
    }

    archived_count = 0
    for handle, product_data in to_archive:
        product_id = product_data["id"]
        title = product_data["title"]

        print(f"   📥 Archiving: {title} (handle: {handle})")

        url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products/{product_id}.json"
        payload = {
            "product": {
                "id": product_id,
                "status": "draft"
            }
        }

        resp = requests.put(url, json=payload, headers=headers)
        if resp.status_code == 200:
            print(f"      ✅ Archived successfully")
            archived_count += 1
        else:
            print(f"      ❌ Failed to archive: {resp.status_code}")
            if resp.text:
                print(f"         Error: {resp.text}")

        time.sleep(0.6)  # Rate limiting

    print(f"\n✅ Auto-cleanup complete: {archived_count}/{len(to_archive)} products archived")

def main():
    start_time = datetime.now()
    print(f"🕐 Script started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    progress_file = "progress.txt"
    rows = read_csv_data_from_ftp()
    print(f"Found {len(rows)} rows in CSV.")
    groups = group_products(rows)
    print(f"Grouped into {len(groups)} products.")
    imported_ids = []
    failed_imports = []

    # Collect ALL handles from feed for cleanup (even if skipping/resuming)
    # Extract handles directly without building full payload to avoid image processing
    feed_handles = set()
    print("🔍 Collecting product handles from feed for cleanup...")
    for product_number, group in groups.items():
        if group:
            first_row = group[0]
            product_name = first_row.get("Product_Name", "").strip()
            if product_name:
                handle = create_handle(product_name)
                feed_handles.add(handle)
    print(f"   Found {len(feed_handles)} unique products in feed")

    last_completed_product = None
    if os.path.exists(progress_file):
        with open(progress_file, "r") as f:
            last_completed_product = f.read().strip()
        print(f"⏩ Återupptar från produktnummer efter: {last_completed_product}")

    skipping = bool(last_completed_product)
    total_products = len(groups)
    processed_count = 0
    for product_number, group in groups.items():
        processed_count += 1

        if skipping:
            if str(product_number) == str(last_completed_product):
                skipping = False
            continue

        print(f"\n📦 Processing product {processed_count}/{total_products} (#{product_number})...")
        product_payload = transform_group_to_product(group)
        if product_payload:
            prod_id, error_msg = send_to_shopify(product_payload)
            if prod_id:
                all_image_urls = product_payload.get("all_image_urls", [])
                if len(all_image_urls) > 1:
                    upload_additional_images(prod_id, all_image_urls)
                    assign_variant_images(prod_id, product_payload["variant_image_map"])
                imported_ids.append(prod_id)
                with open(progress_file, "w") as f:
                    f.write(str(product_number))
                save_image_cache()
            else:
                if error_msg:
                    failed_imports.append((product_payload['title'], error_msg))
                else:
                    failed_imports.append((product_payload['title'], "API error eller ogiltigt svar"))
            time.sleep(1.0)
        else:
            failed_imports.append((f"(Ingen payload, product_number: {product_number})", "Payload byggdes ej"))

    print(f"Imported {len(imported_ids)} products successfully.")
    save_image_cache()

    if failed_imports:
        print("\n🚫 Följande produkter kunde INTE importeras:")
        for name, reason in failed_imports:
            print(f" - {name} | Orsak: {reason}")
    else:
        print("\n✅ Alla produkter importerades utan fel.")
    
    # Remove progress file since we've completed processing all products
    # (regardless of whether some imports failed)
    if os.path.exists(progress_file):
        os.remove(progress_file)
        print(f"\n🧹 Progress-fil '{progress_file}' raderad (import slutförd).")

    # Auto-cleanup: Archive products not in CSV feed anymore
    archive_products_not_in_feed(feed_handles)

    # Calculate and display execution time
    end_time = datetime.now()
    duration = end_time - start_time
    hours, remainder = divmod(duration.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    print(f"\n⏱️ Script completed at: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📊 Total execution time: {int(hours)}h {int(minutes)}m {int(seconds)}s")
    print(f"📦 Products processed: {len(groups)}")
    print(f"✅ Products imported: {len(imported_ids)}")
    if failed_imports:
        print(f"❌ Failed imports: {len(failed_imports)}")

if __name__ == "__main__":
    main()
