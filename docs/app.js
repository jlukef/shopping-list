/* ============================================================
   Shopping List — Frontend Application
   Talks to a Google Apps Script web app (all via GET to avoid
   CORS issues with Apps Script's redirect behaviour).
   AI sorting (Claude) runs server-side in Apps Script —
   the API key is stored in Script Properties, never in the browser.
============================================================ */

'use strict';

// ── Config (persisted in localStorage) ──────────────────────
const CFG = {
  get scriptUrl()   { return localStorage.getItem('scriptUrl')   || ''; },
  get defaultShop() { return localStorage.getItem('defaultShop') || 'tesco'; },
  set scriptUrl(v)  { localStorage.setItem('scriptUrl',   v); },
  set defaultShop(v){ localStorage.setItem('defaultShop', v); },
};

// ── Application state ────────────────────────────────────────
const STATE = {
  items:              [],    // full list from API
  shops:              [],    // shop objects {id,name,color,emoji}
  layouts:            {},    // { shopId: [{shop,department,order,keywords}] }
  enabledShops:       [],    // shop IDs shown in create tab
  activeShopFilter:   null,  // shopping tab filter
  activeAddShop:      null,  // which shop's add row is currently open
  activeAddInputValue:'',    // preserved across re-renders
  acTimeout:          null,
  acSelected:         -1,
  acShop:             null,  // which shop's autocomplete is open
  loading:            false,
};

// ── DOM refs ─────────────────────────────────────────────────
const $  = id => document.getElementById(id);
const $$ = sel => document.querySelectorAll(sel);

