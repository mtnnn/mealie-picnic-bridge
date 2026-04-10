// === State ===
let auditResult = null;
let currentTab = 'overview';
let photoFilter = 'all';
let currentPhotoSlug = null;
let dalleUrl = null;
let batchFixEventSource = null;
let currentBatchSlug = null;

const PROMPT_STORAGE_KEY = 'dalle_prompt_template';

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
document.addEventListener('DOMContentLoaded', () => {
    initPromptTemplate();
    // Try loading cached results
    fetch('/audit/results')
        .then(r => r.ok ? r.json() : null)
        .then(data => {
            if (data) {
                auditResult = data;
                renderAuditResults();
            }
        })
        .catch(() => {});
});

// === Prompt Template ===
function initPromptTemplate() {
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

// === Tab Navigation ===
function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    document.querySelectorAll('.tab-content').forEach(el => {
        el.classList.toggle('active', el.id === 'tab-' + tab);
    });
}

// === Scan ===
async function startScan() {
    const btn = document.getElementById('scanBtn');
    const spinner = document.getElementById('scanSpinner');
    const icon = document.getElementById('scanIcon');
    const progress = document.getElementById('scanProgress');

    btn.disabled = true;
    spinner.style.display = 'block';
    icon.style.display = 'none';
    progress.classList.add('visible');
    document.getElementById('auditSummary').textContent = 'Scanning...';

    const eventSource = new EventSource('/audit/scan/stream', {
        // POST is not directly supported by EventSource, use fetch + ReadableStream
    });

    // EventSource only supports GET. Use fetch for POST SSE.
    try {
        const resp = await fetch('/audit/scan/stream', { method: 'POST' });
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let totalRecipes = 0;
        let scannedCount = 0;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            let eventType = '';
            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    eventType = line.slice(7).trim();
                } else if (line.startsWith('data: ') && eventType) {
                    const data = JSON.parse(line.slice(6));
                    handleScanEvent(eventType, data);

                    if (eventType === 'scan_start') {
                        totalRecipes = data.total_recipes;
                    } else if (eventType === 'recipe_scanned') {
                        scannedCount++;
                        const pct = totalRecipes > 0 ? (scannedCount / totalRecipes * 100) : 0;
                        document.getElementById('scanProgressFill').style.width = pct + '%';
                        document.getElementById('scanProgressCount').textContent =
                            `${scannedCount} / ${totalRecipes}`;
                        document.getElementById('scanProgressLabel').textContent =
                            `Scanning: ${data.name}`;
                    }
                    eventType = '';
                }
            }
        }
    } catch (e) {
        document.getElementById('auditSummary').textContent = 'Scan failed: ' + e.message;
    } finally {
        btn.disabled = false;
        spinner.style.display = 'none';
        icon.style.display = '';
        setTimeout(() => { progress.classList.remove('visible'); }, 1000);
    }
}

function handleScanEvent(eventType, data) {
    if (eventType === 'scan_complete') {
        auditResult = data;
        renderAuditResults();
    }
}

// === Render Audit Results ===
function renderAuditResults() {
    if (!auditResult) return;

    const r = auditResult;

    // Summary
    document.getElementById('auditSummary').textContent =
        `${r.total_recipes} recipes — Health: ${r.overall_health_score}%`;

    // Show overview and tabs
    document.getElementById('auditOverview').style.display = 'flex';
    document.getElementById('auditTabs').style.display = 'flex';

    // Score circle
    const scoreEl = document.getElementById('overallScore');
    scoreEl.textContent = Math.round(r.overall_health_score);
    scoreEl.className = 'score-circle ' + scoreClass(r.overall_health_score);

    // Stats
    document.getElementById('statIngredients').textContent = r.ingredient_issue_recipe_count;
    document.getElementById('statSteps').textContent = r.step_issue_recipe_count;
    document.getElementById('statLanguage').textContent = r.language_issue_recipe_count;
    document.getElementById('statPhotos').textContent = r.photo_missing_count;

    // Stat card colors
    setStatColor('statIngredients', r.ingredient_issue_recipe_count);
    setStatColor('statSteps', r.step_issue_recipe_count);
    setStatColor('statLanguage', r.language_issue_recipe_count);
    setStatColor('statPhotos', r.photo_missing_count);

    // Tab issue indicators
    setTabIssue('ingredients', r.ingredient_issue_recipe_count > 0);
    setTabIssue('steps', r.step_issue_recipe_count > 0);
    setTabIssue('language', r.language_issue_recipe_count > 0);
    setTabIssue('photos', r.photo_missing_count > 0);

    // Render each tab
    renderOverviewTab();
    renderIngredientsTab();
    renderStepsTab();
    renderLanguageTab();
    renderPhotosTab();
}

