# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based Shopify product import automation system that synchronizes product data from external suppliers (Chevalier and Deerhunter) into Shopify stores.

## Key Commands

### Setup and Run
```bash
# Create virtual environment (if not exists)
python -m venv venv

# Activate virtual environment
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Install dependencies (no requirements.txt exists, install manually)
pip install requests python-dotenv pillow black

# Run Chevalier import (from XML)
python shopify_import_chevalier.py

# Run Deerhunter import (from FTP CSV)
python shopify_import_deerhunter.py
```

### Code Formatting
```bash
# Format code with Black
black shopify_import_chevalier.py
black shopify_import_deerhunter.py
```

### Testing Individual Functions
```bash
# Run Python interactive shell for testing
python -i shopify_import_chevalier.py
# Then test functions like: create_handle("Test Product")
```

## Architecture Overview

### Core Components

1. **Import Scripts**: Two independent scripts for different suppliers
   - `shopify_import_chevalier.py`: Imports from XML feed
   - `shopify_import_deerhunter.py`: Imports from CSV via FTP

2. **Data Flow**:
   ```
   External Source (XML/CSV) → Parse & Group → Validate → Transform → Shopify API
                                                 ↓
                                          Cache/Progress
   ```

3. **Key Patterns**:
   - **Product Grouping**: Products are grouped by unique identifier before sending to Shopify
   - **Variant Management**: Each product can have multiple variants (color/size combinations)
   - **Image Deduplication**: Advanced `get_base_without_hash()` removes Shopify UUID suffixes to prevent duplicates
   - **Inventory Sync**: Uses Inventory Levels API to update stock quantities (requires `read_locations` scope)
   - **Global Publication**: All products automatically published to all sales channels (Google, Facebook, etc.)
   - **Error Recovery**: Progress tracking allows resuming failed imports

### Shared Architecture Patterns

Both scripts follow similar patterns but with data source-specific implementations:

1. **Configuration**: Environment variables loaded from `.env`
2. **API Integration**: Direct REST API calls to Shopify Admin API
3. **Rate Limiting**: Sleep delays between API calls (0.6-1.0 seconds)
4. **Error Handling**: Graceful failures with detailed error messages
5. **State Management**: JSON cache files for tracking processed items

### Key Differences

| Feature | Chevalier | Deerhunter |
|---------|-----------|------------|
| Data Source | XML from URL | CSV from FTP |
| Image Validation | No | Yes (size/dimensions) |
| Progress Resumption | No | Yes (progress.txt) |
| Price Handling | Standard | Dynamic outlet pricing |
| Cache File | chevalier_image_imported.json | deerhunter_validation_cache.json |

### Deerhunter Dynamic Outlet Pricing (Updated 2025-09-27)

The Deerhunter script implements dynamic outlet pricing based on wholesale/retail margins:

**Pricing Logic for Outlet Products:**
- Calculates cost ratio: `wholesale_price / retail_price`
- Applies dynamic multipliers based on margin:
  - **<20% cost ratio** (very high margin): 2.5x wholesale (150% profit)
  - **<30% cost ratio** (high margin): 2.2x wholesale (120% profit)
  - **<40% cost ratio** (medium margin): 2.0x wholesale (100% profit)
  - **≥40% cost ratio** (low margin): 1.8x wholesale (80% profit)
- Maximum discount capped at 30% (never below 70% of retail price)
- Non-outlet products remain at full retail price

**Example Price Calculations:**
- Product with 13% cost ratio (e.g., Sneaky 3D): 151.8 SEK wholesale → 380 SEK outlet price (67% discount!)
- Product with 29% cost ratio: 619 SEK wholesale → 1362 SEK outlet price (37% discount)
- Product with 45% cost ratio: 784 SEK wholesale → 1225 SEK outlet price (30% max discount)

This strategy optimizes for competitive Google Shopping placement while maintaining profitability.

### API Integration Points

1. **Products API**: `/admin/api/2023-04/products.json`
   - Create and update products
   - Set `published_scope: "global"` for visibility on all sales channels
2. **Product Images API**: `/admin/api/2023-04/products/{id}/images.json`
   - Upload product images
   - Image deduplication via `get_base_without_hash()` function
3. **Inventory Levels API**: `/admin/api/2023-04/inventory_levels/set.json`
   - Update inventory quantities (requires `read_locations` scope)
   - Called via `update_inventory_levels()` function
4. **Smart Collections API**: `/admin/api/2023-04/smart_collections.json`
5. **Locations API**: `/admin/api/2023-04/locations.json`
   - Fetch store location for inventory updates