// ── API helper ───────────────────────────────────────────────
async function api(action, data = {}) {
  const url = CFG.scriptUrl;
  if (!url) {
    toast('Set the Apps Script URL in ⚙ Settings first', 'warn');
    return null;
  }
  try {
    const params = new URLSearchParams({ action });
    if (Object.keys(data).length) params.set('data', JSON.stringify(data));
    const res = await fetch(`${url}?${params}`, { redirect: 'follow' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    if (json.error) { toast(json.error, 'error'); return null; }
    return json;
  } catch (e) {
    toast('API error: ' + e.message, 'error');
    return null;
  }
}

// ── Toast notification ───────────────────────────────────────
let toastTimer = null;
function toast(msg, type = 'info') {
  const el = $('toast');
  el.textContent = msg;
  el.className = `toast toast-${type}`;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 3500);
}

// ── Loading state ─────────────────────────────────────────────
function setLoading(on) {
  STATE.loading = on;
  document.body.style.cursor = on ? 'wait' : '';
}

// ════════════════════════════════════════════════════════════
// Data loading
// ════════════════════════════════════════════════════════════
async function loadAll() {
  setLoading(true);
  const [listRes, shopsRes] = await Promise.all([
    api('getList'),
    api('getShops'),
  ]);
  setLoading(false);
  if (listRes)  STATE.items = listRes.items || [];
  if (shopsRes) STATE.shops = shopsRes.shops || [];

  // Set default enabled shops (first 3) if nothing saved yet
  if (!STATE.enabledShops.length && STATE.shops.length) {
    const saved = localStorage.getItem('createEnabledShops');
    if (saved) {
      try { STATE.enabledShops = JSON.parse(saved); } catch (e) {}
    }
    if (!STATE.enabledShops.length) {
      STATE.enabledShops = STATE.shops.slice(0, 3).map(s => s.id);
    }
  }

  renderAll();
}

async function loadLayouts(shopId) {
  const res = await apiQ('getLayouts', { shop: shopId });
  if (res) STATE.layouts[shopId] = res.layouts || [];
  return STATE.layouts[shopId] || [];
}

// Override api() for query params that aren't in `data`
async function apiQ(action, queryExtra = {}) {
  const url = CFG.scriptUrl;
  if (!url) { toast('Set the Apps Script URL in ⚙ Settings first', 'warn'); return null; }
  try {
    const params = new URLSearchParams({ action, ...queryExtra });
    const res = await fetch(`${url}?${params}`, { redirect: 'follow' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    if (json.error) { toast(json.error, 'error'); return null; }
    return json;
  } catch (e) {
    toast('API error: ' + e.message, 'error');
    return null;
  }
}

// ════════════════════════════════════════════════════════════
// Render
// ════════════════════════════════════════════════════════════
function renderAll() {
  renderSettingSelects();
  renderCreateTab();
  renderShoppingList();
  renderSettingsShops();
  renderShopFilterChips();
}

/* ── Settings selects (no longer used in create tab) ────────── */
function renderSettingSelects() {
  const shops = STATE.shops;
  const defSel = $('defaultShopSelect');
  if (defSel) {
    const cur = defSel.value || CFG.defaultShop;
    defSel.innerHTML = shops.map(s =>
      `<option value="${s.id}" ${s.id === cur ? 'selected' : ''}>${s.emoji} ${s.name}</option>`
    ).join('');
  }
  const laySel = $('layoutShopSelect');
  if (laySel) {
    laySel.innerHTML = shops.map(s =>
      `<option value="${s.id}">${s.emoji} ${s.name}</option>`
    ).join('');
  }
}

/* ── Shopping tab filter chips ───────────────────────────── */
function renderShopFilterChips() {
  const container = $('shopFilterChips');
  container.innerHTML = STATE.shops.map(s => `
    <button class="chip${STATE.activeShopFilter === s.id ? ' active' : ''}"
            data-shop="${s.id}" style="${STATE.activeShopFilter === s.id ? `background:${s.color};border-color:${s.color}` : ''}">
      ${s.emoji} ${s.name}
    </button>
  `).join('');
  $('shopFilterAll').className = 'chip' + (STATE.activeShopFilter === null ? ' active' : '');
  container.querySelectorAll('.chip').forEach(btn => {
    btn.addEventListener('click', () => {
      STATE.activeShopFilter = btn.dataset.shop;
      renderShoppingList();
      renderShopFilterChips();
    });
  });
}

/* ════════════════════════════════════════════════════════════
   CREATE TAB — shop columns with inline add rows
══════════════════════════════════════════════════════════════ */
function renderCreateTab() {
  // Remember which add row was open so we can restore it
  const savedShop  = STATE.activeAddShop;
  const savedValue = STATE.activeAddInputValue;

  renderShopToggleChips();
  renderCreateSections();

  const total = STATE.items.length;
  $('createCount').textContent = `${total} item${total !== 1 ? 's' : ''}`;

  const empty = $('emptyCreate');
  if (STATE.enabledShops.length === 0) {
    empty.classList.remove('hidden');
  } else {
    empty.classList.add('hidden');
  }

  // Restore open add row (preserves state across re-renders)
  if (savedShop && STATE.enabledShops.includes(savedShop)) {
    openAddRow(savedShop, savedValue, false);
  }
}

function renderShopToggleChips() {
  const container = $('shopToggles');
  if (!container) return;
  container.innerHTML = STATE.shops.map(s => {
    const on = STATE.enabledShops.includes(s.id);
    const style = on ? `background:${s.color};border-color:${s.color};color:#fff` : '';
    return `<button class="shopToggleChip${on ? ' active' : ''}"
                    data-shop="${s.id}" style="${style}">
              ${s.emoji} ${esc(s.name)}
            </button>`;
  }).join('');
  container.querySelectorAll('.shopToggleChip').forEach(btn => {
    btn.addEventListener('click', () => toggleCreateShop(btn.dataset.shop));
  });
}

function toggleCreateShop(shopId) {
  const enabled = [...STATE.enabledShops];
  const idx = enabled.indexOf(shopId);
  if (idx >= 0) {
    enabled.splice(idx, 1);
    if (STATE.activeAddShop === shopId) {
      STATE.activeAddShop       = null;
      STATE.activeAddInputValue = '';
    }
  } else {
    enabled.push(shopId);
  }
  STATE.enabledShops = enabled;
  localStorage.setItem('createEnabledShops', JSON.stringify(enabled));

  // Animate columns sliding to their new positions
  if (document.startViewTransition) {
    document.startViewTransition(() => renderCreateTab());
  } else {
    renderCreateTab();
  }
}

function renderCreateSections() {
  const container = $('createSections');
  if (!container) return;
  const shopMap = shopColorMap();

  container.innerHTML = STATE.enabledShops.map(shopId => {
    const shop = shopMap[shopId];
    if (!shop) return '';
    const items = STATE.items.filter(i => i.shop === shopId);
    return renderShopSection(shop, items);
  }).join('');

  // Wire add-row placeholders
  container.querySelectorAll('.shopAddPlaceholder').forEach(el => {
    el.addEventListener('click', () => {
      openAddRow(el.closest('.shopAddRow').dataset.shop, '', true);
    });
  });

  // Wire delete buttons
  container.querySelectorAll('.itemRowDelete').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); removeItem(btn.dataset.id); });
  });
}

