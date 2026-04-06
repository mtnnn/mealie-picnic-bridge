// === State ===
let allRecipes = [];
let currentFilter = 'all';
let currentSlug = null;
let currentName = null;
let dalleUrl = null;

const PROMPT_STORAGE_KEY = 'dalle_prompt_template';

// Hardcoded fallback so the textarea is never empty
const FALLBACK_TEMPLATE =
    'Hyper-realistic food photograph of {name}. ' +
    'Shot on a professional DSLR camera, 85mm lens, shallow depth of field. ' +
    'Studio lighting with soft natural light, photorealistic textures, ultra-detailed. ' +
    'Food magazine quality, award-winning food photography. ' +
    '{description} ' +
    'Key ingredients: {ingredients}. ' +
    'Clean elegant plating on a neutral surface. ' +
    'No illustration, no painting, no CGI — pure photorealism.';

let defaultTemplate = FALLBACK_TEMPLATE;

// === Init ===
document.addEventListener('DOMContentLoaded', async () => {
    initPromptTemplate();
    loadRecipes();
});

// === Prompt Template ===
function initPromptTemplate() {
    // Try to load from API (non-blocking), but use fallback immediately
    fetch('/audit/prompt-template')
        .then(r => r.ok ? r.json() : null)
        .then(data => {
            if (data && data.template) defaultTemplate = data.template;
        })
        .catch(() => {});

    const saved = localStorage.getItem(PROMPT_STORAGE_KEY);
    const el = document.getElementById('promptTemplate');
    el.value = saved || defaultTemplate;
    el.addEventListener('input', onPromptInput);
}

function getPromptTemplate() {
    const val = document.getElementById('promptTemplate').value.trim();
    return val || defaultTemplate;
}

function onPromptInput() {
    const current = document.getElementById('promptTemplate').value.trim();
    const saved = localStorage.getItem(PROMPT_STORAGE_KEY);
    const baseline = saved || defaultTemplate;
    document.getElementById('promptSaveBtn').disabled = (current === baseline);
}

function savePromptTemplate() {
    const val = document.getElementById('promptTemplate').value.trim();
    if (val && val !== defaultTemplate) {
        localStorage.setItem(PROMPT_STORAGE_KEY, val);
    } else {
        localStorage.removeItem(PROMPT_STORAGE_KEY);
    }
    const btn = document.getElementById('promptSaveBtn');
    btn.disabled = true;
    btn.textContent = 'Saved!';
    setTimeout(() => { btn.textContent = 'Save changes'; }, 1500);
}

function resetPromptTemplate() {
    document.getElementById('promptTemplate').value = defaultTemplate;
    localStorage.removeItem(PROMPT_STORAGE_KEY);
    document.getElementById('promptSaveBtn').disabled = true;
}

// === Load Recipes ===
async function loadRecipes() {
    const grid = document.getElementById('recipeGrid');
    const btn = document.getElementById('refreshBtn');
    const spinner = document.getElementById('refreshSpinner');
    const icon = document.getElementById('refreshIcon');

    grid.innerHTML = '<div class="empty-state"><p>Loading…</p></div>';
    btn.disabled = true;
    spinner.style.display = 'block';
    icon.style.display = 'none';

    try {
        const resp = await fetch('/audit/recipes');
        if (!resp.ok) throw new Error(await resp.text());
        allRecipes = await resp.json();
        const missing = allRecipes.filter(r => !r.has_photo).length;
        document.getElementById('auditSummary').textContent =
            `${allRecipes.length} recipes — ${missing} missing photo`;
        document.getElementById('auditFilters').style.display = 'flex';
        renderGrid(getFiltered());
    } catch (e) {
        grid.innerHTML = `<div class="empty-state"><p>Error loading recipes: ${escHtml(e.message)}</p></div>`;
        document.getElementById('auditSummary').textContent = 'Error';
    } finally {
        btn.disabled = false;
        spinner.style.display = 'none';
        icon.style.display = '';
    }
}

// === Filter ===
function filterRecipes(filter) {
    currentFilter = filter;
    ['all', 'missing', 'has'].forEach(f => {
        const id = 'filter' + f.charAt(0).toUpperCase() + f.slice(1);
        document.getElementById(id).classList.toggle('active', f === filter);
    });
    renderGrid(getFiltered());
}

