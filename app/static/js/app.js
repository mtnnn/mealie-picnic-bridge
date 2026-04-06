// === State ===
let isSyncing = false;
let totalItems = 0;
let processedItems = 0;
let cartCount = 0;

// === Page Load ===
document.addEventListener('DOMContentLoaded', async () => {
    await checkAuthStatus();
    await loadShoppingLists();
    await loadLastSync();
    setupCodeInputs();
});

// === Auth Status ===
async function checkAuthStatus() {
    try {
        const resp = await fetch('/auth/status');
        const data = await resp.json();
        const dot = document.getElementById('statusDot');
        const text = document.getElementById('statusText');
        const authBtn = document.getElementById('authBtn');
        if (data.needs_2fa) {
            dot.classList.add('needs-auth');
            text.textContent = '2FA Required';
            authBtn.style.display = '';
            openAuthModal();
        } else {
            dot.classList.remove('needs-auth');
            text.textContent = 'Connected';
            authBtn.style.display = 'none';
        }
    } catch {
        document.getElementById('statusDot').classList.add('needs-auth');
        document.getElementById('statusText').textContent = 'Disconnected';
        document.getElementById('authBtn').style.display = '';
    }
}

// === Shopping Lists ===
async function loadShoppingLists() {
    try {
        const resp = await fetch('/shopping-lists');
        const lists = await resp.json();
        const totalCount = lists.reduce((sum, l) => sum + l.item_count, 0);
        const names = lists.map(l => l.name).join(', ');

        document.getElementById('listName').textContent = names || 'No lists';
        document.getElementById('itemCount').textContent = totalCount + ' items';
        document.getElementById('shoppingCount').textContent = totalCount;

        // Populate shopping panel with items from all lists
        const shoppingList = document.getElementById('shoppingList');
        if (totalCount === 0) {
            shoppingList.innerHTML = '<div class="empty-state"><p>No items in shopping lists</p></div>';
            return;
        }

        // Fetch items for each list
        let allItems = [];
        for (const list of lists) {
            const itemResp = await fetch(`/shopping-lists/${list.id}/items`);
            const items = await itemResp.json();
            allItems = allItems.concat(items);
        }

        shoppingList.innerHTML = '';
        allItems.forEach((item, i) => {
            shoppingList.appendChild(createShoppingItem(item, i));
        });
    } catch {
        document.getElementById('listName').textContent = 'Error loading lists';
        document.getElementById('itemCount').textContent = '';
    }
}

function createShoppingItem(item, index) {
    const div = document.createElement('div');
    div.className = 'item pending';
    div.dataset.index = index;
    div.dataset.name = item.name;

    const info = document.createElement('div');
    const name = document.createElement('div');
    name.className = 'item-name';
    name.textContent = item.name;
    info.appendChild(name);

    if (item.quantity || item.unit) {
        const meta = document.createElement('div');
        meta.className = 'item-meta';
        const parts = [];
        if (item.quantity && item.quantity !== 1) parts.push(item.quantity);
        if (item.unit) parts.push(item.unit);
        meta.textContent = parts.join(' ');
        info.appendChild(meta);
    }

    div.appendChild(info);
    return div;
}

// === Last Sync Results ===
async function loadLastSync() {
    try {
        const resp = await fetch('/status');
        const data = await resp.json();
        if (data.last_sync) {
            renderSyncResults(data.last_sync);
        }
    } catch { /* ignore */ }
}

function renderSyncResults(result) {
    const shoppingList = document.getElementById('shoppingList');
    const cartList = document.getElementById('cartList');

    shoppingList.innerHTML = '';
    cartList.innerHTML = '';
    cartCount = 0;

    result.items.forEach((item, i) => {
        // Shopping panel item
        const shopDiv = document.createElement('div');
        const isSuccess = ['matched', 'llm_matched', 'cached'].includes(item.status);
        shopDiv.className = 'item ' + (isSuccess ? 'synced' : (item.status === 'no_match' ? 'no-match' : (item.status === 'error' ? 'error' : 'pending')));
        shopDiv.dataset.index = i;
        shopDiv.dataset.name = item.name;

        const info = document.createElement('div');
        const name = document.createElement('div');
        name.className = 'item-name';
        name.textContent = item.name;
        info.appendChild(name);
        shopDiv.appendChild(info);

        const badge = document.createElement('span');
        badge.className = 'status-badge ' + item.status;
        badge.textContent = item.status.replace('_', ' ');
        shopDiv.appendChild(badge);

        shoppingList.appendChild(shopDiv);

        // Cart panel item (only for successful matches)
        if (isSuccess && item.picnic_product_name) {
            addCartItem({ ...item, name: item.name });
        }
    });

    document.getElementById('shoppingCount').textContent = result.items.length;
    document.getElementById('cartCount').textContent = cartCount;
    updateCartEmpty();
}