const UNIT_OPTIONS = `
  <option value="">—</option>
  <option value="g">g</option><option value="kg">kg</option>
  <option value="ml">ml</option><option value="L">L</option>
  <option value="pack">pack</option><option value="tin">tin</option>
  <option value="bottle">bottle</option><option value="bag">bag</option>
  <option value="box">box</option><option value="bunch">bunch</option>
  <option value="jar">jar</option><option value="tub">tub</option>
  <option value="loaf">loaf</option><option value="head">head</option>
  <option value="clove">clove</option><option value="tbsp">tbsp</option>
  <option value="tsp">tsp</option>`;

function renderShopSection(shop, items) {
  const itemsHtml = items.map(item => {
    const qty = formatQty(item.quantity, item.unit);
    return `<div class="itemRow${item._saving ? ' saving' : ''}" data-id="${item.id}">
      <span class="itemRowName">${esc(item.item)}</span>
      <span class="itemRowRight">
        ${qty ? `<span class="itemRowQty">${esc(qty)}</span>` : ''}
        ${item._saving
          ? '<span class="savingDot"></span>'
          : `<button class="itemRowDelete" data-id="${item.id}" title="Remove">✕</button>`}
      </span>
    </div>`;
  }).join('');

  return `
    <div class="shopSection" data-shop="${shop.id}"
         style="view-transition-name: shop-${shop.id}">
      <div class="shopSectionHeader" style="border-left:4px solid ${shop.color}">
        <span class="shopSectionTitle">${shop.emoji} ${esc(shop.name)}</span>
        ${items.length ? `<span class="shopSectionCount">${items.length}</span>` : ''}
      </div>
      <div class="sectionItems" id="sectionItems_${shop.id}">${itemsHtml}</div>
      <div class="shopAddRow" data-shop="${shop.id}">
        <div class="shopAddPlaceholder">
          <span class="shopAddIcon">＋</span>
          <span class="shopAddText">Add item…</span>
        </div>
        <div class="shopAddForm hidden" id="addForm_${shop.id}">
          <div class="shopAddInputWrap">
            <input type="text" class="shopAddInput" id="addInput_${shop.id}"
                   placeholder="Item name…" autocomplete="off" autocorrect="off" spellcheck="false">
            <ul class="shopAddAcList hidden" id="addAc_${shop.id}"></ul>
          </div>
          <input type="number" class="shopAddQty" id="addQty_${shop.id}" value="1" min="0.5" step="0.5">
          <select class="shopAddUnit" id="addUnit_${shop.id}">${UNIT_OPTIONS}</select>
        </div>
      </div>
    </div>`;
}

/* ── Inline add-row logic ─────────────────────────────────── */
function openAddRow(shopId, initialValue = '', doFocus = true) {
  // Close any other open add row first
  STATE.enabledShops.forEach(id => {
    if (id !== shopId) _closeAddRowDOM(id);
  });

  const form = $(`addForm_${shopId}`);
  const ph   = form?.closest('.shopAddRow')?.querySelector('.shopAddPlaceholder');
  const inp  = $(`addInput_${shopId}`);
  if (!form || !inp) return;

  STATE.activeAddShop = shopId;
  ph?.classList.add('hidden');
  form.classList.remove('hidden');

  if (initialValue && inp.value !== initialValue) {
    inp.value = initialValue;
    inp.setSelectionRange(inp.value.length, inp.value.length);
  }
  if (doFocus) inp.focus();

  // Attach handlers (idempotent — replace previous)
  inp.onkeydown = e => handleAddKey(e, shopId);
  inp.oninput   = () => {
    STATE.activeAddInputValue = inp.value;
    clearTimeout(STATE.acTimeout);
    STATE.acTimeout = setTimeout(() => fetchAddAc(shopId), 220);
  };
  inp.onblur    = () => {
    // Slight delay so mousedown on AC item fires first
    setTimeout(() => {
      if (STATE.acShop !== shopId) _closeAddRowDOM(shopId);
    }, 180);
  };
}

function _closeAddRowDOM(shopId) {
  const form = $(`addForm_${shopId}`);
  const ph   = form?.closest('.shopAddRow')?.querySelector('.shopAddPlaceholder');
  if (!form) return;
  hideAddAc(shopId);
  form.classList.add('hidden');
  ph?.classList.remove('hidden');
  if (STATE.activeAddShop === shopId) {
    STATE.activeAddShop       = null;
    STATE.activeAddInputValue = '';
  }
}

