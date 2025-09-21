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

# Bildvaliderings-cache
CACHE_FILE = "deerhunter_validation_cache.json"
MAX_SIZE_MB = 20
MAX_PIXELS = 5000

if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r") as f:
        image_validation_cache = json.load(f)
else:
    image_validation_cache = {}

def is_valid_shopify_image(url):
    if not url:
        return False
    if url in image_validation_cache:
        return image_validation_cache[url]["valid"]
    try:
        head = requests.head(url, timeout=10)
        if head.status_code != 200:
            print(f"⚠️ HEAD misslyckades: {url}")
            image_validation_cache[url] = {"valid": False}
            return False
        if 'Content-Length' in head.headers:
            size_bytes = int(head.headers['Content-Length'])
            if size_bytes > MAX_SIZE_MB * 1024 * 1024:
                print(f"⚠️ Bild för stor enligt headers: {url} ({size_bytes/1024/1024:.1f} MB)")
                image_validation_cache[url] = {"valid": False}
                return False
        # Om rimlig storlek, kontrollera dimensioner med GET
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            print(f"⚠️ Bild kunde inte hämtas: {url}")
            image_validation_cache[url] = {"valid": False}
            return False
        img = Image.open(BytesIO(resp.content))
        width, height = img.width, img.height
        if width > MAX_PIXELS or height > MAX_PIXELS:
            print(f"⚠️ Bild för stor i px: {url} ({width}x{height})")
            image_validation_cache[url] = {"valid": False, "width": width, "height": height}
            return False
        image_validation_cache[url] = {"valid": True, "width": width, "height": height}
        return True
    except Exception as e:
        print(f"⚠️ Bildproblem: {url} - {e}")
        image_validation_cache[url] = {"valid": False}
        return False

import atexit
@atexit.register
def save_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(image_validation_cache, f)

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
    parts = filename.rsplit("_", 1)
    if len(parts) == 2 and (len(parts[1]) >= 8 and (parts[1].replace("-", "").isalnum() or "-" in parts[1])):
        return parts[0]
    return filename

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
        is_outlet = row.get("Outlet", "").strip().lower() == "yes"
        if is_outlet:
            discounted_price = round(original_price * 0.7, 2)
            price_str = f"{discounted_price:.2f}"
            compare_str = f"{original_price:.2f}"
        else:
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
            if url and url not in image_urls:
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

    colors = list({v["option1"] for v in variants if v["option1"]})
    sizes = list({v["option2"] for v in variants if v["option2"]})
    options = [
        {"name": "Color", "values": colors},
        {"name": "Size",  "values": sizes}
    ]

    primary_tag = f"handle:{handle}"
    all_tags = [primary_tag] + tags
    all_image_urls = list(image_urls)
    images = [{"src": url} for url in all_image_urls[:1]]

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
            current_map[new_sku]["inventory_quantity"] = new_var["inventory_quantity"]
            current_map[new_sku]["barcode"] = new_var.get("barcode", current_map[new_sku].get("barcode", ""))
            current_map[new_sku]["inventory_management"] = "shopify"
            current_map[new_sku]["inventory_policy"] = "deny"
        else:
            current_variants.append(new_var)
    current_images = current_product.get("images", [])
    for image in product_data.get("images", []):
        src = image.get("src")
        if src:
            base = get_base_without_hash(os.path.splitext(os.path.basename(src))[0]).lower()
            duplicate_found = any(
                base == get_base_without_hash(os.path.splitext(os.path.basename(existing.get("src", "") or ""))[0]).lower()
                for existing in current_images
            )
            if not duplicate_found:
                current_images.append({"src": src})
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
        base = get_base_without_hash(os.path.splitext(os.path.basename(src))[0]).lower()
        image_mapping[base] = img.get("id")
    updated_variants = []
    for variant in product_data.get("variants", []):
        sku = variant.get("sku", "").lower()
        color_in_variant = variant.get("option1", "").lower()
        assigned_image_url = variant_image_map.get(sku) or variant_image_map.get(color_in_variant)
        if assigned_image_url:
            feed_identifier = get_base_without_hash(
                os.path.splitext(os.path.basename(urllib.parse.urlparse(assigned_image_url).path))[0]
            ).lower()
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
        print(f"❌ Failed to fetch locations: {loc_resp.text}")
        return
    locations = loc_resp.json().get("locations", [])
    if not locations:
        print("❌ No locations found!")
        return
    location_id = locations[0]["id"]
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
        update_inventory_levels(prod_id, product_data)
        if "variant_image_map" in product_data:
            assign_variant_images(prod_id, product_data["variant_image_map"])
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
            base = get_base_without_hash(filename).lower()
            uploaded_basenames.add(base)
    for url in image_urls[1:]:
        filename = os.path.splitext(os.path.basename(urllib.parse.urlparse(url).path))[0]
        base = get_base_without_hash(filename).lower()
        if base in uploaded_basenames:
            print(f"⏩ Skippade bild (fanns redan basnamn): {url}")
            continue
        payload = {"image": {"src": url}}
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

def save_image_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(image_validation_cache, f)
        
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

    last_completed_product = None
    if os.path.exists(progress_file):
        with open(progress_file, "r") as f:
            last_completed_product = f.read().strip()
        print(f"⏩ Återupptar från produktnummer efter: {last_completed_product}")

    skipping = bool(last_completed_product)
    for product_number, group in groups.items():
        if skipping:
            if str(product_number) == str(last_completed_product):
                skipping = False
            continue

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
    
    # Remove progress file if we've completed all products successfully
    # (no failed imports and we processed all groups)
    if not failed_imports and not skipping:
        if os.path.exists(progress_file):
            os.remove(progress_file)
            print(f"\n🧹 Progress-fil '{progress_file}' raderad (alla produkter importerades framgångsrikt).")
    
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