// === Sync (SSE) ===
async function startSync() {
    if (isSyncing) return;
    isSyncing = true;
    processedItems = 0;
    cartCount = 0;

    const btn = document.getElementById('syncBtn');
    btn.disabled = true;
    btn.classList.add('syncing');
    btn.querySelector('.btn-label').textContent = 'Syncing...';

    // Show stop button
    const stopBtn = document.getElementById('stopBtn');
    if (stopBtn) stopBtn.classList.add('visible');

    const skipCache = document.getElementById('skipCache').checked;

    // Clear cart panel
    document.getElementById('cartList').innerHTML = '';
    document.getElementById('cartCount').textContent = '0';
    updateCartEmpty();

    // Show progress
    const progress = document.getElementById('progressContainer');
    progress.classList.add('visible');
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressCount').textContent = '0 / 0 items';

    try {
        const response = await fetch('/sync/stream?skip_cache=' + skipCache, { method: 'POST' });
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // Keep incomplete line in buffer

            let eventType = '';
            let eventData = '';

            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    eventType = line.slice(7).trim();
                } else if (line.startsWith('data: ')) {
                    eventData = line.slice(6).trim();
                } else if (line === '' && eventType && eventData) {
                    handleSyncEvent(eventType, JSON.parse(eventData));
                    eventType = '';
                    eventData = '';
                }
            }
        }
    } catch (err) {
        console.error('Sync error:', err);
    } finally {
        finishSync();
    }
}

function handleSyncEvent(type, data) {
    switch (type) {
        case 'sync_start':
            handleSyncStart(data);
            break;
        case 'item_start':
            handleItemStart(data);
            break;
        case 'item_result':
            handleItemResult(data);
            break;
        case 'sync_complete':
            handleSyncComplete(data);
            break;
        case 'sync_cancelled':
            document.getElementById('progressCount').textContent = 'Sync cancelled';
            break;
    }
}

function handleSyncStart(data) {
    totalItems = data.total_items;
    document.getElementById('progressCount').textContent = `0 / ${totalItems} items`;

    // Populate shopping list with all items as pending
    const shoppingList = document.getElementById('shoppingList');
    shoppingList.innerHTML = '';
    data.items.forEach((item, i) => {
        const div = document.createElement('div');
        div.className = 'item pending';
        div.dataset.index = i;
        div.dataset.name = item.name;

        const info = document.createElement('div');
        const name = document.createElement('div');
        name.className = 'item-name';
        name.textContent = item.name;
        info.appendChild(name);

        if (item.quantity || item.unit) {
            const meta = document.createElement('div');
            meta.className = 'item-meta';
            const parts = [];
            if (item.quantity && item.quantity !== 1) parts.push(item.quantity);
            if (item.unit) parts.push(item.unit);
            meta.textContent = parts.join(' ');
            info.appendChild(meta);
        }

        div.appendChild(info);
        shoppingList.appendChild(div);
    });

    document.getElementById('shoppingCount').textContent = totalItems;
}

function handleItemStart(data) {
    const item = findShoppingItem(data.index);
    if (!item) return;

    item.className = 'item ' + data.phase;

    // Remove old badge
    const oldBadge = item.querySelector('.status-badge');
    if (oldBadge) oldBadge.remove();

    const badge = document.createElement('span');
    badge.className = 'status-badge ' + data.phase;
    badge.textContent = data.phase === 'searching' ? 'searching...' : 'matching...';
    item.appendChild(badge);
}

function handleItemResult(data) {
    processedItems++;
    const isSuccess = ['matched', 'llm_matched', 'cached'].includes(data.status);

    // Update shopping list item
    const item = findShoppingItem(data.index);
    if (item) {
        item.className = 'item ' + (isSuccess ? 'synced' : (data.status === 'no_match' ? 'no-match' : 'error'));

        const oldBadge = item.querySelector('.status-badge');
        if (oldBadge) oldBadge.remove();

        const badge = document.createElement('span');
        badge.className = 'status-badge ' + data.status;
        badge.textContent = data.status.replace('_', ' ');
        item.appendChild(badge);
    }

    // Add to cart if successful
    if (isSuccess && data.picnic_product_name) {
        addCartItem(data);
    }

    // Update progress
    const pct = totalItems > 0 ? Math.round((processedItems / totalItems) * 100) : 0;
    document.getElementById('progressFill').style.width = pct + '%';
    document.getElementById('progressCount').textContent = `${processedItems} / ${totalItems} items`;
}