function handleAddKey(e, shopId) {
  const list  = $(`addAc_${shopId}`);
  const items = list ? list.querySelectorAll('li') : [];

  if (e.key === 'ArrowDown') { e.preventDefault(); STATE.acSelected = Math.min(STATE.acSelected + 1, items.length - 1); highlightAddAc(items); return; }
  if (e.key === 'ArrowUp')   { e.preventDefault(); STATE.acSelected = Math.max(STATE.acSelected - 1, -1); highlightAddAc(items); return; }
  if (e.key === 'Enter') {
    if (STATE.acSelected >= 0 && items[STATE.acSelected]) selectAddAcItem(shopId, items[STATE.acSelected]);
    else commitAdd(shopId);
    return;
  }
  if (e.key === 'Escape') { _closeAddRowDOM(shopId); return; }
}

function commitAdd(shopId) {
  const inp  = $(`addInput_${shopId}`);
  const qty  = $(`addQty_${shopId}`);
  const unit = $(`addUnit_${shopId}`);
  const name = inp?.value.trim();
  if (!name) { _closeAddRowDOM(shopId); return; }

  const item = {
    item:     name,
    quantity: parseFloat(qty?.value) || 1,
    unit:     unit?.value || '',
    shop:     shopId,
    notes:    '',
  };

  // Reset input for next entry, keep add row open
  inp.value   = '';
  if (qty)  qty.value  = '1';
  if (unit) unit.value = '';
  hideAddAc(shopId);
  STATE.activeAddInputValue = '';
  inp.focus();

  // Optimistic insert into DOM
  const tempId  = 'temp_' + Date.now();
  const tempObj = { ...item, id: tempId, bought: false, sortOrder: 999, _saving: true };
  STATE.items.push(tempObj);

  const section = $(`sectionItems_${shopId}`);
  if (section) {
    const div = document.createElement('div');
    div.className  = 'itemRow saving';
    div.dataset.id = tempId;
    const qtyStr = formatQty(item.quantity, item.unit);
    div.innerHTML  = `
      <span class="itemRowName">${esc(name)}</span>
      <span class="itemRowRight">
        ${qtyStr ? `<span class="itemRowQty">${esc(qtyStr)}</span>` : ''}
        <span class="savingDot"></span>
      </span>`;
    section.appendChild(div);
    // Update badge
    const badge = section.closest('.shopSection')?.querySelector('.shopSectionCount');
    if (badge) badge.textContent = section.children.length;
  }

  // Update total count
  const total = STATE.items.length;
  $('createCount').textContent = `${total} item${total !== 1 ? 's' : ''}`;

  // Persist
  api('addItem', item).then(res => {
    const entry = STATE.items.find(i => i.id === tempId);
    if (res && entry) {
      entry.id      = res.id;
      entry._saving = false;
      const el = document.querySelector(`.itemRow[data-id="${tempId}"]`);
      if (el) {
        el.dataset.id = res.id;
        el.classList.remove('saving');
        const dot = el.querySelector('.savingDot');
        if (dot) {
          const btn = document.createElement('button');
          btn.className  = 'itemRowDelete';
          btn.dataset.id = res.id;
          btn.title      = 'Remove';
          btn.textContent = '✕';
          btn.addEventListener('click', ev => { ev.stopPropagation(); removeItem(res.id); });
          dot.replaceWith(btn);
        }
      }
    } else if (!res) {
      STATE.items = STATE.items.filter(i => i.id !== tempId);
      document.querySelector(`.itemRow[data-id="${tempId}"]`)?.remove();
      $('createCount').textContent = `${STATE.items.length} item${STATE.items.length !== 1 ? 's' : ''}`;
    }
  });
}

/* ── Add-row autocomplete ─────────────────────────────────── */
async function fetchAddAc(shopId) {
  const inp = $(`addInput_${shopId}`);
  const q   = inp?.value.trim();
  if (!q || !CFG.scriptUrl) { hideAddAc(shopId); return; }
  const res = await apiQ('getAutocomplete', { q });
  if (!res || !res.items.length) { hideAddAc(shopId); return; }
  showAddAc(shopId, res.items);
}

