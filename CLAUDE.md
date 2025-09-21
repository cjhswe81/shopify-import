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
   - **Image Deduplication**: Cache systems prevent re-uploading the same images
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
| Price Handling | Standard | Supports outlet pricing |
| Cache File | chevalier_image_imported.json | deerhunter_validation_cache.json |

### API Integration Points

1. **Products API**: `/admin/api/2023-10/products.json`
2. **Product Images API**: `/admin/api/2023-10/products/{id}/images.json`
3. **Inventory API**: `/admin/api/2023-10/inventory_levels/set.json`
4. **Smart Collections API**: `/admin/api/2023-10/smart_collections.json`

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

### Common Development Tasks

1. **Adding New Data Source**: Copy existing script structure, modify parsing logic
2. **Debugging Failed Imports**: Check cache files and progress.txt for state
3. **Testing API Calls**: Comment out `requests.post/put` calls and print payloads
4. **Handling New Product Types**: Update category detection logic in scripts

### Error Handling Considerations

- Always check for 429 (rate limit) errors from Shopify API
- Validate required fields before sending to API
- Use cache files to avoid duplicate processing
- Implement progress tracking for long-running imports

## Automation with GitHub Actions

### Script Execution Times
Based on testing (2025-09-09):
- **Chevalier**: ~38 minutes (275 products)
- **Deerhunter**: ~1h 58m (508 products)
- **Total Runtime**: ~2h 36m

### GitHub Actions Workflow
Since the combined runtime is under 3 hours, GitHub Actions is perfect for daily automation (6-hour timeout for public repos).

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
    timeout-minutes: 240  # 4 hour timeout as safety
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: pip install requests python-dotenv pillow
      
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
```

### Setup Instructions
1. Create the workflow file in your repository
2. Go to GitHub repository Settings → Secrets and variables → Actions
3. Add all required secrets (SHOPIFY_API_KEY, SHOPIFY_STORE_URL, FTP_HOST, etc.)
4. The workflow will run automatically at 2 AM UTC daily, or can be triggered manually from Actions tab