function scoreClass(score) {
    if (score >= 75) return 'score-good';
    if (score >= 50) return 'score-ok';
    return 'score-bad';
}

function healthColor(score) {
    if (score >= 75) return 'good';
    if (score >= 50) return 'ok';
    return 'bad';
}

function setStatColor(id, count) {
    const card = document.getElementById(id).parentElement;
    card.classList.toggle('stat-warning', count > 0);
    card.classList.toggle('stat-ok', count === 0);
}

function setTabIssue(tab, hasIssues) {
    const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
    if (btn) btn.classList.toggle('has-issues', hasIssues);
}

// === Overview Tab ===
function renderOverviewTab() {
    const grid = document.getElementById('overviewGrid');
    const recipes = auditResult.recipes;
    if (!recipes.length) {
        grid.innerHTML = '<div class="empty-state"><p>No recipes found</p></div>';
        return;
    }
    grid.innerHTML = '';
    // Sort by health score ascending (worst first)
    const sorted = [...recipes].sort((a, b) => a.health_score - b.health_score);
    sorted.forEach(recipe => grid.appendChild(createAuditCard(recipe)));
}

function createAuditCard(recipe) {
    const card = document.createElement('div');
    card.className = 'recipe-card';
    card.dataset.slug = recipe.recipe_slug;

    // Thumbnail
    const thumb = document.createElement('div');
    thumb.className = 'recipe-thumb';
    if (recipe.has_photo && recipe.image_url) {
        const img = document.createElement('img');
        img.src = recipe.image_url;
        img.alt = recipe.recipe_name;
        img.loading = 'lazy';
        thumb.appendChild(img);
    } else {
        thumb.innerHTML = '<div class="no-photo-placeholder">&#x1F4F7;</div>';
    }

    // Info
    const info = document.createElement('div');
    info.className = 'recipe-card-info';
    const nameEl = document.createElement('div');
    nameEl.className = 'recipe-card-name';
    nameEl.textContent = recipe.recipe_name;
    info.appendChild(nameEl);

    // Issue icons
    const icons = document.createElement('div');
    icons.className = 'issue-icons';
    if (recipe.ingredient_issue_count > 0) {
        icons.innerHTML += `<span class="issue-icon ingredients">${recipe.ingredient_issue_count} ing</span>`;
    }
    if (!recipe.has_instructions) {
        icons.innerHTML += '<span class="issue-icon steps">No steps</span>';
    }
    if (!recipe.is_correct_language && recipe.detected_language) {
        icons.innerHTML += `<span class="issue-icon language">${recipe.detected_language.toUpperCase()}</span>`;
    }
    if (!recipe.has_photo) {
        icons.innerHTML += '<span class="issue-icon photos">No photo</span>';
    }

    // Health bar
    const healthBar = document.createElement('div');
    healthBar.className = 'health-bar';
    const healthFill = document.createElement('div');
    healthFill.className = 'health-fill ' + healthColor(recipe.health_score);
    healthFill.style.width = recipe.health_score + '%';
    healthBar.appendChild(healthFill);

    card.append(thumb, info, icons, healthBar);
    return card;
}

// === Ingredients Tab ===
function renderIngredientsTab() {
    const grid = document.getElementById('ingredientGrid');
    const issues = auditResult.ingredient_issues;
    const info = document.getElementById('ingredientTabInfo');
    const btn = document.getElementById('fixIngredientsBtn');

    if (!issues.length) {
        grid.innerHTML = '<div class="empty-state"><p>All ingredients are properly structured</p></div>';
        info.textContent = 'No issues found';
        btn.style.display = 'none';
        return;
    }

    info.textContent = `${issues.length} recipes with ingredient issues`;
    btn.style.display = '';
    grid.innerHTML = '';

    issues.forEach(item => {
        const card = document.createElement('div');
        card.className = 'recipe-card';

        const info = document.createElement('div');
        info.className = 'recipe-card-info';
        info.style.padding = '12px';

        const name = document.createElement('div');
        name.className = 'recipe-card-name';
        name.textContent = item.recipe_name;

        const detail = document.createElement('div');
        detail.style.cssText = 'font-size:12px;color:var(--text-muted);margin-top:4px;';

        const byType = {};
        item.issues.forEach(iss => {
            byType[iss.issue_type] = (byType[iss.issue_type] || 0) + 1;
        });
        const parts = [];
        if (byType.missing_food) parts.push(`${byType.missing_food} missing food`);
        if (byType.missing_quantity) parts.push(`${byType.missing_quantity} missing qty`);
        if (byType.missing_unit) parts.push(`${byType.missing_unit} missing unit`);
        detail.textContent = parts.join(', ');

        info.append(name, detail);
        card.appendChild(info);
        grid.appendChild(card);
    });
}