### Environment Configuration

Required `.env` variables:
```
SHOPIFY_STORE_URL=your-store.myshopify.com
SHOPIFY_API_KEY=your-api-access-token

# For Deerhunter only:
FTP_HOST=ftp.example.com
FTP_USERNAME=username
FTP_PASSWORD=password
FTP_FILE_PATH=/path/to/csv/file.csv
```

### Required API Permissions (Scopes)

The Shopify API key must have the following scopes enabled:

**Essential Scopes:**
- ✅ `read_products` - Read product data
- ✅ `write_products` - Create and update products
- ✅ `write_inventory` - Update inventory levels
- ✅ `read_locations` - Required for inventory updates (fetch store location)

**Optional but Recommended:**
- ✅ `read_publications` - Read sales channel information
- ✅ `write_publications` - Publish products to sales channels (not currently used)

**How to add scopes:**
1. Go to Shopify Admin → Settings → Apps and sales channels → Develop apps
2. Click on your app (or create one)
3. Under "Admin API access scopes", enable the scopes listed above
4. Save and reinstall app to apply changes
5. Copy the new API key to `.env` file

### Key Features and Fixes (Updated 2025-10-23)

#### 1. Inventory Sync (Fixed)
**Problem:** Inventory levels were not syncing from supplier feeds to Shopify.

