// === State ===
let recipes = [];
let selectedRecipeSlug = null;
let currentIngredients = [];

// === Init ===
document.addEventListener('DOMContentLoaded', async () => {
    await loadRecipes();
});

// Override product selection callback for the mapping page
_onProductSelected = async function(product, foodId) {
    await fetch('/foods/' + foodId + '/product', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            product_id: product.id,
            product_name: product.name,
            image_id: product.image_id,
        }),
    });
    // Refresh ingredient list to show updated mapping
    if (selectedRecipeSlug) {
        await selectRecipe(selectedRecipeSlug);
    }
};

// === Recipe Loading ===
async function loadRecipes() {
    try {
        const resp = await fetch('/recipes');
        recipes = await resp.json();
        renderRecipeList(recipes);
        document.getElementById('recipeCount').textContent = recipes.length;
        document.getElementById('mappingSummary').textContent = recipes.length + ' recipes';
    } catch (err) {
        document.getElementById('recipeList').innerHTML =
            '<div class="empty-state"><p>Failed to load recipes</p></div>';
    }
}

function renderRecipeList(list) {
    const container = document.getElementById('recipeList');
    container.innerHTML = '';

    if (!list.length) {
        container.innerHTML = '<div class="empty-state"><p>No recipes found</p></div>';
        return;
    }

    list.forEach(recipe => {
        const div = document.createElement('div');
        div.className = 'mapping-recipe-item';
        div.dataset.slug = recipe.slug;
        if (recipe.slug === selectedRecipeSlug) div.classList.add('selected');
        div.onclick = () => selectRecipe(recipe.slug);

        if (recipe.image) {
            const img = document.createElement('img');
            img.className = 'mapping-recipe-thumb';
            img.src = recipe.image;
            img.loading = 'lazy';
            div.appendChild(img);
        } else {
            const placeholder = document.createElement('div');
            placeholder.className = 'mapping-recipe-thumb-placeholder';
            placeholder.textContent = recipe.name.charAt(0).toUpperCase();
            div.appendChild(placeholder);
        }

        const name = document.createElement('div');
        name.className = 'mapping-recipe-name';
        name.textContent = recipe.name;
        div.appendChild(name);

        container.appendChild(div);
    });

    document.getElementById('recipeCount').textContent = list.length;
}

function filterRecipes() {
    const q = document.getElementById('recipeSearchInput').value.toLowerCase();
    const filtered = q ? recipes.filter(r => r.name.toLowerCase().includes(q)) : recipes;
    renderRecipeList(filtered);
}

// === Recipe Selection & Ingredient Loading ===
async function selectRecipe(slug) {
    selectedRecipeSlug = slug;

    // Highlight selected recipe
    document.querySelectorAll('.mapping-recipe-item').forEach(el => {
        el.classList.toggle('selected', el.dataset.slug === slug);
    });

    // Load ingredients
    const container = document.getElementById('ingredientList');
    container.innerHTML = '<div class="empty-state"><p>Loading ingredients...</p></div>';

    try {
        const resp = await fetch('/recipes/' + encodeURIComponent(slug) + '/ingredients');
        const data = await resp.json();
        currentIngredients = data.ingredients;

        document.getElementById('ingredientPanelTitle').textContent = data.name;
        document.getElementById('ingredientCount').textContent = currentIngredients.length;

        renderIngredients(currentIngredients);
    } catch (err) {
        container.innerHTML = '<div class="empty-state"><p>Failed to load ingredients</p></div>';
    }
}

function renderIngredients(ingredients) {
    const container = document.getElementById('ingredientList');
    container.innerHTML = '';

    if (!ingredients.length) {
        container.innerHTML = '<div class="empty-state"><p>No ingredients</p></div>';
        return;
    }

    ingredients.forEach((ing, index) => {
        const div = document.createElement('div');
        div.className = 'item mapping-ingredient';

        if (!ing.has_food) {
            div.classList.add('no-food');
        } else if (ing.is_mapped) {
            div.classList.add('mapped');
        } else {
            div.classList.add('unmapped');
        }

        // Left side: ingredient info
        const info = document.createElement('div');
        info.className = 'mapping-ingredient-info';

        const name = document.createElement('div');
        name.className = 'item-name';
        name.textContent = ing.food_name || ing.display;
        info.appendChild(name);

        const meta = document.createElement('div');
        meta.className = 'item-meta';
        const parts = [];
        if (ing.quantity) parts.push(ing.quantity + (ing.unit ? ' ' + ing.unit : ''));
        if (ing.note) parts.push(ing.note);
        if (parts.length) meta.textContent = parts.join(' · ');
        if (parts.length) info.appendChild(meta);

        div.appendChild(info);

        // Right side: mapping status / actions
        if (!ing.has_food) {
            const badge = document.createElement('span');
            badge.className = 'status-badge mapping-no-food-badge';
            badge.textContent = 'no food link';
            badge.title = 'This ingredient has no food link in Mealie — add one there first';
            div.appendChild(badge);
        } else if (ing.is_mapped) {
            const mapped = document.createElement('div');
            mapped.className = 'mapping-product';

            if (ing.picnic_image_url) {
                const thumb = document.createElement('img');
                thumb.className = 'cart-item-thumb';
                thumb.src = ing.picnic_image_url;
                thumb.loading = 'lazy';
                mapped.appendChild(thumb);
            }

            const prodName = document.createElement('span');
            prodName.className = 'mapping-product-name';
            prodName.textContent = ing.picnic_product_name;
            mapped.appendChild(prodName);

            const changeBtn = document.createElement('button');
            changeBtn.className = 'change-product-btn';
            changeBtn.textContent = 'wijzig';
            changeBtn.onclick = (e) => {
                e.stopPropagation();
                openProductModal(ing.food_id, ing.food_name || ing.display, null);
            };
            mapped.appendChild(changeBtn);

            const clearBtn = document.createElement('button');
            clearBtn.className = 'remove-product-btn';
            clearBtn.innerHTML = '&times;';
            clearBtn.title = 'Remove mapping';
            clearBtn.onclick = (e) => {
                e.stopPropagation();
                clearMapping(ing.food_id);
            };
            mapped.appendChild(clearBtn);

            div.appendChild(mapped);
        } else {
            const mapBtn = document.createElement('button');
            mapBtn.className = 'sync-btn mapping-map-btn';
            mapBtn.textContent = 'Map';
            mapBtn.onclick = (e) => {
                e.stopPropagation();
                openProductModal(ing.food_id, ing.food_name || ing.display, null);
            };
            div.appendChild(mapBtn);
        }

        container.appendChild(div);
    });

    // Update summary stats
    const mapped = ingredients.filter(i => i.is_mapped).length;
    const unmapped = ingredients.filter(i => i.has_food && !i.is_mapped).length;
    const noFood = ingredients.filter(i => !i.has_food).length;
    const parts = [mapped + ' mapped', unmapped + ' unmapped'];
    if (noFood) parts.push(noFood + ' without food link');
    document.getElementById('mappingSummary').textContent = parts.join(', ');
}

// === Clear Mapping ===
async function clearMapping(foodId) {
    try {
        await fetch('/foods/' + foodId + '/product', { method: 'DELETE' });
        if (selectedRecipeSlug) {
            await selectRecipe(selectedRecipeSlug);
        }
    } catch (err) {
        alert('Failed to clear mapping: ' + err.message);
    }
}