// === Steps Tab ===
function renderStepsTab() {
    const grid = document.getElementById('stepsGrid');
    const issues = auditResult.step_issues;
    const info = document.getElementById('stepsTabInfo');

    if (!issues.length) {
        grid.innerHTML = '<div class="empty-state"><p>All recipes have instructions</p></div>';
        info.textContent = 'No issues found';
        return;
    }

    info.textContent = `${issues.length} recipes missing instructions`;
    grid.innerHTML = '';

    issues.forEach(item => {
        const card = document.createElement('div');
        card.className = 'recipe-card';

        const cardInfo = document.createElement('div');
        cardInfo.className = 'recipe-card-info';
        cardInfo.style.padding = '12px';

        const name = document.createElement('div');
        name.className = 'recipe-card-name';
        name.textContent = item.recipe_name;

        const detail = document.createElement('div');
        detail.style.cssText = 'font-size:12px;color:var(--error);margin-top:4px;';
        detail.textContent = 'No instructions found';

        cardInfo.append(name, detail);
        card.appendChild(cardInfo);
        grid.appendChild(card);
    });
}

// === Language Tab ===
function renderLanguageTab() {
    const grid = document.getElementById('languageGrid');
    const issues = auditResult.language_issues;
    const info = document.getElementById('languageTabInfo');
    const btn = document.getElementById('fixLanguageBtn');

    if (!issues.length) {
        grid.innerHTML = '<div class="empty-state"><p>All recipes are in the target language</p></div>';
        info.textContent = 'No issues found';
        btn.style.display = 'none';
        return;
    }

    info.textContent = `${issues.length} recipes in wrong language`;
    btn.style.display = '';
    grid.innerHTML = '';

    issues.forEach(item => {
        const card = document.createElement('div');
        card.className = 'recipe-card';

        const cardInfo = document.createElement('div');
        cardInfo.className = 'recipe-card-info';
        cardInfo.style.padding = '12px';

        const name = document.createElement('div');
        name.className = 'recipe-card-name';
        name.textContent = item.recipe_name;

        const detail = document.createElement('div');
        detail.style.cssText = 'font-size:12px;color:var(--text-muted);margin-top:4px;';
        detail.textContent = `Detected: ${item.detected_language.toUpperCase()} (${Math.round(item.confidence * 100)}% confident) — Target: ${item.target_language.toUpperCase()}`;

        cardInfo.append(name, detail);
        card.appendChild(cardInfo);
        grid.appendChild(card);
    });
}

// === Photos Tab ===
function renderPhotosTab() {
    const grid = document.getElementById('photoGrid');
    const recipes = auditResult.recipes;
    const filtersEl = document.getElementById('photoFilters');
    filtersEl.style.display = 'flex';

    const filtered = getPhotoFiltered(recipes);
    renderPhotoGrid(grid, filtered);
}

function filterPhotos(filter) {
    photoFilter = filter;
    document.querySelectorAll('#photoFilters .filter-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.filter === filter);
    });
    const grid = document.getElementById('photoGrid');
    renderPhotoGrid(grid, getPhotoFiltered(auditResult.recipes));
}

function getPhotoFiltered(recipes) {
    if (photoFilter === 'missing') return recipes.filter(r => !r.has_photo);
    if (photoFilter === 'has') return recipes.filter(r => r.has_photo);
    return recipes;
}

