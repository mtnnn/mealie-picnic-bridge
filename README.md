# Mealie Picnic Bridge

Automatically syncs your [Mealie](https://mealie.io/) shopping lists to your [Picnic](https://picnic.app/) cart.

## How it works

1. Fetches all shopping lists from Mealie
2. Searches Picnic for matching products per ingredient
3. Matches ingredients to products using fuzzy matching or LLM-based matching (Claude)
4. Adds matched products to your Picnic cart
5. Caches product mappings in Mealie for faster future syncs

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
| `ANTHROPIC_API_KEY` | No | Required when LLM matching is enabled |
| `LLM_MODEL` | No | Claude model to use (default: claude-haiku-4-5-20251001) |

### 2. Run with Docker

```bash
docker compose up -d --build
```

The web UI is available at `http://localhost:8080`.

### 3. Authenticate with Picnic

If your Picnic account uses 2FA (most do), go to `http://localhost:8080/auth` to complete verification. The auth token is automatically saved to your `.env` file for future restarts.

## Usage

- Open `http://localhost:8080` and click **Sync naar Picnic**
- Check **Cache overslaan** to force fresh product searches (ignores cached mappings)
- Results show match status per item: matched, LLM matched, cached, no match, or error

## Matching strategies

### Fuzzy matching (default)

Uses token-based string similarity (`rapidfuzz`) to match ingredient names to Picnic product names. Fast but can miss semantic matches.

### LLM matching (optional)

Sends ingredient names with candidate products to Claude in a single batch request. Claude considers ingredient amounts, packaging sizes, and product categories to select the best match. Falls back to fuzzy matching on failure.

Enable with `LLM_MATCHING_ENABLED=true` and a valid `ANTHROPIC_API_KEY`.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Web UI with sync button |
| POST | `/sync` | Start sync (`?skip_cache=true` to bypass cache) |
| GET | `/status` | Last sync results |
| GET | `/auth` | 2FA authentication page |

## Tech stack

- Python 3.12 / FastAPI / uvicorn
- Direct Picnic API integration (no third-party wrapper)
- httpx for async HTTP
- rapidfuzz for fuzzy string matching
- Anthropic Claude API for LLM matching

## Acknowledgements

The Picnic API integration was built using endpoint documentation and auth flow from [mcp-picnic](https://github.com/ivo-toby/mcp-picnic) by Ivo Toby — an MCP server that lets AI assistants interact with the Picnic API.