**Root Cause:**
- API key lacked `read_locations` scope
- Script attempted to update inventory via Products API (doesn't work for existing products)

**Solution:**
- Added `read_locations` scope to API key
- Use Inventory Levels API (`/admin/api/2023-04/inventory_levels/set.json`)
- Implemented `update_inventory_levels()` function with proper error logging
- Removed incorrect `inventory_quantity` update in `update_product()` function

**Location:** `shopify_import_deerhunter.py:426-510`, `shopify_import_chevalier.py:360-453`

#### 2. Image Deduplication (Fixed)
**Problem:** Some products had 250+ duplicate images (Shopify's max limit).

**Root Cause:**
- Shopify adds UUID suffixes to uploaded images (e.g., `image_4f68b42b-9d99-41c0.jpg`)
- Original `get_base_without_hash()` couldn't handle multiple suffixes or numeric variants
- Example: `D_M_F_3733-642_1_e450759a-fd73-4409.jpg` was treated as different from `D_M_F_3733-642.jpg`

**Solution:**
- Improved `get_base_without_hash()` function to:
  - Remove Shopify UUID suffixes (36 chars with 4+ dashes)
  - Remove long alphanumeric hashes (≥16 chars)
  - Remove numeric suffixes (_1, _2, etc.) from suppliers
  - Preserve product codes with dashes (e.g., 3733-642)
- Loop removes multiple suffix layers until base filename is found

**Example:**
```python
# Before fix:
"D_M_F_3733-642_1_e450759a-fd73-4409-a7f2" → "d_m_f" (incorrect!)

# After fix:
"D_M_F_3733-642_1_e450759a-fd73-4409-a7f2" → "d_m_f_3733-642" (correct!)
"D_M_F_3733-642_4f68b42b-9d99-41c0"       → "d_m_f_3733-642" (correct!)
"D_M_F_3733-642"                           → "d_m_f_3733-642" (correct!)
```

**Location:** `shopify_import_deerhunter.py:145-182`, `shopify_import_chevalier.py:46-83`

#### 3. Automatic Sales Channel Publication (New Feature)
**Feature:** Automatically publish products to all sales channels (Google & YouTube, Facebook & Instagram, etc.)

**Implementation:**
- New `ensure_global_publication()` function
- Sets `published_scope: "global"` on all products
- Makes products visible on:
  - Online Store
  - Point of Sale
  - Google & YouTube (Google Shopping)
  - Facebook & Instagram
- Simple, reliable method (single API call)

**Benefits:**
- No manual work needed to publish products
- Products automatically appear in Google Shopping feed
- Facebook/Instagram catalog automatically updated
- Works for both new and existing products

**Location:** `shopify_import_deerhunter.py:597-624`, `shopify_import_chevalier.py:488-515`

#### 4. Enhanced Error Logging
**Improvements:**
- Clear warnings if `read_locations` scope is missing
- Logs location_id being used for inventory updates
- Better error messages for debugging API issues
- Critical errors clearly marked with ❌ and ⚠️ symbols

### Common Development Tasks

1. **Adding New Data Source**: Copy existing script structure, modify parsing logic
2. **Debugging Failed Imports**: Check cache files for state (progress.txt only exists during interrupted runs)
3. **Testing API Calls**: Comment out `requests.post/put` calls and print payloads
4. **Handling New Product Types**: Update category detection logic in scripts

### Error Handling Considerations

- **Rate Limiting**: Always check for 429 errors from Shopify API
- **Field Validation**: Validate required fields before sending to API
- **Cache Management**: Use cache files to avoid duplicate processing
- **Progress Tracking**: Implement resume capability for long-running imports
- **API Permissions**: Script logs clear warnings if required scopes are missing
  - Look for "CRITICAL" messages in logs
  - Check for `read_locations` scope if inventory not updating
- **Image Deduplication**: Monitor for products with excessive images (>50)
  - Use `get_base_without_hash()` to normalize filenames
  - Check logs for "⏩ Skippade bild" messages
- **Publication Status**: Verify `published_scope: "global"` is set on products
  - Check logs for "📢 Product published globally" messages

## Automation with GitHub Actions

### Script Execution Times
Based on testing (2025-09-09):
- **Chevalier**: ~38 minutes (275 products)
- **Deerhunter**: ~1h 58m (508 products)
- **Total Runtime**: ~2h 36m (locally), ~4h+ (GitHub Actions)

### GitHub Actions Workflow
GitHub Actions provides a 6-hour timeout for public repos. We use a 5h 50m timeout to ensure completion since GitHub Actions runners are slower than local machines.

### Caching and Resume Capability
The workflow implements persistent caching to handle interruptions and avoid re-processing:

#### Cache Files
- **`chevalier_image_imported.json`**: Tracks which Chevalier product images have been uploaded
- **`deerhunter_validation_cache.json`**: Caches validated Deerhunter images to skip re-validation
- **`progress.txt`**: Tracks last successfully imported Deerhunter product for resume capability (automatically deleted after complete runs)

#### How Caching Works
1. **GitHub Actions Cache**: Uses `actions/cache@v3` to persist files between workflow runs
2. **Resume on Failure**: If the workflow times out or fails:
   - Chevalier will skip already uploaded images (via JSON cache)
   - Deerhunter will resume from the last product in `progress.txt`
3. **Automatic Cleanup**: `progress.txt` is automatically deleted when import completes (regardless of individual product failures)
4. **Debug Artifacts**: On failure, all cache and log files are uploaded as artifacts for debugging

Create `.github/workflows/shopify-import.yml`:

```yaml
name: Daily Shopify Import
on:
  schedule:
    - cron: '0 2 * * *'  # Runs at 2 AM UTC daily
  workflow_dispatch:  # Allows manual trigger

jobs:
  shopify-import:
    runs-on: ubuntu-latest
    timeout-minutes: 350  # 5 hours 50 minutes timeout
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: pip install requests python-dotenv pillow

      # Restore cache files from previous runs
      - name: Restore cache files
        uses: actions/cache@v3
        with:
          path: |
            chevalier_image_imported.json
            deerhunter_validation_cache.json
            progress.txt
          key: shopify-cache-${{ github.run_number }}
          restore-keys: |
            shopify-cache-

      - name: Run Chevalier Import
        env:
          SHOPIFY_API_KEY: ${{ secrets.SHOPIFY_API_KEY }}
          SHOPIFY_STORE_URL: ${{ secrets.SHOPIFY_STORE_URL }}
        run: python shopify_import_chevalier.py
      
      - name: Run Deerhunter Import  
        env:
          SHOPIFY_API_KEY: ${{ secrets.SHOPIFY_API_KEY }}
          SHOPIFY_STORE_URL: ${{ secrets.SHOPIFY_STORE_URL }}
          FTP_HOST: ${{ secrets.FTP_HOST }}
          FTP_USERNAME: ${{ secrets.FTP_USERNAME }}
          FTP_PASSWORD: ${{ secrets.FTP_PASSWORD }}
          FTP_FILE_PATH: ${{ secrets.FTP_FILE_PATH }}
        run: python shopify_import_deerhunter.py

      # Upload debug files on failure for troubleshooting
      - name: Upload debug files on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: import-failure-debug-${{ github.run_number }}
          path: |
            *.log
            chevalier_image_imported.json
            deerhunter_validation_cache.json
            progress.txt
          retention-days: 7
          if-no-files-found: ignore
```

### Setup Instructions
1. Create the workflow file in your repository
2. Go to GitHub repository Settings → Secrets and variables → Actions
3. Add all required secrets (SHOPIFY_API_KEY, SHOPIFY_STORE_URL, FTP_HOST, etc.)
4. The workflow will run automatically at 2 AM UTC daily, or can be triggered manually from Actions tab