function showAddAc(shopId, items) {
  const list = $(`addAc_${shopId}`);
  if (!list) return;
  const shopMap2 = shopColorMap();
  STATE.acSelected = -1;
  STATE.acShop = shopId;

  list.innerHTML = items.map(item => {
    const s   = item.defaultShop ? shopMap2[item.defaultShop] : null;
    const qty = formatQty(item.defaultQty, item.defaultUnit);
    return `<li data-item='${JSON.stringify(item).replace(/'/g, '&#39;')}'>
      <span class="acItem">${esc(item.item)}</span>
      <span class="acMeta">
        ${qty ? `<span>${esc(qty)}</span>` : ''}
        ${s ? `<span class="acShopTag">${s.emoji} ${esc(s.name)}</span>` : ''}
      </span>
    </li>`;
  }).join('');

  list.querySelectorAll('li').forEach(li => {
    li.addEventListener('mousedown', e => { e.preventDefault(); selectAddAcItem(shopId, li); });
  });
  list.classList.remove('hidden');
}

function hideAddAc(shopId) {
  const list = $(`addAc_${shopId}`);
  if (list) { list.classList.add('hidden'); list.innerHTML = ''; }
  if (STATE.acShop === shopId) { STATE.acShop = null; STATE.acSelected = -1; }
}

function selectAddAcItem(shopId, li) {
  const item = JSON.parse(li.dataset.item);
  const inp  = $(`addInput_${shopId}`);
  const qty  = $(`addQty_${shopId}`);
  const unit = $(`addUnit_${shopId}`);
  if (inp)  inp.value  = item.item;
  if (qty  && item.defaultQty)  qty.value  = item.defaultQty;
  if (unit && item.defaultUnit) unit.value = item.defaultUnit;
  STATE.activeAddInputValue = item.item;
  hideAddAc(shopId);
  inp?.focus();
}

function highlightAddAc(items) {
  items.forEach((el, i) => el.classList.toggle('selected', i === STATE.acSelected));
  if (STATE.acSelected >= 0) items[STATE.acSelected].scrollIntoView({ block: 'nearest' });
}

/* ── Shopping tab list ────────────────────────────────────── */
function renderShoppingList() {
  const container  = $('shoppingList');
  const emptyEl    = $('emptyShop');
  const shopMap    = shopColorMap();
  const activeShop = STATE.activeShopFilter;

  // Filter items
  let items = STATE.items.filter(i => !activeShop || i.shop === activeShop);

  // Sort: sort order → alphabetical within shop groups
  items = [...items].sort((a, b) => {
    if (a.shop !== b.shop) {
      const ai = STATE.shops.findIndex(s => s.id === a.shop);
      const bi = STATE.shops.findIndex(s => s.id === b.shop);
      return ai - bi;
    }
    const ao = a.sortOrder ?? 999;
    const bo = b.sortOrder ?? 999;
    if (ao !== bo) return ao - bo;
    return a.item.localeCompare(b.item);
  });

  if (!items.length) {
    container.innerHTML = '';
    emptyEl.classList.remove('hidden');
    updateProgressBar(items);
    return;
  }
  emptyEl.classList.add('hidden');
  updateProgressBar(items);

  // Group by shop
  const groups = {};
  const order  = [];
  items.forEach(item => {
    if (!groups[item.shop]) { groups[item.shop] = []; order.push(item.shop); }
    groups[item.shop].push(item);
  });

  container.innerHTML = order.map(shopId => {
    const shop       = shopMap[shopId] || { id: shopId, name: shopId, color: '#888', emoji: '🏪' };
    const shopItems  = groups[shopId];
    const boughtCnt  = shopItems.filter(i => i.bought).length;
    const totalCnt   = shopItems.length;

    return `
      <div class="shopGroup" data-shop="${shopId}">
        <div class="shopGroupHeader">
          <span class="shopGroupTitle">
            <span>${shop.emoji}</span>
            <span>${esc(shop.name)}</span>
          </span>
          <span class="shopGroupProgress">${boughtCnt}/${totalCnt}</span>
        </div>
        <div class="shopItems">
          ${shopItems.map(item => {
            const qty = formatQty(item.quantity, item.unit);
            return `
              <div class="shopItem${item.bought ? ' bought' : ''}" data-id="${item.id}">
                <div class="checkCircle">${item.bought ? '✓' : ''}</div>
                <div class="shopItemInfo">
                  <div class="shopItemName">${esc(item.item)}</div>
                  ${qty ? `<div class="shopItemQty">${esc(qty)}</div>` : ''}
                  ${item.notes ? `<div class="shopItemNotes">${esc(item.notes)}</div>` : ''}
                </div>
              </div>`;
          }).join('')}
        </div>
      </div>`;
  }).join('');

  // Click to toggle bought
  container.querySelectorAll('.shopItem').forEach(el => {
    el.addEventListener('click', () => toggleBought(el.dataset.id));
  });
}