function handleSyncComplete(data) {
    document.getElementById('progressFill').style.width = '100%';
    document.getElementById('progressCount').textContent =
        `${data.added_to_cart} added, ${data.no_match} no match, ${data.errors} errors`;
}

async function stopSync() {
    try {
        await fetch('/sync/stop', { method: 'POST' });
    } catch { /* ignore */ }
}

function finishSync() {
    isSyncing = false;
    const btn = document.getElementById('syncBtn');
    btn.disabled = false;
    btn.classList.remove('syncing');
    btn.querySelector('.btn-label').textContent = 'Sync to Picnic';

    // Hide stop button
    const stopBtn = document.getElementById('stopBtn');
    if (stopBtn) stopBtn.classList.remove('visible');

    // Hide progress after a short delay
    setTimeout(() => {
        document.getElementById('progressContainer').classList.remove('visible');
    }, 3000);
}

// === Delete Cache ===
async function deleteCache() {
    if (!confirm('Weet je zeker dat je alle opgeslagen Picnic product-koppelingen wilt verwijderen?')) return;

    const btn = document.getElementById('deleteCacheBtn');
    btn.disabled = true;
    btn.textContent = 'Deleting...';

    try {
        const resp = await fetch('/cache', { method: 'DELETE' });
        const data = await resp.json();
        if (data.ok) {
            btn.textContent = `${data.cleared} cleared`;
            setTimeout(() => {
                btn.disabled = false;
                btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg> Delete cache';
            }, 2000);
        } else {
            btn.textContent = 'Error';
            btn.disabled = false;
        }
    } catch (err) {
        btn.textContent = 'Error';
        btn.disabled = false;
        console.error('Delete cache error:', err);
    }
}

