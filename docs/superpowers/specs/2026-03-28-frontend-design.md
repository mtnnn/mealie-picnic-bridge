# Frontend Design Spec — Mealie Picnic Bridge

## Context

The app currently has a minimal inline HTML UI embedded in `main.py` — a single sync button with post-sync results. The user wants a polished frontend with Picnic branding, a split-panel live sync view, and a 2FA auth flow as a modal overlay.

## Decisions

| Decision | Choice |
|----------|--------|
| Stack | Embedded in FastAPI — Jinja2 templates served from FastAPI |
| JS approach | Vanilla JS (no framework, no build step) |
| Layout | Equal split panel: shopping list (left), Picnic cart (right) |
| Sync UX | Real-time SSE streaming — items move left→right as they process |
| Auth flow | Modal overlay with stepped 2-step card |
| Mobile | Stack panels vertically, auto-scroll to cart during sync |

## Brand & Design System

**Colors** (Picnic-inspired):
- Primary (Picnic red): `#E1171E`
- Primary dark: `#B81219`
- Primary light: `#FFF3F3`
- Success green: `#4CAF50` / light: `#E8F5E9`
- Warning orange: `#FF9800` / light: `#FFF3E0`
- Background: `#FAFAFA`
- Cart background: `#F8FFF5`
- Text primary: `#1A1A2E`
- Text secondary: `#666`
- Border: `#E8E8E8`

**Typography**: System font stack (`-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`)

**Icons**: Inline SVG (Lucide-style) — no icon library dependency

## Architecture

### File Structure

```
app/
├── main.py              # FastAPI routes (modified: replace inline HTML, add SSE endpoint)
├── templates/
│   ├── base.html        # Base template with navbar, CSS, shared JS
│   ├── index.html       # Main dashboard (extends base)
│   └── components/
│       ├── sync_bar.html      # Sync control bar partial
│       ├── shopping_panel.html # Left panel partial
│       ├── cart_panel.html     # Right panel partial
│       └── auth_modal.html    # 2FA modal partial
├── static/
│   ├── css/
│   │   └── style.css    # All styles
│   └── js/
│       └── app.js       # All client-side logic (SSE, DOM, animations)
├── models.py            # Add SyncEvent model for SSE
├── config.py            # Unchanged
├── mealie.py            # Unchanged
├── picnic_client.py     # Unchanged
├── matcher.py           # Unchanged
└── llm_matcher.py       # Unchanged
```

### New Backend: SSE Sync Endpoint

**`POST /sync/stream`** — Server-Sent Events endpoint that streams sync progress.

Each SSE event is a JSON object:

```
event: sync_start
data: {"total_items": 6, "lists": ["Weekly Groceries"]}

event: item_start
data: {"name": "Tomaten", "index": 0, "phase": "searching"}

event: item_result
data: {"name": "Tomaten", "index": 0, "status": "matched", "picnic_product_name": "Cherry Tomaten 250g", "picnic_product_id": "12345", "score": 95.2}

event: item_result
data: {"name": "Zelfgemaakte pesto", "index": 5, "status": "no_match"}

event: sync_complete
data: {"total_items": 6, "added_to_cart": 4, "no_match": 1, "errors": 1}
```

**Implementation approach**: Refactor the sync logic from the existing `sync()` endpoint into a generator function that yields `SyncEvent` objects. The existing `POST /sync` endpoint stays for API consumers. The new `POST /sync/stream` wraps the same generator with `StreamingResponse` using `text/event-stream` content type.

The existing 3-phase sync (collect → match → process) maps to SSE events as:
- Phase 1 (collect): Emits `item_start` with `phase: "searching"` for each item, then `item_result` for cached items and no-match items
- Phase 2 (match): Emits `item_start` with `phase: "matching"` for pending items entering batch/fuzzy matching
- Phase 3 (process): Emits `item_result` for each matched item after cart addition

### Pages & Components

#### 1. Navigation Bar
- Sticky top bar, Picnic red (`#E1171E`)
- Left: "mealie × picnic" brand text
- Right: Connection status dot (green=connected, orange=disconnected), Settings button
- Settings button triggers auth modal if 2FA needed, otherwise shows config info

#### 2. Sync Control Bar
- White card below navbar
- Left: Shopping list name + item count (fetched from Mealie on page load via `GET /status` or a new `GET /lists` endpoint)
- Right: "Skip cache" checkbox + "Sync to Picnic" button (red, rounded pill)
- Button shows spinner and disables during sync
- Needs a new endpoint `GET /shopping-lists` that returns list names and item counts