function updateProgressBar(items) {
  const bar    = $('progressBar');
  const fill   = $('progressFill');
  const label  = $('progressLabel');
  if (!items.length) { bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');
  const done  = items.filter(i => i.bought).length;
  const total = items.length;
  const pct   = Math.round((done / total) * 100);
  fill.style.width  = pct + '%';
  label.textContent = `${done} / ${total}`;
}

/* ── Settings modal shops list ───────────────────────────── */
function renderSettingsShops() {
  const list = $('shopsList');
  list.innerHTML = STATE.shops.map(s => `
    <li class="shopManageItem" data-id="${s.id}">
      <span class="shopManageEmoji">${s.emoji}</span>
      <span class="shopManageName">${esc(s.name)}</span>
      <span class="shopManageDot" style="background:${s.color}"></span>
      <button class="shopManageDel" data-id="${s.id}" title="Remove shop">✕</button>
    </li>
  `).join('');
  list.querySelectorAll('.shopManageDel').forEach(btn => {
    btn.addEventListener('click', () => removeShop(btn.dataset.id));
  });
}

// ════════════════════════════════════════════════════════════
// Actions — List
// ════════════════════════════════════════════════════════════

async function removeItem(id) {
  const removed = STATE.items.find(i => i.id === id);
  const idx     = STATE.items.indexOf(removed);

  // Remove from STATE and DOM immediately
  STATE.items = STATE.items.filter(i => i.id !== id);
  document.querySelector(`.itemRow[data-id="${id}"]`)?.remove();
  renderShoppingList();
  // Update count + badge
  const total = STATE.items.length;
  $('createCount').textContent = `${total} item${total !== 1 ? 's' : ''}`;
  if (removed) {
    const badge = document.querySelector(`#sectionItems_${removed.shop}`)
                    ?.closest('.shopSection')?.querySelector('.shopSectionCount');
    if (badge) {
      const cnt = STATE.items.filter(i => i.shop === removed.shop).length;
      badge.textContent = cnt || '';
    }
  }

  const res = await api('deleteItem', { id });
  if (!res && removed) {
    STATE.items.splice(idx, 0, removed);
    renderCreateTab();
    renderShoppingList();
  }
}

async function toggleBought(id) {
  const item = STATE.items.find(i => i.id === id);
  if (!item) return;
  const newVal = !item.bought;
  item.bought = newVal;
  renderShoppingList();
  const res = await api('updateItem', { id, bought: newVal });
  if (!res) { item.bought = !newVal; renderShoppingList(); }
}

async function clearBought() {
  if (!confirm('Remove all ✓ bought items from the list?')) return;
  const res = await api('clearBought');
  if (!res) return;
  STATE.items = STATE.items.filter(i => !i.bought);
  renderCreateTab();
  renderShoppingList();
  toast('Bought items cleared', 'success');
}

async function clearList() {
  if (!confirm('Clear the entire shopping list?')) return;
  const res = await api('clearList');
  if (!res) return;
  STATE.items = [];
  renderCreateTab();
  renderShoppingList();
  toast('List cleared', 'success');
}

// ════════════════════════════════════════════════════════════
// Actions — Shops
// ════════════════════════════════════════════════════════════
async function addShop() {
  const name  = $('newShopName').value.trim();
  const emoji = $('newShopEmoji').value.trim() || '🏪';
  const color = $('newShopColor').value;
  if (!name) return;

  const res = await api('addShop', { name, emoji, color });
  if (!res) return;
  STATE.shops.push({ id: res.id, name, emoji, color });
  renderAll();
  $('newShopName').value  = '';
  $('newShopEmoji').value = '';
  toast(`${emoji} ${name} added`, 'success');
}

async function removeShop(id) {
  const shop = STATE.shops.find(s => s.id === id);
  if (!shop) return;
  if (!confirm(`Remove "${shop.name}"?`)) return;
  const res = await api('deleteShop', { id });
  if (!res) return;
  STATE.shops = STATE.shops.filter(s => s.id !== id);
  renderAll();
}

// ════════════════════════════════════════════════════════════
// AI Sorting — via Claude (server-side in Apps Script)
// ════════════════════════════════════════════════════════════
function openSortModal() {
  const shops = STATE.shops.filter(s =>
    STATE.items.some(i => i.shop === s.id && !i.bought)
  );
  if (!shops.length) { toast('No unbought items to sort', 'warn'); return; }

  if (shops.length === 1) {
    doSort(shops[0].id);
    return;
  }

  // Show shop picker
  const list = $('sortShopList');
  list.innerHTML = shops.map(s => `
    <li>
      <button class="shopPickItem" data-shop="${s.id}">
        <span class="shopPickEmoji">${s.emoji}</span>
        <span class="shopPickName">${esc(s.name)}</span>
      </button>
    </li>
  `).join('');
  list.querySelectorAll('.shopPickItem').forEach(btn => {
    btn.addEventListener('click', () => {
      closeSortModal();
      doSort(btn.dataset.shop);
    });
  });

  $('sortModal').classList.remove('hidden');
}

function closeSortModal() {
  $('sortModal').classList.add('hidden');
}

async function doSort(shopId) {
  const items = STATE.items.filter(i => i.shop === shopId && !i.bought);
  if (!items.length) { toast('No unbought items for this shop', 'warn'); return; }

  const shop = STATE.shops.find(s => s.id === shopId);
  toast(`🤖 Asking Claude to sort ${shop ? shop.name : shopId}…`);

  // Send to Apps Script — Claude runs server-side with the stored API key
  const res = await api('sortList', {
    items: items.map(i => ({ id: i.id, item: i.item })),
    shop:  shopId
  });

  if (!res) return;

  const method = res.method || 'unknown';
  applySortOrder(res.items || []);

  if (method === 'claude')   toast('✅ Sorted by Claude AI!', 'success');
  else if (method === 'keywords') toast('Sorted by aisle keywords (add Claude key in Settings for AI)', 'info');
  else toast('✅ Sorted!', 'success');
}

function applySortOrder(sortedItems) {
  sortedItems.forEach((item, idx) => {
    const found = STATE.items.find(i => i.id === item.id);
    if (found) found.sortOrder = idx;
    api('updateItem', { id: item.id, sortOrder: idx }); // persist async
  });
  renderShoppingList();
}

// ════════════════════════════════════════════════════════════
// Settings modal
// ════════════════════════════════════════════════════════════
function openSettings() {
  $('scriptUrlInput').value = CFG.scriptUrl;
  $('claudeKeyInput').value = '';
  $('claudeKeyStatus').textContent = '';
  $('setupStatus').textContent     = '';

  // Set default shop select if it's populated; otherwise leave it
  const defSel = $('defaultShopSelect');
  if (defSel.options.length) defSel.value = CFG.defaultShop;

  // Show modal immediately — don't await anything first
  $('settingsModal').classList.remove('hidden');

  // Then fetch key status in the background
  if (CFG.scriptUrl) {
    $('claudeKeyStatus').textContent = '⏳ Checking…';
    apiQ('getApiKeySet').then(res => {
      if (res) {
        $('claudeKeyStatus').textContent = res.set
          ? `✅ Key saved (${res.preview})`
          : '⚠️ No Claude key saved yet';
      } else {
        $('claudeKeyStatus').textContent = '';
      }
    });
  }
}

function closeSettings() {
  $('settingsModal').classList.add('hidden');
}

async function saveSettings() {
  const url = $('scriptUrlInput').value.trim();
  CFG.scriptUrl   = url;
  CFG.defaultShop = $('defaultShopSelect').value;

  // Save Claude API key to Script Properties if one was entered
  const claudeKey = $('claudeKeyInput').value.trim();
  if (claudeKey && url) {
    const keyRes = await api('saveApiKey', { claudeKey });
    if (keyRes) {
      $('claudeKeyStatus').textContent = '✅ Key saved to server';
      $('claudeKeyInput').value = '';
    }
  }

  closeSettings();
  toast('Settings saved ✓', 'success');
  if (CFG.scriptUrl) loadAll();
}

async function testConnection() {
  const url = $('scriptUrlInput').value.trim();
  if (!url) { toast('Enter a URL first', 'warn'); return; }
  const old = CFG.scriptUrl;
  CFG.scriptUrl = url;
  const res = await api('getShops');
  CFG.scriptUrl = old;
  if (res) toast(`✅ Connected — ${res.shops.length} shop(s) found`, 'success');
}

async function runSetup() {
  const url = $('scriptUrlInput').value.trim();
  if (!url) { toast('Enter a URL first', 'warn'); return; }
  const btn = $('runSetupBtn');
  btn.disabled    = true;
  btn.textContent = 'Setting up…';
  CFG.scriptUrl   = url;
  const res = await api('setup');
  btn.disabled    = false;
  btn.textContent = 'Run setup';
  if (res) {
    // Show feedback inside the modal (toast is hidden behind backdrop)
    $('setupStatus').textContent = '✅ Setup complete! Shops and layouts ready.';
    $('setupStatus').style.color = '#2e7d32';
    loadAll();
  }
}

// ── Layout editor ─────────────────────────────────────────────
async function loadLayout() {
  const shopId = $('layoutShopSelect').value;
  if (!shopId) return;
  if (!STATE.layouts[shopId]) await loadLayouts(shopId);
  const layouts = STATE.layouts[shopId] || [];
  $('layoutEditor').value = layouts
    .sort((a, b) => Number(a.order) - Number(b.order))
    .map(l => `${l.department} | ${l.keywords}`)
    .join('\n');
}

async function saveLayout() {
  const shopId = $('layoutShopSelect').value;
  if (!shopId) return;
  const lines = $('layoutEditor').value.split('\n').filter(l => l.trim());
  const departments = lines.map((line, i) => {
    const [name, kw] = line.split('|').map(s => s.trim());
    return { name: name || `Section ${i+1}`, order: i + 1, keywords: kw || '' };
  });
  const res = await api('saveLayout', { shop: shopId, departments });
  if (res) {
    STATE.layouts[shopId] = departments.map(d => ({ shop: shopId, ...d }));
    toast(`Layout saved for ${shopId} ✓`, 'success');
  }
}

// ════════════════════════════════════════════════════════════
// Utilities
// ════════════════════════════════════════════════════════════
function shopColorMap() {
  const m = {};
  STATE.shops.forEach(s => m[s.id] = s);
  return m;
}

function formatQty(qty, unit) {
  if (!qty && qty !== 0) return '';
  return unit ? `${qty} ${unit}` : `× ${qty}`;
}

function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ════════════════════════════════════════════════════════════
// Tab switching
// ════════════════════════════════════════════════════════════
function switchTab(tabId) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabId));
  $$('.tabContent').forEach(c => c.classList.toggle('active', c.id === tabId + 'Tab'));
}