function getFiltered() {
    if (currentFilter === 'missing') return allRecipes.filter(r => !r.has_photo);
    if (currentFilter === 'has') return allRecipes.filter(r => r.has_photo);
    return allRecipes;
}

// === Render Grid ===
function renderGrid(recipes) {
    const grid = document.getElementById('recipeGrid');
    if (!recipes.length) {
        grid.innerHTML = '<div class="empty-state"><p>No recipes</p></div>';
        return;
    }
    grid.innerHTML = '';
    recipes.forEach(recipe => grid.appendChild(createRecipeCard(recipe)));
}

function createRecipeCard(recipe) {
    const card = document.createElement('div');
    card.className = 'recipe-card' + (recipe.has_photo ? '' : ' missing-photo');
    card.dataset.slug = recipe.slug;

    // Thumbnail
    const thumb = document.createElement('div');
    thumb.className = 'recipe-thumb';
    if (recipe.has_photo && recipe.image_url) {
        const img = document.createElement('img');
        img.src = recipe.image_url;
        img.alt = recipe.name;
        img.loading = 'lazy';
        thumb.appendChild(img);
    } else {
        thumb.innerHTML = '<div class="no-photo-placeholder">📷</div>';
    }

    // Info
    const info = document.createElement('div');
    info.className = 'recipe-card-info';
    const nameEl = document.createElement('div');
    nameEl.className = 'recipe-card-name';
    nameEl.textContent = recipe.name;
    info.appendChild(nameEl);

    // Status badge
    const badge = document.createElement('span');
    badge.className = 'status-badge ' + (recipe.has_photo ? 'matched' : 'no_match');
    badge.textContent = recipe.has_photo ? 'Has Photo' : 'No Photo';

    card.append(thumb, info, badge);

    // Find Photo button (only for missing)
    if (!recipe.has_photo) {
        const btn = document.createElement('button');
        btn.className = 'find-photo-btn';
        btn.textContent = 'Find Photo';
        btn.onclick = () => openPhotoModal(recipe.slug, recipe.name);
        card.appendChild(btn);
    }

    return card;
}

// === Photo Modal ===
async function openPhotoModal(slug, name) {
    currentSlug = slug;
    currentName = name;
    dalleUrl = null;

    document.getElementById('modalTitle').textContent = `Find Photo: ${name}`;
    resetModalState();

    document.getElementById('photoModal').style.display = 'flex';

    await generatePhotos();
}

function resetModalState() {
    document.getElementById('modalSearching').style.display = 'flex';
    document.getElementById('modalSearching').innerHTML =
        '<div class="spinner-large"></div><p>Searching for photos — this may take a moment…</p>';
    document.getElementById('modalResults').style.display = 'none';
    document.getElementById('dalleImg').innerHTML = '';
    document.getElementById('dalleError').textContent = '';
    document.getElementById('braveImgs').innerHTML = '';
    document.getElementById('braveError').textContent = '';
    document.getElementById('dalleApplyBtn').disabled = true;
    document.getElementById('dalleApplyBtn').textContent = 'Apply';
    document.getElementById('dalleApplyBtn').style.background = '';
    document.getElementById('dalleRefreshBtn').disabled = false;
}