#### 3. Progress Bar
- Hidden by default, appears during sync
- Shows "Syncing items to Picnic..." with `X / Y items` counter
- Red gradient fill bar, updated via SSE events

#### 4. Split Panels

**Left Panel — Shopping List:**
- Header: clipboard icon + "Shopping List" + item count badge
- Red bottom border on header
- Items fetched on page load from Mealie
- Item states during sync:
  - `pending`: gray background, gray left border
  - `syncing`: light red background, red left border, pulse animation
  - `synced`: gray, strikethrough, 50% opacity
  - `no-match`: orange background, orange left border
- Each item shows: name, quantity/unit, and status badge

**Right Panel — Picnic Cart:**
- Header: cart icon + "Picnic Cart" + count badge
- Green bottom border on header
- Light green background (`#F8FFF5`)
- Items appear with `slideIn` animation (translateX from -20px, 0.4s ease)
- Each cart item shows: matched product name, category, match score/type
- Empty state: cart icon + "Items will appear here during sync"

#### 5. Auth Modal
- Triggered by: navbar button click, or auto-shown if `GET /auth/status` returns `needs_2fa: true`
- Backdrop: semi-transparent black with `backdrop-filter: blur(4px)`
- Card: white, rounded 20px, 380px wide, centered
- **Step 1**: Picnic logo, step indicator (1 active, 2 gray), "Request verification code" text, "Send SMS Code" button
- **Step 2**: Step indicator (1 green check, 2 active), 6 individual digit inputs with auto-advance, "Verify & Connect" button
- Success state: green checkmark, "Connected!" message, auto-close after 2s
- Error state: red text below button, shake animation
- Needs new endpoint: `GET /auth/status` returning `{"needs_2fa": bool, "authenticated": bool}`

#### 6. Mobile Layout (< 768px)
- Panels stack vertically (CSS grid single column)
- Sync bar stacks: list info on top, button full-width below
- During sync: auto-scroll to cart panel when new item arrives
- Navbar: smaller font, hide "Settings" text (icon only)

## New API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/auth/status` | Returns `{needs_2fa, authenticated}` |
| `GET` | `/shopping-lists` | Returns `[{name, id, item_count}]` for all Mealie shopping lists |
| `POST` | `/sync/stream` | SSE endpoint streaming sync progress events |

Existing endpoints (`GET /`, `POST /sync`, `GET /status`, auth endpoints) remain unchanged for backward compatibility.

## Data Flow

```
Page Load:
  1. GET /auth/status → if needs_2fa, show auth modal
  2. GET /shopping-lists → populate sync bar with list name + count
  3. GET /status → if last_sync exists, populate panels with previous results

Sync Flow (SSE):
  1. User clicks "Sync to Picnic"
  2. JS opens EventSource to POST /sync/stream (via fetch + ReadableStream since EventSource only supports GET — use fetch with streaming body reader)
  3. sync_start → show progress bar, populate left panel with all items as "pending"
  4. item_start → update item in left panel to "syncing" state
  5. item_result →
     - Update left panel item to final state (synced/no-match/error)
     - If matched: add item to right panel with slideIn animation
     - Update progress bar
  6. sync_complete → update progress bar to 100%, re-enable sync button

Auth Flow:
  1. User clicks navbar button (or auto-triggered)
  2. Show modal at Step 1
  3. Click "Send SMS Code" → POST /auth/request-code
  4. On success → advance to Step 2
  5. User types 6-digit code (auto-advance between inputs)
  6. Click "Verify" → POST /auth/verify
  7. On success → show green check, close modal after 2s, update status dot
```

## SSE Implementation Note

Since `EventSource` only supports GET, and our sync needs POST (with `skip_cache` param), we'll use the Fetch API with `ReadableStream` to consume the SSE:

```js
const response = await fetch('/sync/stream?skip_cache=' + skipCache, { method: 'POST' });
const reader = response.body.getReader();
const decoder = new TextDecoder();
// Parse SSE format from chunks
```

This avoids needing a separate library while supporting POST.

## Verification

1. **Page load**: Navigate to `/` — navbar renders, sync bar shows shopping list info, previous sync results populate if available
2. **Auth flow**: If 2FA needed, modal auto-appears. Complete SMS flow → modal closes, status dot turns green
3. **Sync**: Click "Sync to Picnic" → progress bar appears, left panel items transition through states, right panel items slide in one-by-one in real-time, progress bar fills
4. **Mobile**: Resize to <768px → panels stack, sync bar stacks, auto-scroll works during sync
5. **Error states**: Disconnect network → status dot turns orange. Sync with failing items → orange "no match" badges, red "error" badges
6. **Backward compat**: `POST /sync` still works for API consumers, `GET /status` still returns last sync