// ════════════════════════════════════════════════════════════
// Event wiring
// ════════════════════════════════════════════════════════════
function wire() {
  // Tabs
  $$('.tab').forEach(tab => tab.addEventListener('click', () => switchTab(tab.dataset.tab)));

  // Header buttons
  $('refreshBtn').addEventListener('click', loadAll);

  // List management
  $('clearListBtn').addEventListener('click', clearList);

  // Shopping tab
  $('sortBtn').addEventListener('click', openSortModal);
  $('clearBoughtBtn').addEventListener('click', clearBought);
  $('shopFilterAll').addEventListener('click', () => {
    STATE.activeShopFilter = null;
    renderShoppingList();
    renderShopFilterChips();
  });

  // Settings modal
  $('settingsBtn').addEventListener('click', openSettings);
  $('closeSettings').addEventListener('click', closeSettings);
  $('modalBackdrop').addEventListener('click', closeSettings);
  $('testConnectionBtn').addEventListener('click', testConnection);
  $('runSetupBtn').addEventListener('click', runSetup);
  $('saveSettingsBtn').addEventListener('click', saveSettings);
  $('addShopBtn').addEventListener('click', addShop);
  $('loadLayoutBtn').addEventListener('click', loadLayout);
  $('saveLayoutBtn').addEventListener('click', saveLayout);

  // Sort modal
  $('closeSortModal').addEventListener('click', closeSortModal);
  $('sortModalBackdrop').addEventListener('click', closeSortModal);
}

// ════════════════════════════════════════════════════════════
// Init
// ════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
  wire();
  if (CFG.scriptUrl) {
    loadAll();
  } else {
    toast('Welcome! Open ⚙ Settings to connect your Google Sheet.', 'info');
    renderAll(); // render empty state
  }
});