function renderPhotoGrid(grid, recipes) {
    if (!recipes.length) {
        grid.innerHTML = '<div class="empty-state"><p>No recipes</p></div>';
        return;
    }
    grid.innerHTML = '';
    recipes.forEach(recipe => {
        const card = document.createElement('div');
        card.className = 'recipe-card' + (recipe.has_photo ? '' : ' missing-photo');
        card.dataset.slug = recipe.recipe_slug;

        const thumb = document.createElement('div');
        thumb.className = 'recipe-thumb';
        if (recipe.has_photo && recipe.image_url) {
            const img = document.createElement('img');
            img.src = recipe.image_url;
            img.alt = recipe.recipe_name;
            img.loading = 'lazy';
            thumb.appendChild(img);
        } else {
            thumb.innerHTML = '<div class="no-photo-placeholder">&#x1F4F7;</div>';
        }

        const info = document.createElement('div');
        info.className = 'recipe-card-info';
        const nameEl = document.createElement('div');
        nameEl.className = 'recipe-card-name';
        nameEl.textContent = recipe.recipe_name;
        info.appendChild(nameEl);

        const badge = document.createElement('span');
        badge.className = 'status-badge ' + (recipe.has_photo ? 'matched' : 'no_match');
        badge.textContent = recipe.has_photo ? 'Has Photo' : 'No Photo';

        card.append(thumb, info, badge);

        if (!recipe.has_photo) {
            const btn = document.createElement('button');
            btn.className = 'find-photo-btn';
            btn.textContent = 'Find Photo';
            btn.onclick = () => openPhotoModal(recipe.recipe_slug, recipe.recipe_name);
            card.appendChild(btn);
        }

        grid.appendChild(card);
    });
}

// === Photo Modal ===
async function openPhotoModal(slug, name) {
    currentPhotoSlug = slug;
    dalleUrl = null;

    document.getElementById('photoModalTitle').textContent = `Find Photo: ${name}`;
    document.getElementById('photoModalSearching').style.display = 'flex';
    document.getElementById('photoModalSearching').innerHTML =
        '<div class="spinner-large"></div><p>Searching for photos...</p>';
    document.getElementById('photoModalResults').style.display = 'none';
    document.getElementById('dalleImg').innerHTML = '';
    document.getElementById('dalleError').textContent = '';
    document.getElementById('braveImgs').innerHTML = '';
    document.getElementById('braveError').textContent = '';
    document.getElementById('dalleApplyBtn').disabled = true;
    document.getElementById('dalleApplyBtn').textContent = 'Apply';
    document.getElementById('dalleApplyBtn').style.background = '';
    document.getElementById('dalleRefreshBtn').disabled = false;

    document.getElementById('photoModal').style.display = 'flex';
    await generatePhotos();
}

async function generatePhotos() {
    if (!currentPhotoSlug) return;
    const template = getPromptTemplate();
    const body = template !== defaultTemplate ? { prompt_template: template } : {};

    try {
        const resp = await fetch(`/audit/recipes/${currentPhotoSlug}/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) throw new Error(await resp.text());
        renderPhotoModalResults(await resp.json());
    } catch (e) {
        document.getElementById('photoModalSearching').innerHTML =
            `<p style="color:var(--error)">Error: ${escHtml(e.message)}</p>`;
    }
}

async function refreshPhotos() {
    if (!currentPhotoSlug) return;
    const btn = document.getElementById('dalleRefreshBtn');
    btn.disabled = true;
    dalleUrl = null;

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
        const resp = await fetch(`/audit/recipes/${currentPhotoSlug}/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) throw new Error(await resp.text());
        document.getElementById('dalleImg').innerHTML = '';
        document.getElementById('braveImgs').innerHTML = '';
        renderPhotoModalResults(await resp.json());
    } catch (e) {
        document.getElementById('dalleImg').innerHTML = '';
        document.getElementById('braveImgs').innerHTML = '';
        document.getElementById('dalleError').textContent = 'Error: ' + e.message;
    } finally {
        btn.disabled = false;
    }
}

function renderPhotoModalResults(result) {
    document.getElementById('photoModalSearching').style.display = 'none';
    document.getElementById('photoModalResults').style.display = 'block';

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
            const applyBtn = document.createElement('button');
            applyBtn.className = 'apply-btn';
            applyBtn.textContent = 'Apply';
            const url = candidate.url;
            applyBtn.onclick = () => applyPhoto('brave', url, applyBtn);
            wrapper.append(img, applyBtn);
            braveGrid.appendChild(wrapper);
        });
    } else {
        document.getElementById('braveError').textContent =
            'Search failed: ' + (result.brave_error || 'no results found');
    }
}

function closePhotoModal() {
    document.getElementById('photoModal').style.display = 'none';
    currentPhotoSlug = null;
    dalleUrl = null;
}

function handlePhotoModalClick(event) {
    if (event.target === document.getElementById('photoModal')) {
        closePhotoModal();
    }
}