// === Cart Helpers ===
function addCartItem(data) {
    const cartList = document.getElementById('cartList');

    const div = document.createElement('div');
    div.className = 'item in-cart';
    if (data.food_id) div.dataset.foodId = data.food_id;
    if (data.name) div.dataset.ingredientName = data.name;

    const itemInfo = document.createElement('div');
    itemInfo.className = 'cart-item-info';

    const text = document.createElement('div');
    text.className = 'cart-item-text';

    const name = document.createElement('div');
    name.className = 'item-name';
    name.textContent = data.picnic_product_name;
    text.appendChild(name);

    const meta = document.createElement('div');
    meta.className = 'item-meta';
    const parts = [];
    if (data.score) parts.push(Math.round(data.score) + '% match');
    else parts.push(data.status.replace('_', ' '));
    meta.textContent = parts.join(' ');
    text.appendChild(meta);

    itemInfo.appendChild(text);

    if (data.food_id) {
        const changeBtn = document.createElement('button');
        changeBtn.className = 'change-product-btn';
        changeBtn.textContent = 'wijzig';
        changeBtn.title = 'Andere variant kiezen';
        changeBtn.onclick = (e) => {
            e.stopPropagation();
            openProductModal(data.food_id, data.name || data.picnic_product_name, div);
        };
        itemInfo.appendChild(changeBtn);
    }

    const status = document.createElement('span');
    status.className = 'item-status';
    status.innerHTML = '&#10003; Added';

    div.appendChild(itemInfo);
    div.appendChild(status);
    cartList.appendChild(div);

    cartCount++;
    document.getElementById('cartCount').textContent = cartCount;
    updateCartEmpty();

    // Auto-scroll on mobile
    if (window.innerWidth <= 768) {
        div.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

function updateCartEmpty() {
    const empty = document.getElementById('cartEmpty');
    if (empty) {
        empty.style.display = cartCount > 0 ? 'none' : '';
    }
}

function findShoppingItem(index) {
    return document.querySelector(`#shoppingList .item[data-index="${index}"]`);
}

// === Auth Modal ===
function openAuthModal() {
    document.getElementById('authModal').classList.add('visible');
    resetAuthModal();
}

function closeAuthModal() {
    document.getElementById('authModal').classList.remove('visible');
}

function resetAuthModal() {
    // Reset to step 1
    document.getElementById('stepDot1').className = 'step-dot active';
    document.getElementById('stepDot2').className = 'step-dot inactive';
    document.getElementById('stepLine').className = 'step-line';
    document.getElementById('authStep1').classList.add('active');
    document.getElementById('authStep2').classList.remove('active');
    document.getElementById('authSuccess').classList.remove('visible');
    hideAuthMessage();

    // Clear code inputs
    document.querySelectorAll('.code-input').forEach(input => {
        input.value = '';
        input.classList.remove('filled');
    });

    // Re-enable buttons
    document.getElementById('sendCodeBtn').disabled = false;
    document.getElementById('verifyBtn').disabled = false;
}

function goToStep2() {
    document.getElementById('stepDot1').className = 'step-dot done';
    document.getElementById('stepDot1').innerHTML = '&#10003;';
    document.getElementById('stepLine').classList.add('done');
    document.getElementById('stepDot2').className = 'step-dot active';
    document.getElementById('authStep1').classList.remove('active');
    document.getElementById('authStep2').classList.add('active');

    // Focus first code input
    const firstInput = document.querySelector('.code-input');
    if (firstInput) firstInput.focus();
}

function showAuthMessage(text, type) {
    const msg = document.getElementById('authMessage');
    msg.textContent = text;
    msg.className = 'modal-message visible ' + type;
}

function hideAuthMessage() {
    document.getElementById('authMessage').className = 'modal-message';
}

async function requestSmsCode() {
    const btn = document.getElementById('sendCodeBtn');
    btn.disabled = true;
    btn.textContent = 'Sending...';
    hideAuthMessage();

    try {
        const resp = await fetch('/auth/request-code', { method: 'POST' });
        const data = await resp.json();
        if (data.ok) {
            showAuthMessage('SMS code sent! Check your phone.', 'success');
            setTimeout(goToStep2, 1000);
        } else {
            showAuthMessage('Error: ' + data.error, 'error');
            btn.disabled = false;
            btn.textContent = 'Send SMS Code';
        }
    } catch (err) {
        showAuthMessage('Network error: ' + err.message, 'error');
        btn.disabled = false;
        btn.textContent = 'Send SMS Code';
    }
}

async function verifyCode() {
    const inputs = document.querySelectorAll('.code-input');
    const code = Array.from(inputs).map(i => i.value).join('');
    if (code.length !== 6) {
        showAuthMessage('Please enter all 6 digits', 'error');
        document.querySelector('.modal').classList.add('shake');
        setTimeout(() => document.querySelector('.modal').classList.remove('shake'), 300);
        return;
    }

    const btn = document.getElementById('verifyBtn');
    btn.disabled = true;
    btn.textContent = 'Verifying...';
    hideAuthMessage();

    try {
        const resp = await fetch('/auth/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code }),
        });
        const data = await resp.json();
        if (data.ok) {
            // Show success state
            document.getElementById('authStep2').classList.remove('active');
            document.getElementById('authSuccess').classList.add('visible');
            document.getElementById('stepDot2').className = 'step-dot done';
            document.getElementById('stepDot2').innerHTML = '&#10003;';

            // Update navbar status
            document.getElementById('statusDot').classList.remove('needs-auth');
            document.getElementById('statusText').textContent = 'Connected';
            document.getElementById('authBtn').style.display = 'none';

            // Auto-close modal
            setTimeout(closeAuthModal, 2000);
        } else {
            showAuthMessage('Error: ' + data.error, 'error');
            document.querySelector('.modal').classList.add('shake');
            setTimeout(() => document.querySelector('.modal').classList.remove('shake'), 300);
            btn.disabled = false;
            btn.textContent = 'Verify & Connect';
        }
    } catch (err) {
        showAuthMessage('Network error: ' + err.message, 'error');
        btn.disabled = false;
        btn.textContent = 'Verify & Connect';
    }
}

// === Code Input Auto-Advance ===
function setupCodeInputs() {
    const inputs = document.querySelectorAll('.code-input');
    inputs.forEach((input, i) => {
        input.addEventListener('input', () => {
            const val = input.value.replace(/[^0-9]/g, '');
            input.value = val;
            if (val) {
                input.classList.add('filled');
                if (i < inputs.length - 1) {
                    inputs[i + 1].focus();
                }
            } else {
                input.classList.remove('filled');
            }
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Backspace' && !input.value && i > 0) {
                inputs[i - 1].focus();
                inputs[i - 1].value = '';
                inputs[i - 1].classList.remove('filled');
            }
            if (e.key === 'Enter') {
                verifyCode();
            }
        });

        // Handle paste of full code
        input.addEventListener('paste', (e) => {
            e.preventDefault();
            const text = (e.clipboardData || window.clipboardData).getData('text').replace(/[^0-9]/g, '');
            for (let j = 0; j < Math.min(text.length, inputs.length - i); j++) {
                inputs[i + j].value = text[j];
                inputs[i + j].classList.add('filled');
            }
            const nextEmpty = Array.from(inputs).findIndex(inp => !inp.value);
            if (nextEmpty >= 0) inputs[nextEmpty].focus();
            else inputs[inputs.length - 1].focus();
        });
    });
}