async function generatePhotos() {
    if (!currentSlug) return;

    const template = getPromptTemplate();
    const body = template !== defaultTemplate ? { prompt_template: template } : {};

    try {
        const resp = await fetch(`/audit/recipes/${currentSlug}/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) {
            const text = await resp.text();
            throw new Error(text);
        }
        const result = await resp.json();
        renderModalResults(result);
    } catch (e) {
        document.getElementById('modalSearching').innerHTML =
            `<p style="color:var(--error)">Error: ${escHtml(e.message)}</p>`;
    }
}

async function refreshPhotos() {
    if (!currentSlug) return;

    const btn = document.getElementById('dalleRefreshBtn');
    btn.disabled = true;
    dalleUrl = null;

    // Show spinners in the result areas
    document.getElementById('dalleImg').innerHTML = '<div class="spinner-large"></div>';
    document.getElementById('dalleError').textContent = '';
    document.getElementById('dalleApplyBtn').disabled = true;
    document.getElementById('dalleApplyBtn').textContent = 'Apply';
    document.getElementById('dalleApplyBtn').style.background = '';
    document.getElementById('braveImgs').innerHTML = '<div class="spinner-large"></div>';
    document.getElementById('braveError').textContent = '';

    const template = getPromptTemplate();
    const body = template !== defaultTemplate ? { prompt_template: template } : {};

    try {
        const resp = await fetch(`/audit/recipes/${currentSlug}/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) throw new Error(await resp.text());
        const result = await resp.json();

        document.getElementById('dalleImg').innerHTML = '';
        document.getElementById('braveImgs').innerHTML = '';
        renderModalResults(result);
    } catch (e) {
        document.getElementById('dalleImg').innerHTML = '';
        document.getElementById('braveImgs').innerHTML = '';
        document.getElementById('dalleError').textContent = 'Error: ' + e.message;
    } finally {
        btn.disabled = false;
    }
}

function renderModalResults(result) {
    document.getElementById('modalSearching').style.display = 'none';
    document.getElementById('modalResults').style.display = 'block';

    // DALL-E result
    if (result.dalle_result && result.dalle_result.url) {
        dalleUrl = result.dalle_result.url;
        const img = document.createElement('img');
        img.src = dalleUrl;
        img.alt = result.dalle_result.title || 'DALL-E result';
        document.getElementById('dalleImg').appendChild(img);
        document.getElementById('dalleApplyBtn').disabled = false;
    } else {
        document.getElementById('dalleError').textContent =
            'Generation failed: ' + (result.dalle_error || 'unknown error');
    }

    // Brave results
    const braveGrid = document.getElementById('braveImgs');
    if (result.brave_results && result.brave_results.length > 0) {
        result.brave_results.forEach(candidate => {
            if (!candidate.url) return;
            const wrapper = document.createElement('div');
            wrapper.className = 'brave-img-wrapper';
            const img = document.createElement('img');
            img.src = candidate.url;
            img.alt = candidate.title || 'Brave result';
            img.loading = 'lazy';
            img.onerror = () => wrapper.style.display = 'none';
            const btn = document.createElement('button');
            btn.className = 'apply-btn';
            btn.textContent = 'Apply';
            const url = candidate.url;
            btn.onclick = () => applyPhoto('brave', url, btn);
            wrapper.append(img, btn);
            braveGrid.appendChild(wrapper);
        });
    } else {
        document.getElementById('braveError').textContent =
            'Search failed: ' + (result.brave_error || 'no results found');
    }
}

function closePhotoModal() {
    document.getElementById('photoModal').style.display = 'none';
    currentSlug = null;
    dalleUrl = null;
}

function handleModalClick(event) {
    if (event.target === document.getElementById('photoModal')) {
        closePhotoModal();
    }
}

// === Apply Photo ===
async function applyPhoto(source, braveUrl, braveBtn) {
    if (!currentSlug) return;
    const url = source === 'dalle' ? dalleUrl : braveUrl;
    if (!url) return;

    const btn = source === 'dalle' ? document.getElementById('dalleApplyBtn') : braveBtn;
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Applying…';

    try {
        const resp = await fetch(`/audit/recipes/${currentSlug}/apply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_url: url }),
        });
        if (!resp.ok) throw new Error(await resp.text());

        btn.textContent = 'Applied!';
        btn.style.background = 'var(--success)';

        // Mark recipe as having a photo in local state
        const recipe = allRecipes.find(r => r.slug === currentSlug);
        if (recipe) {
            recipe.has_photo = true;
            recipe.image_url = null; // will reload on next refresh
        }

        setTimeout(() => {
            closePhotoModal();
            renderGrid(getFiltered());
            // Update summary count
            const missing = allRecipes.filter(r => !r.has_photo).length;
            document.getElementById('auditSummary').textContent =
                `${allRecipes.length} recipes — ${missing} missing photo`;
        }, 1200);
    } catch (e) {
        btn.textContent = originalText;
        btn.disabled = false;
        btn.style.background = '';
        alert('Failed to apply photo: ' + e.message);
    }
}

// === Helpers ===
function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
