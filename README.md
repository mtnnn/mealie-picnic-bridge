# Mealie Picnic Bridge

Automatically syncs your [Mealie](https://mealie.io/) shopping lists to your [Picnic](https://picnic.app/) cart, with a comprehensive recipe audit system to ensure your recipes are sync-ready.

## How it works

### Shopping list sync

1. Fetches all shopping lists from Mealie
2. Searches Picnic for matching products per ingredient
3. Matches ingredients to products using fuzzy matching or LLM-based matching (Claude)
4. Adds matched products to your Picnic cart
5. Caches product mappings in Mealie for faster future syncs

### Recipe audit

The audit system scans all your recipes and identifies quality issues that affect sync accuracy:

- **Ingredients** -- Detects missing food links, quantities, and units. Links directly to Mealie's built-in ingredient parser for fixing.
- **Steps** -- Flags recipes with no instructions. Links to the recipe in Mealie for editing.
- **Language** -- Detects recipes not in your target language using LLM-based language detection. Batch-translates recipe names, descriptions, steps, and ingredient food names with per-recipe confirmation before applying.
- **Photos** -- Finds recipes missing photos. Generates candidates via DALL-E 3 and Brave image search.
- **Health score** -- Each recipe gets a 0-100 health score based on completeness across all categories.

## Setup

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

| Variable | Required | Description |
|---|---|---|
| `MEALIE_HOST` | Yes | Mealie instance URL |
| `MEALIE_TOKEN` | Yes | Mealie API token |
| `PICNIC_USERNAME` | Yes | Picnic account email |
| `PICNIC_PASSWORD` | Yes | Picnic account password |
| `PICNIC_AUTH_TOKEN` | No | Saved after 2FA, reused across restarts |
| `PICNIC_COUNTRY_CODE` | No | `NL` (default) or `DE` |
| `FUZZY_THRESHOLD` | No | Match score threshold 0-100 (default: 65) |
| `LLM_MATCHING_ENABLED` | No | Enable Claude-based matching (default: false) |
| `ANTHROPIC_API_KEY` | No | Required for LLM matching and recipe translation |
| `LLM_MODEL` | No | Claude model (default: claude-haiku-4-5-20251001) |
| `OPENAI_API_KEY` | No | Required for DALL-E photo generation |
| `BRAVE_API_KEY` | No | Required for Brave image search |
| `AUDIT_TARGET_LANGUAGE` | No | Target language ISO code (default: `nl`) |
| `AUDIT_PARSER` | No | Mealie ingredient parser: `nlp`, `brute`, or `openai` (default: `openai`) |
| `AUDIT_LLM_PROVIDER` | No | LLM provider for translation: `anthropic` or `openai` (default: `anthropic`) |

### 2. Run with Docker

```bash
docker compose up -d --build
```

The web UI is available at `http://localhost:8080`.

### 3. Authenticate with Picnic

If your Picnic account uses 2FA (most do), click the **Auth** button in the navbar to complete verification. The auth token is automatically saved to your `.env` file for future restarts.

## Usage

### Sync

- Open `http://localhost:8080` and click **Sync naar Picnic**
- Check **Cache overslaan** to force fresh product searches (ignores cached mappings)
- Results show match status per item: matched, LLM matched, cached, no match, or error

### Audit

- Open `http://localhost:8080/audit` and click **Scan All**
- The overview tab shows a health score dashboard with issue counts per category
- Click a stat card or use the tabs to navigate to specific issue categories
- **Ingredients/Steps**: Click a recipe card to open it in Mealie for editing
- **Language**: Click **Translate All** to start the batch translation wizard with per-recipe confirmation
- **Photos**: Click **Find Photo** on any recipe to generate or search for photos

## Matching strategies

### Fuzzy matching (default)

Uses token-based string similarity (`rapidfuzz`) to match ingredient names to Picnic product names. Fast but can miss semantic matches.

### LLM matching (optional)

Sends ingredient names with candidate products to Claude in a single batch request. Claude considers ingredient amounts, packaging sizes, and product categories to select the best match. Falls back to fuzzy matching on failure.

Enable with `LLM_MATCHING_ENABLED=true` and a valid `ANTHROPIC_API_KEY`.

## Tech stack

- Python 3.12 / FastAPI / uvicorn
- Direct Picnic API integration (no third-party wrapper)
- httpx for async HTTP
- rapidfuzz for fuzzy string matching
- Anthropic Claude API for LLM matching and recipe translation
- OpenAI API for DALL-E photo generation
- Jinja2 templates with vanilla JS frontend

## Acknowledgements

The Picnic API integration was built using endpoint documentation and auth flow from [mcp-picnic](https://github.com/ivo-toby/mcp-picnic) by Ivo Toby -- an MCP server that lets AI assistants interact with the Picnic API.