// Close modal on backdrop click
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-overlay')) {
        closeAuthModal();
        closeProductModal();
    }
});

// === Product Search Modal ===
let _productModalFoodId = null;
let _productModalCartItem = null;
let _productSearchTimeout = null;

function openProductModal(foodId, ingredientName, cartItemEl) {
    _productModalFoodId = foodId;
    _productModalCartItem = cartItemEl;

    document.getElementById('productModalIngredient').textContent = ingredientName || '';
    document.getElementById('productSearchInput').value = ingredientName || '';
    document.getElementById('productResults').innerHTML = '<div class="product-search-empty">Typ om te zoeken</div>';
    document.getElementById('productModal').classList.add('visible');

    const input = document.getElementById('productSearchInput');
    input.focus();
    input.select();

    // Auto-search with the ingredient name
    if (ingredientName) {
        searchPicnicProducts(ingredientName);
    }
}

function closeProductModal() {
    document.getElementById('productModal').classList.remove('visible');
    _productModalFoodId = null;
    _productModalCartItem = null;
    if (_productSearchTimeout) clearTimeout(_productSearchTimeout);
}

function onProductSearchInput() {
    if (_productSearchTimeout) clearTimeout(_productSearchTimeout);
    _productSearchTimeout = setTimeout(() => {
        const q = document.getElementById('productSearchInput').value.trim();
        if (q.length >= 2) searchPicnicProducts(q);
    }, 400);
}

async function searchPicnicProducts(query) {
    const resultsEl = document.getElementById('productResults');
    resultsEl.innerHTML = '<div class="product-search-spinner"><div class="spinner-large"></div></div>';

    try {
        const resp = await fetch('/picnic/search?q=' + encodeURIComponent(query));
        const products = await resp.json();

        if (!products.length) {
            resultsEl.innerHTML = '<div class="product-search-empty">Geen producten gevonden</div>';
            return;
        }

        resultsEl.innerHTML = '';
        products.forEach(product => {
            const item = document.createElement('div');
            item.className = 'product-result-item';
            item.onclick = () => selectProduct(product);

            const left = document.createElement('div');

            const name = document.createElement('div');
            name.className = 'product-result-name';
            name.textContent = product.name;
            left.appendChild(name);

            if (product.unit_quantity) {
                const meta = document.createElement('div');
                meta.className = 'product-result-meta';
                meta.textContent = product.unit_quantity;
                left.appendChild(meta);
            }

            const price = document.createElement('div');
            price.className = 'product-result-price';
            if (product.display_price) {
                price.textContent = '€' + (product.display_price / 100).toFixed(2);
            }

            item.appendChild(left);
            item.appendChild(price);
            resultsEl.appendChild(item);
        });
    } catch (err) {
        resultsEl.innerHTML = '<div class="product-search-empty">Fout bij zoeken: ' + err.message + '</div>';
    }
}

async function selectProduct(product) {
    if (!_productModalFoodId) return;

    try {
        await fetch('/foods/' + _productModalFoodId + '/product', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ product_id: product.id, product_name: product.name }),
        });

        // Update the cart item display
        if (_productModalCartItem) {
            const nameEl = _productModalCartItem.querySelector('.item-name');
            if (nameEl) nameEl.textContent = product.name;

            const metaEl = _productModalCartItem.querySelector('.item-meta');
            if (metaEl) metaEl.textContent = 'handmatig gekozen';
        }

        closeProductModal();
    } catch (err) {
        alert('Opslaan mislukt: ' + err.message);
    }
}