async function applyPhoto(source, braveUrl, braveBtn) {
    if (!currentPhotoSlug) return;
    const url = source === 'dalle' ? dalleUrl : braveUrl;
    if (!url) return;

    const btn = source === 'dalle' ? document.getElementById('dalleApplyBtn') : braveBtn;
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Applying...';

    try {
        const resp = await fetch(`/audit/recipes/${currentPhotoSlug}/apply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_url: url }),
        });
        if (!resp.ok) throw new Error(await resp.text());

        btn.textContent = 'Applied!';
        btn.style.background = 'var(--success)';

        if (auditResult) {
            const recipe = auditResult.recipes.find(r => r.recipe_slug === currentPhotoSlug);
            if (recipe) {
                recipe.has_photo = true;
                recipe.image_url = null;
            }
        }

        setTimeout(() => {
            closePhotoModal();
            if (auditResult) renderPhotosTab();
        }, 1200);
    } catch (e) {
        btn.textContent = originalText;
        btn.disabled = false;
        btn.style.background = '';
        alert('Failed to apply photo: ' + e.message);
    }
}

// === Batch Fix ===
async function startBatchFix(fixType) {
    let slugs;
    if (fixType === 'ingredients') {
        slugs = auditResult.ingredient_issues.map(i => i.recipe_slug);
    } else if (fixType === 'language') {
        slugs = auditResult.language_issues.map(i => i.recipe_slug);
    } else {
        return;
    }

    if (!slugs.length) return;

    // Open modal
    document.getElementById('fixModal').style.display = 'flex';
    document.getElementById('fixModalTitle').textContent =
        fixType === 'ingredients' ? 'Fix Ingredients' : 'Translate Recipes';
    document.getElementById('fixProgressText').textContent = '';
    document.getElementById('fixProgressFill').style.width = '0%';
    document.getElementById('fixProposal').innerHTML = '';
    document.getElementById('fixActions').style.display = 'none';
    document.getElementById('fixWaiting').style.display = 'flex';
    document.getElementById('fixComplete').style.display = 'none';

    try {
        const resp = await fetch('/audit/fix/batch/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fix_type: fixType, recipe_slugs: slugs }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            let eventType = '';
            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    eventType = line.slice(7).trim();
                } else if (line.startsWith('data: ') && eventType) {
                    const data = JSON.parse(line.slice(6));
                    handleBatchFixEvent(eventType, data, fixType);
                    eventType = '';
                }
            }
        }
    } catch (e) {
        document.getElementById('fixWaiting').innerHTML =
            `<p style="color:var(--error)">Error: ${escHtml(e.message)}</p>`;
    }
}

function handleBatchFixEvent(eventType, data, fixType) {
    if (eventType === 'batch_start') {
        // ready
    } else if (eventType === 'fix_propose') {
        currentBatchSlug = data.recipe_slug;
        const pct = data.total > 0 ? ((data.index + 1) / data.total * 100) : 0;
        document.getElementById('fixProgressFill').style.width = pct + '%';
        document.getElementById('fixProgressText').textContent =
            `Recipe ${data.index + 1} of ${data.total}`;

        document.getElementById('fixWaiting').style.display = 'none';
        document.getElementById('fixActions').style.display = 'flex';

        const proposal = data.proposal;
        if (fixType === 'ingredients') {
            renderIngredientProposal(proposal);
        } else {
            renderTranslationProposal(proposal);
        }
    } else if (eventType === 'fix_applied' || eventType === 'fix_skipped') {
        document.getElementById('fixActions').style.display = 'none';
        document.getElementById('fixWaiting').style.display = 'flex';
        document.getElementById('fixWaiting').innerHTML =
            '<div class="spinner-large"></div><p>Preparing next recipe...</p>';
    } else if (eventType === 'fix_error') {
        // Show error briefly then continue
        document.getElementById('fixActions').style.display = 'none';
        document.getElementById('fixWaiting').style.display = 'flex';
        document.getElementById('fixWaiting').innerHTML =
            `<p style="color:var(--error)">Error: ${escHtml(data.error)}</p>`;
        // Auto-continue after the server moves to next
    } else if (eventType === 'batch_complete') {
        document.getElementById('fixWaiting').style.display = 'none';
        document.getElementById('fixActions').style.display = 'none';
        document.getElementById('fixProgressFill').style.width = '100%';

        const stats = document.getElementById('fixCompleteStats');
        stats.innerHTML =
            `<strong>${data.applied}</strong> fixed, ` +
            `<strong>${data.skipped}</strong> skipped` +
            (data.errors > 0 ? `, <strong style="color:var(--error)">${data.errors}</strong> errors` : '');
        document.getElementById('fixComplete').style.display = 'block';
    }
}

function renderIngredientProposal(proposal) {
    const el = document.getElementById('fixProposal');
    let html = `<h4 style="margin-bottom:12px;">${escHtml(proposal.recipe_name)}</h4>`;
    html += `<div style="font-size:12px;color:var(--text-muted);margin-bottom:12px;">Parser: ${escHtml(proposal.parser_used)}</div>`;

    if (!proposal.ingredients.length) {
        html += '<p style="color:var(--text-muted)">No fixes needed for this recipe.</p>';
        el.innerHTML = html;
        return;
    }

    html += '<table><thead><tr><th>Original</th><th>Proposed</th><th>Confidence</th></tr></thead><tbody>';
    proposal.ingredients.forEach(fix => {
        const conf = fix.confidence != null ? Math.round(fix.confidence * 100) + '%' : '-';
        const proposed = [
            fix.proposed_quantity != null ? fix.proposed_quantity : '',
            fix.proposed_unit || '',
            fix.proposed_food || '',
        ].filter(Boolean).join(' ');

        html += `<tr>
            <td class="original">${escHtml(fix.original_text || '-')}</td>
            <td class="proposed">${escHtml(proposed || '-')}</td>
            <td class="confidence">${conf}</td>
        </tr>`;
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

function renderTranslationProposal(proposal) {
    const el = document.getElementById('fixProposal');
    let html = '';

    // Name
    html += '<div class="translation-preview">';
    html += '<h4>Name</h4>';
    html += `<div class="original-text">${escHtml(proposal.recipe_name)}</div>`;
    html += `<div class="proposed-text">${escHtml(proposal.proposed_name)}</div>`;
    html += '</div>';

    // Description
    if (proposal.proposed_description) {
        html += '<div class="translation-preview">';
        html += '<h4>Description</h4>';
        html += `<div class="proposed-text">${escHtml(proposal.proposed_description)}</div>`;
        html += '</div>';
    }

    // Steps
    if (proposal.proposed_steps && proposal.proposed_steps.length) {
        html += '<div class="translation-preview">';
        html += '<h4>Steps</h4>';
        proposal.proposed_steps.forEach((step, i) => {
            html += `<div class="proposed-text" style="margin-bottom:4px;">${i + 1}. ${escHtml(step)}</div>`;
        });
        html += '</div>';
    }

    // Ingredients
    if (proposal.ingredient_translations && proposal.ingredient_translations.length) {
        html += '<div class="translation-preview">';
        html += '<h4>Ingredients</h4>';
        html += '<table style="width:100%;font-size:13px;"><thead><tr><th>Original</th><th>Translated</th><th>Match</th></tr></thead><tbody>';
        proposal.ingredient_translations.forEach(t => {
            const match = t.matched_food_name
                ? `<span style="color:var(--success)">${escHtml(t.matched_food_name)}</span>`
                : '<span style="color:var(--warning)">New food</span>';
            html += `<tr>
                <td>${escHtml(t.original_food_name)}</td>
                <td class="proposed">${escHtml(t.translated_food_name)}</td>
                <td>${match}</td>
            </tr>`;
        });
        html += '</tbody></table>';
        html += '</div>';
    }

    el.innerHTML = html;
}

async function confirmBatchFix(action) {
    if (!currentBatchSlug) return;

    const applyBtn = document.getElementById('fixApplyBtn');
    const skipBtn = document.getElementById('fixSkipBtn');
    applyBtn.disabled = true;
    skipBtn.disabled = true;

    try {
        await fetch('/audit/fix/batch/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ recipe_slug: currentBatchSlug, action }),
        });
    } catch (e) {
        console.error('Failed to confirm fix:', e);
    } finally {
        applyBtn.disabled = false;
        skipBtn.disabled = false;
    }
}

function closeFixModal() {
    document.getElementById('fixModal').style.display = 'none';
    currentBatchSlug = null;
}

function handleFixModalClick(event) {
    if (event.target === document.getElementById('fixModal')) {
        skipAndCloseBatchFix();
    }
}

function skipAndCloseBatchFix() {
    if (currentBatchSlug) {
        confirmBatchFix('skip');
    }
    closeFixModal();
}

// === Helpers ===
function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
