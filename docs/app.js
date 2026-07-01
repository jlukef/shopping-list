/* ============================================================
   Shopping List — Frontend Application
   Talks to a Google Apps Script web app (all via GET to avoid
   CORS issues with Apps Script's redirect behaviour).
   AI sorting (Claude) runs server-side in Apps Script —
   the API key is stored in Script Properties, never in the browser.
============================================================ */

'use strict';

console.log('[ShoppingList] app.js v2 — drag handles build');

// ── Config (persisted in localStorage) ──────────────────────
// True when the page is being served by the FastAPI host (sharedlist.co.uk or
// the local 127.0.0.1:8770 / localhost:8770 dev port) rather than from legacy
// GitHub Pages or a plain static preview server.
// Used to default the API to same-origin /api and to reveal the logout button.
function isHostedMode() {
  const host = location.hostname.toLowerCase();
  return (
    host === 'sharedlist.co.uk' ||
    host === 'www.sharedlist.co.uk' ||
    ((host === '127.0.0.1' || host === 'localhost') && location.port === '8770')
  );
}

const DEFAULT_API_URL = isHostedMode() ? '/api' : '';

const CFG = {
  get scriptUrl()   { return localStorage.getItem('scriptUrl')   || ''; },
  // Hosted mode must always use the authenticated same-origin backend. A stale
  // legacy Apps Script URL in localStorage must never bypass /api.
  get apiUrl()      { return DEFAULT_API_URL || this.scriptUrl; },
  get defaultShop() { return localStorage.getItem('defaultShop') || 'morrisons'; },
  set scriptUrl(v)  { localStorage.setItem('scriptUrl',   v); },
  set defaultShop(v){ localStorage.setItem('defaultShop', v); },
};

function apiConfigured() {
  return Boolean(CFG.apiUrl);
}

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
  receipts:           [],    // receipt summaries for the Receipts tab
  activeReceiptId:    null,  // receipt currently open in the review card, if any
  activeReceiptData:  null,
  activeReceiptFile:  null,  // the File last uploaded/retried — kept in memory only, for Retry
  activeReceiptFileFor: null, // which receipt id activeReceiptFile belongs to
  receiptPatchPromise: Promise.resolve(), // serialize shop/date edits before accept
  receiptAiOptions:   [],    // [{alias,label,provider}] from /api/receipt-ai/options
  receiptAiSelected:  'auto',
  historyTrips:       [],
  activeHistoryTrip:  null,
  historyPatchPromise: Promise.resolve(),
  products:           [],    // catalog + live purchase stats from /api/products
  productSearch:      '',
  productSelection:   new Set(),  // product ids ticked for merging
};

// ── Sortable instances (keyed so we can destroy on re-render) ─
const SORTABLES = {};

// ── DOM refs ─────────────────────────────────────────────────
const $  = id => document.getElementById(id);
const $$ = sel => document.querySelectorAll(sel);

// ── API helper ───────────────────────────────────────────────
async function api(action, data = {}) {
  const url = CFG.apiUrl;
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

  // Apply saved drag-order before first render
  applySavedOrder();

  // Set default enabled shops (first 3) if nothing saved yet
  if (!STATE.enabledShops.length && STATE.shops.length) {
    const saved = localStorage.getItem('createEnabledShops');
    if (saved) {
      try {
        const validIds = new Set(STATE.shops.map(s => s.id));
        STATE.enabledShops = JSON.parse(saved).filter(id => validIds.has(id));
      } catch (e) {}
    }
    if (!STATE.enabledShops.length) {
      STATE.enabledShops = STATE.shops.slice(0, 3).map(s => s.id);
    }
  }

  renderAll();
}

/* ── Shop ordering — persisted in localStorage ───────────── */
function applySavedOrder() {
  const saved = localStorage.getItem('shopOrder');
  if (!saved) return;
  try {
    const ids = JSON.parse(saved);
    STATE.shops.sort((a, b) => {
      const ai = ids.indexOf(a.id);
      const bi = ids.indexOf(b.id);
      if (ai === -1 && bi === -1) return 0;
      if (ai === -1) return 1;   // new shops go to the end
      if (bi === -1) return -1;
      return ai - bi;
    });
  } catch (e) { /* ignore */ }
}

function reorderShops(orderedSubsetIds) {
  // Rearrange STATE.shops so the dragged subset occupies the same index
  // slots they held before the drag, now in their new order.
  // Non-dragged shops stay exactly where they were.
  const subsetSet = new Set(orderedSubsetIds);
  const slots = [];
  STATE.shops.forEach((s, i) => { if (subsetSet.has(s.id)) slots.push(i); });

  const newShops = [...STATE.shops];
  orderedSubsetIds.forEach((id, j) => {
    const shop = STATE.shops.find(s => s.id === id);
    if (shop) newShops[slots[j]] = shop;
  });

  STATE.shops = newShops;
  localStorage.setItem('shopOrder', JSON.stringify(STATE.shops.map(s => s.id)));
  renderAll();
}

function reorderItems(orderedIds, shopId) {
  // Map id → new sortOrder index
  const newOrder = new Map(orderedIds.map((id, i) => [id, i]));

  // Update sortOrder on STATE items
  STATE.items.forEach(item => {
    if (newOrder.has(item.id)) item.sortOrder = newOrder.get(item.id);
  });

  // Reposition this shop's items within STATE.items so create-tab
  // re-renders preserve the visual order (slot-preserving swap)
  const slots = [];
  STATE.items.forEach((item, i) => { if (item.shop === shopId) slots.push(i); });
  const reordered = orderedIds.map(id => STATE.items.find(i => i.id === id)).filter(Boolean);
  reordered.forEach((item, j) => { STATE.items[slots[j]] = item; });

  // Persist async (skip temp items that haven't been saved yet)
  orderedIds.forEach((id, idx) => {
    if (!id.startsWith('temp_')) api('updateItem', { id, sortOrder: idx });
  });

  renderShoppingList(); // re-sort shopping tab with new sortOrders
}

async function loadLayouts(shopId) {
  const res = await apiQ('getLayouts', { shop: shopId });
  if (res) STATE.layouts[shopId] = res.layouts || [];
  return STATE.layouts[shopId] || [];
}

// Override api() for query params that aren't in `data`
async function apiQ(action, queryExtra = {}) {
  const url = CFG.apiUrl;
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
  initAllSortables();
}

/* ── Settings selects (no longer used in create tab) ────────── */
function renderSettingSelects() {
  const shops = STATE.shops;
  const defSel = $('defaultShopSelect');
  if (defSel) {
    const requested = defSel.value || CFG.defaultShop;
    const cur = shops.some(s => s.id === requested) ? requested : shops[0]?.id;
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
    const t = document.startViewTransition(() => renderCreateTab());
    // Re-init sortables after the new DOM is in place
    t.finished.then(() => initAllSortables());
  } else {
    renderCreateTab();
    initAllSortables();
  }
}

function renderCreateSections() {
  const container = $('createSections');
  if (!container) return;
  const enabledSet = new Set(STATE.enabledShops);

  // Iterate STATE.shops in order so the columns respect the user's drag order
  container.innerHTML = STATE.shops
    .filter(s => enabledSet.has(s.id))
    .map(shop => {
      const items = STATE.items.filter(i => i.shop === shop.id);
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

  // Wire inline name editing — click item name to edit in place
  container.querySelectorAll('.itemRowName').forEach(span => {
    span.addEventListener('click', e => {
      e.stopPropagation();
      const row = span.closest('.itemRow');
      const id  = row?.dataset.id;
      if (!id || id.startsWith('temp_') || row.classList.contains('saving')) return;
      editItemName(span, id);
    });
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
      <span class="itemDragHandle"></span>
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
        <span class="dragHandle" title="Drag to reorder"></span>
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
      <span class="itemDragHandle"></span>
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
  if (!q || !apiConfigured()) { hideAddAc(shopId); return; }
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
          <span class="dragHandle" title="Drag to reorder"></span>
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
                <span class="itemDragHandle"></span>
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

  // Click to toggle bought (ignore clicks on the drag handle)
  container.querySelectorAll('.shopItem').forEach(el => {
    el.addEventListener('click', e => {
      if (e.target.closest('.itemDragHandle')) return;
      toggleBought(el.dataset.id);
    });
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
      <span class="dragHandle" title="Drag to reorder"></span>
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

  // Then fetch key status in the background (legacy mode only — the AI-key
  // section is hidden in hosted mode, which uses local sorting).
  if (apiConfigured() && !isHostedMode()) {
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
  if (claudeKey && apiConfigured()) {
    const keyRes = await api('saveApiKey', { claudeKey });
    if (keyRes) {
      $('claudeKeyStatus').textContent = '✅ Key saved to server';
      $('claudeKeyInput').value = '';
    }
  }

  closeSettings();
  toast('Settings saved ✓', 'success');
  if (apiConfigured()) loadAll();
}

async function testConnection() {
  const url = $('scriptUrlInput').value.trim();
  const old = CFG.scriptUrl;
  CFG.scriptUrl = url;
  const res = await api('getShops');
  CFG.scriptUrl = old;
  if (res) toast(`✅ Connected — ${res.shops.length} shop(s) found`, 'success');
}

async function runSetup() {
  const url = $('scriptUrlInput').value.trim();
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
// Draggable department rows. The DOM is the source of truth for names,
// keywords and order; renders only happen on load/add/remove/drag so
// in-progress typing is never thrown away.
async function loadLayout() {
  const shopId = $('layoutShopSelect').value;
  if (!shopId) { $('layoutRows').innerHTML = ''; return; }
  await loadLayouts(shopId); // always refetch — another device may have edited
  const layouts = (STATE.layouts[shopId] || [])
    .slice()
    .sort((a, b) => Number(a.order) - Number(b.order));
  renderLayoutRows(layouts.map(l => ({ name: l.department, keywords: l.keywords || '' })));
}

function readLayoutRowsFromDom() {
  return [...$('layoutRows').children].map(li => ({
    name: li.querySelector('.layoutName').value,
    keywords: li.querySelector('.layoutKeywords').value,
  }));
}

function renderLayoutRows(rows) {
  $('layoutRows').innerHTML = rows.map((d, i) => `
    <li class="layoutRow">
      <span class="layoutDragHandle" title="Drag to reorder">⠿</span>
      <span class="layoutFields">
        <input class="layoutName" value="${esc(d.name)}" placeholder="Department" aria-label="Department name">
        <input class="layoutKeywords" value="${esc(d.keywords)}" placeholder="keywords, comma, separated" aria-label="Department keywords">
      </span>
      <button class="rowDel" type="button" title="Remove department" onclick="removeLayoutRow(${i})">✕</button>
    </li>`).join('');
  if (SORTABLES.layoutEditor) SORTABLES.layoutEditor.destroy();
  SORTABLES.layoutEditor = new Sortable($('layoutRows'), {
    handle: '.layoutDragHandle',
    animation: 150,
    onEnd: () => renderLayoutRows(readLayoutRowsFromDom()),
  });
}

function addLayoutRow() {
  const rows = readLayoutRowsFromDom();
  rows.push({ name: '', keywords: '' });
  renderLayoutRows(rows);
  const items = $('layoutRows').children;
  items[items.length - 1].querySelector('.layoutName').focus();
}

function removeLayoutRow(idx) {
  const rows = readLayoutRowsFromDom();
  rows.splice(idx, 1);
  renderLayoutRows(rows);
}

async function saveLayout() {
  const shopId = $('layoutShopSelect').value;
  if (!shopId) { toast('Choose a shop first', 'warn'); return; }
  const departments = readLayoutRowsFromDom()
    .filter(d => d.name.trim())
    .map((d, i) => ({ name: d.name.trim(), order: i + 1, keywords: d.keywords.trim() }));
  if (!departments.length) { toast('Add at least one department', 'warn'); return; }
  const res = await api('saveLayout', { shop: shopId, departments });
  if (res) {
    STATE.layouts[shopId] = departments.map(d => ({ shop: shopId, department: d.name, ...d }));
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
  // Sortable can't measure elements that are inside display:none.
  // Re-init after the tab becomes visible so hit-detection works correctly.
  if (tabId === 'shop') requestAnimationFrame(() => initAllSortables());
  if (tabId === 'receipts' && isHostedMode()) loadReceipts();
}

// Receipts tab — [Receipts | History] segmented control (client-side only).
function switchSegment(viewId) {
  $$('.segment').forEach(s => {
    const on = s.dataset.segment === viewId;
    s.classList.toggle('active', on);
    s.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  $$('.segmentView').forEach(v => v.classList.toggle('active', v.id === viewId));
  if (viewId === 'historyView' && isHostedMode()) loadHistory();
  if (viewId === 'productsView' && isHostedMode()) loadProducts();
}

// ════════════════════════════════════════════════════════════
// Receipts — transient upload, multi-provider AI extraction, review, and accept.
// The photo is sent only to the selected extraction provider during the request
// and is never stored by this app — see PHASE5_RECEIPT_OCR_PLAN.md §5.
// ════════════════════════════════════════════════════════════
async function receiptFetch(path, options = {}) {
  try {
    const res = await fetch(path, options);
    let json = null;
    try { json = await res.json(); } catch (e) {}
    if (!res.ok) {
      toast((json && (json.error || json.detail)) || `Receipt error (HTTP ${res.status})`, 'error');
      return null;
    }
    return json;
  } catch (e) {
    toast('Receipt request failed: ' + e.message, 'error');
    return null;
  }
}

function parsePenceInput(raw) {
  const cleaned = String(raw || '').replace(/[^0-9.]/g, '');
  if (!cleaned) return null;
  const pounds = parseFloat(cleaned);
  if (Number.isNaN(pounds)) return null;
  return Math.round(pounds * 100);
}

function formatPence(pennies) {
  if (pennies === null || pennies === undefined) return '';
  return '£' + (pennies / 100).toFixed(2);
}

async function loadReceipts() {
  const data = await receiptFetch('/api/receipts');
  if (!data) return;
  STATE.receipts = data.receipts || [];
  renderReceiptsList();
}

async function loadReceiptAiOptions() {
  const data = await receiptFetch('/api/receipt-ai/options');
  STATE.receiptAiOptions = (data && data.options) || [];
  renderReceiptAiSelects();
}

function renderReceiptAiSelects() {
  const options = STATE.receiptAiOptions;
  const optionsHtml = options.length
    ? '<option value="auto">Read with: Automatic</option>' +
      options.map(o => `<option value="${esc(o.alias)}">Read with: ${esc(o.label)}</option>`).join('')
    : '';
  ['receiptAiSelect', 'receiptAiSelect2'].forEach(id => {
    const el = $(id);
    if (!el) return;
    el.innerHTML = optionsHtml;
    el.value = STATE.receiptAiSelected;
    el.classList.toggle('hidden', options.length === 0);
  });
  const retrySel = $('retryAiSelect');
  if (retrySel) {
    retrySel.innerHTML = optionsHtml;
    retrySel.value = STATE.receiptAiSelected;
  }
}

function setReceiptUploading(on) {
  $('receiptProcessing').classList.toggle('hidden', !on);
  const buttonIds = [
    'receiptTakePhotoBtn', 'receiptTakePhotoBtn2',
    'receiptChooseFileBtn', 'receiptChooseFileBtn2',
  ];
  buttonIds.forEach(id => {
    const el = $(id);
    if (!el) return;
    el.classList.toggle('disabled', on);
    el.setAttribute('aria-disabled', String(on));
  });
  [
    'receiptCameraInput', 'receiptCameraInput2',
    'receiptLibraryInput', 'receiptLibraryInput2',
    'receiptAiSelect', 'receiptAiSelect2',
  ].forEach(id => {
    const el = $(id);
    if (el) el.disabled = on;
  });
}

function renderReceiptsList() {
  const empty = $('receiptEmptyState');
  const listWrap = $('receiptsListWrap');
  const reviewWrap = $('receiptReviewWrap');
  if (STATE.activeReceiptId) {
    empty.classList.add('hidden');
    listWrap.classList.add('hidden');
    reviewWrap.classList.remove('hidden');
    return;
  }
  reviewWrap.classList.add('hidden');
  const hasReceipts = STATE.receipts.length > 0;
  empty.classList.toggle('hidden', hasReceipts);
  listWrap.classList.toggle('hidden', !hasReceipts);
  if (!hasReceipts) return;

  $('receiptsList').innerHTML = STATE.receipts.map(r => {
    const shop = STATE.shops.find(s => s.id === r.shopId);
    const shopLabel = shop ? `${shop.emoji} ${esc(shop.name)}` : 'No shop yet';
    const dateLabel = esc(r.purchaseDate || 'No date yet');
    const statusClass = r.status === 'saved' ? 'status-saved' : (r.status === 'failed' ? 'status-error' : 'status-ready');
    const statusLabel = r.status === 'saved' ? 'Saved' : (r.status === 'failed' ? "Couldn't read this receipt" : 'Ready to review');
    const totalLabel = r.totalPennies != null ? ' · ' + formatPence(r.totalPennies) : '';
    return `
      <li>
        <button class="receiptListItem" type="button" onclick="openReceiptReview('${r.id}')">
          <span class="receiptListItemMeta">
            <span class="receiptListItemTitle">${shopLabel} · ${dateLabel}</span>
            <span class="hint">${r.itemCount} item${r.itemCount === 1 ? '' : 's'}${totalLabel}</span>
          </span>
          <span class="statusChip ${statusClass}">${statusLabel}</span>
        </button>
      </li>`;
  }).join('');
}

async function uploadReceiptFile(file) {
  if (!file) return;
  setReceiptUploading(true);
  setLoading(true);
  let data;
  try {
    const params = new URLSearchParams({ extractor: STATE.receiptAiSelected || 'auto' });
    data = await receiptFetch(`/api/receipts?${params}`, {
      method: 'POST',
      headers: {
        'Content-Type': file.type || 'application/octet-stream',
        'X-Receipt-Filename': encodeURIComponent(file.name || ''),
      },
      body: file,
    });
  } finally {
    setLoading(false);
    setReceiptUploading(false);
  }
  if (!data) return;
  STATE.activeReceiptFile = file;
  STATE.activeReceiptFileFor = data.id;
  await loadReceipts();
  await openReceiptReview(data.id);
}

async function retryReceiptReview() {
  if (!STATE.activeReceiptId || !STATE.activeReceiptFile) return;
  const receiptId = STATE.activeReceiptId;
  const includedCount = (STATE.activeReceiptData?.items || []).filter(i => i.accepted && !i.excluded).length;
  if (includedCount > 0 && !confirm('Retrying will replace the current extracted items. Continue?')) return;
  const alias = $('retryAiSelect').value || 'auto';
  setReceiptUploading(true);
  setLoading(true);
  let data;
  try {
    const params = new URLSearchParams({ extractor: alias });
    data = await receiptFetch(`/api/receipts/${receiptId}/retry?${params}`, {
      method: 'POST',
      headers: {
        'Content-Type': STATE.activeReceiptFile.type || 'application/octet-stream',
        'X-Receipt-Filename': encodeURIComponent(STATE.activeReceiptFile.name || ''),
      },
      body: STATE.activeReceiptFile,
    });
  } finally {
    setLoading(false);
    setReceiptUploading(false);
  }
  if (!data) return;
  if (STATE.activeReceiptId !== receiptId) return;
  STATE.activeReceiptData = data;
  renderReceiptReview();
}

async function openReceiptReview(id) {
  const data = await receiptFetch(`/api/receipts/${id}`);
  if (!data) return;
  // The in-memory File only ever belongs to the receipt it was just
  // uploaded/retried for — opening a different receipt from the list must
  // not offer to "retry" it with a stale, unrelated photo.
  if (STATE.activeReceiptFileFor !== id) {
    STATE.activeReceiptFile = null;
    STATE.activeReceiptFileFor = null;
  }
  STATE.activeReceiptId = id;
  STATE.activeReceiptData = data;
  renderReceiptReview();
  renderReceiptsList();
}

function closeReceiptReview() {
  STATE.activeReceiptId = null;
  STATE.activeReceiptData = null;
  STATE.activeReceiptFile = null;
  STATE.activeReceiptFileFor = null;
  STATE.receiptPatchPromise = Promise.resolve();
  renderReceiptsList();
}

function renderReceiptReview() {
  const data = STATE.activeReceiptData;
  if (!data) return;
  const editable = ['ready', 'reviewed', 'failed', 'saved'].includes(data.status);
  const saved = data.status === 'saved';

  const shopSel = $('reviewShopSelect');
  shopSel.innerHTML = '<option value="">Choose shop…</option>' +
    STATE.shops.map(s => `<option value="${s.id}"${s.id === data.shopId ? ' selected' : ''}>${s.emoji} ${esc(s.name)}</option>`).join('');
  $('reviewDateInput').value = data.purchaseDate || '';
  $('reviewTotalInput').value = data.totalPennies == null ? '' : (data.totalPennies / 100).toFixed(2);
  shopSel.disabled = !editable;
  $('reviewDateInput').disabled = !editable;
  $('reviewTotalInput').disabled = !editable;
  $('reviewSavedNote').classList.toggle('hidden', editable && !saved);
  $('reviewSavedNote').textContent = saved
    ? 'Saved to history · edits here update history automatically.'
    : `${data.status} · editing is temporarily unavailable`;
  $('reviewNewItemRow').classList.toggle('hidden', !editable);
  $('reviewAddRowBtn').classList.toggle('hidden', !editable);
  $('reviewDiscardBtn').classList.toggle('hidden', !editable);
  $('reviewSaveBtn').classList.toggle('hidden', !editable || saved);
  $('reviewDiscardBtn').textContent = saved ? 'Delete receipt & history' : 'Discard';

  // Status chip — only shown for the "couldn't read this" branch; ready/reviewed/saved
  // are already conveyed by the shop/date fields and footer being editable or not.
  $('reviewStatusRow').classList.toggle('hidden', data.status !== 'failed');

  // Retry — only offered while this browser tab still holds the photo that was
  // just uploaded/retried for *this* receipt (images are never stored server-side).
  const canRetry = STATE.receiptAiOptions.length > 0 && data.status !== 'saved';
  const hasFile = STATE.activeReceiptFileFor === STATE.activeReceiptId && STATE.activeReceiptFile;
  $('reviewRetryRow').classList.toggle('hidden', !(canRetry && hasFile));
  $('reviewRetryHint').classList.toggle('hidden', !(canRetry && !hasFile && data.status === 'failed'));

  const includedCount = data.items.filter(i => i.accepted && !i.excluded).length;
  // itemsTotalPennies sums only the included rows — removing a line removes its
  // cost, so this can legitimately differ from the printed receipt total.
  const includedTotal = data.itemsTotalPennies != null ? ` · ${formatPence(data.itemsTotalPennies)}` : '';
  $('reviewCount').textContent = `${includedCount} item${includedCount === 1 ? '' : 's'}${includedTotal}`;

  const excludedCount = data.items.filter(i => i.excluded).length;
  $('reviewExcludedNote').classList.toggle('hidden', excludedCount === 0);
  if (excludedCount > 0) {
    $('reviewExcludedNote').textContent =
      `${excludedCount} line${excludedCount === 1 ? '' : 's'} hidden as offers / totals / points — tap to review`;
  }

  $('reviewRows').innerHTML = data.items.map(item => {
    const excluded = item.excluded;
    const priceLabel = item.lineTotalPennies != null ? formatPence(item.lineTotalPennies)
      : (item.unitPricePennies != null ? formatPence(item.unitPricePennies) : '');
    const qtyLabel = item.quantity ? ` × ${item.quantity}${item.unit ? ' ' + esc(item.unit) : ''}` : '';
    const action = !editable ? '' : (excluded
      ? `<button class="rowRestore" type="button" title="Restore" onclick="toggleReceiptItemExcluded('${item.id}', false)">↺</button>`
      : `<button class="rowDel" type="button" title="Remove" onclick="toggleReceiptItemExcluded('${item.id}', true)">✕</button>`);
    const fields = editable ? `
        <input class="reviewInput grow" value="${esc(item.name || item.rawText)}" aria-label="Item name" onchange="saveReceiptItemField('${item.id}', 'name', this.value)">
        <input class="reviewInput qty" inputmode="decimal" value="${item.quantity ?? 1}" aria-label="Quantity" onchange="saveReceiptItemField('${item.id}', 'quantity', this.value)">
        <input class="reviewInput unit" value="${esc(item.unit || '')}" aria-label="Unit" onchange="saveReceiptItemField('${item.id}', 'unit', this.value)">
        <input class="reviewInput price" inputmode="decimal" value="${priceLabel.replace('£', '')}" placeholder="£" aria-label="Price" onchange="saveReceiptItemField('${item.id}', 'lineTotalPennies', this.value)">`
      : `<span class="reviewInput grow" style="background:none;border:none">${esc(item.name || item.rawText)}${qtyLabel}</span>
        <span class="reviewInput price" style="background:none;border:none;text-align:right">${priceLabel}</span>`;
    const confDot = item.confidence == null ? ''
      : `<span class="confDot ${item.confidence >= 0.6 ? 'conf-ok' : 'conf-low'}" title="${item.confidence >= 0.6 ? 'High confidence' : 'Low confidence — check this'}"></span>`;
    return `
      <li class="reviewRow${excluded ? ' excluded' : ''}">
        ${confDot}
        ${fields}
        ${action}
      </li>`;
  }).join('');
}

async function saveReceiptShopDate() {
  if (!STATE.activeReceiptId || !STATE.activeReceiptData) return null;
  const receiptId = STATE.activeReceiptId;
  const payload = {
    shopId: $('reviewShopSelect').value || null,
    purchaseDate: $('reviewDateInput').value || null,
    totalPennies: parsePenceInput($('reviewTotalInput').value),
  };
  const patch = async () => {
    const data = await receiptFetch(`/api/receipts/${receiptId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (data && STATE.activeReceiptId === receiptId) STATE.activeReceiptData = data;
    return data;
  };
  STATE.receiptPatchPromise = STATE.receiptPatchPromise.then(patch, patch);
  return STATE.receiptPatchPromise;
}

async function saveReceiptItemField(itemId, field, rawValue) {
  if (!STATE.activeReceiptId) return;
  const receiptId = STATE.activeReceiptId;
  let value = rawValue;
  if (field === 'quantity') value = Number(rawValue);
  if (field === 'lineTotalPennies') value = parsePenceInput(rawValue);
  const patch = () => receiptFetch(`/api/receipts/${receiptId}/items/${itemId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [field]: value }),
    });
  STATE.receiptPatchPromise = STATE.receiptPatchPromise.then(patch, patch);
  const data = await STATE.receiptPatchPromise;
  if (!data) {
    renderReceiptReview(); // restore the last server-confirmed value after validation errors
    return;
  }
  if (STATE.activeReceiptId === receiptId) {
    STATE.activeReceiptData = data;
  }
}

async function addReceiptReviewItem() {
  if (!STATE.activeReceiptId) return;
  const receiptId = STATE.activeReceiptId;
  const nameInput = $('reviewNewItemName');
  const name = nameInput.value.trim();
  if (!name) { toast('Enter an item name', 'warn'); return; }
  const quantity = parseFloat($('reviewNewItemQty').value) || 1;
  const unit = $('reviewNewItemUnit').value.trim();
  const lineTotalPennies = parsePenceInput($('reviewNewItemPrice').value);

  const patch = () => receiptFetch(`/api/receipts/${receiptId}/items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, quantity, unit, lineTotalPennies }),
    });
  STATE.receiptPatchPromise = STATE.receiptPatchPromise.then(patch, patch);
  const data = await STATE.receiptPatchPromise;
  if (!data) return;
  if (STATE.activeReceiptId !== receiptId) return;
  STATE.activeReceiptData = data;
  renderReceiptReview();
  nameInput.value = '';
  $('reviewNewItemQty').value = '';
  $('reviewNewItemUnit').value = '';
  $('reviewNewItemPrice').value = '';
  nameInput.focus();
}

async function toggleReceiptItemExcluded(itemId, excluded) {
  if (!STATE.activeReceiptId) return;
  const receiptId = STATE.activeReceiptId;
  const patch = () => receiptFetch(`/api/receipts/${receiptId}/items/${itemId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ excluded }),
    });
  STATE.receiptPatchPromise = STATE.receiptPatchPromise.then(patch, patch);
  const data = await STATE.receiptPatchPromise;
  if (!data) return;
  if (STATE.activeReceiptId !== receiptId) return;
  STATE.activeReceiptData = data;
  renderReceiptReview();
}

async function discardReceiptReview() {
  if (!STATE.activeReceiptId) return;
  const saved = STATE.activeReceiptData?.status === 'saved';
  const warning = saved
    ? 'Delete this receipt and its linked history entry? This cannot be undone.'
    : 'Discard this receipt? This cannot be undone.';
  if (!confirm(warning)) return;
  await STATE.receiptPatchPromise;
  const data = await receiptFetch(`/api/receipts/${STATE.activeReceiptId}`, { method: 'DELETE' });
  if (!data) return;
  toast(saved ? 'Receipt and history deleted' : 'Receipt discarded', 'info');
  closeReceiptReview();
  loadReceipts();
  if (saved) loadHistory();
}

async function acceptReceiptReview() {
  if (!STATE.activeReceiptId) return;
  if (!await saveReceiptShopDate()) return;
  const data = await receiptFetch(`/api/receipts/${STATE.activeReceiptId}/accept`, { method: 'POST' });
  if (!data) return;
  toast(`Saved ${data.itemCount} item${data.itemCount === 1 ? '' : 's'} to history`, 'success');
  closeReceiptReview();
  loadReceipts();
}

// ════════════════════════════════════════════════════════════
// History — trip-grouped browse, edit, and delete.
// Receipt-backed trips remain linked bidirectionally on the server.
// ════════════════════════════════════════════════════════════
async function loadHistory() {
  const data = await receiptFetch('/api/history');
  if (!data) return;
  STATE.historyTrips = data.trips || [];
  if (STATE.activeHistoryTrip) {
    STATE.activeHistoryTrip = STATE.historyTrips.find(t => t.id === STATE.activeHistoryTrip.id) || null;
  }
  renderHistory();
}

function historyShopLabel(trip) {
  const shop = STATE.shops.find(s => s.id === trip.shopId);
  return shop ? `${shop.emoji} ${shop.name}` : 'Unknown shop';
}

function renderHistory() {
  const empty = $('historyEmpty');
  const list = $('historyList');
  const editor = $('historyEditor');
  if (STATE.activeHistoryTrip) {
    empty.classList.add('hidden');
    list.classList.add('hidden');
    editor.classList.remove('hidden');
    renderHistoryEditor();
    return;
  }
  editor.classList.add('hidden');
  const hasTrips = STATE.historyTrips.length > 0;
  empty.classList.toggle('hidden', hasTrips);
  list.classList.toggle('hidden', !hasTrips);
  list.innerHTML = STATE.historyTrips.map(trip => {
    const total = trip.totalPennies == null ? '' : formatPence(trip.totalPennies);
    const source = trip.receiptId ? 'Receipt' : 'List';
    return `<li class="tripCard">
      <button class="tripHead" type="button" onclick="openHistoryTrip('${trip.id}')">
        <span class="tripEmoji">${trip.receiptId ? '🧾' : '🛒'}</span>
        <span class="tripMeta"><strong>${esc(historyShopLabel(trip))}</strong> · ${esc(trip.tripDate)} · ${trip.items.length} item${trip.items.length === 1 ? '' : 's'}<br><span class="hint">${source}</span></span>
        <span class="tripTotal">${total}</span>
        <span class="tripChevron">›</span>
      </button>
    </li>`;
  }).join('');
}

function openHistoryTrip(id) {
  STATE.activeHistoryTrip = STATE.historyTrips.find(t => t.id === id) || null;
  STATE.historyPatchPromise = Promise.resolve();
  renderHistory();
}

function closeHistoryTrip() {
  STATE.activeHistoryTrip = null;
  STATE.historyPatchPromise = Promise.resolve();
  renderHistory();
}

function renderHistoryEditor() {
  const trip = STATE.activeHistoryTrip;
  if (!trip) return;
  const shopSel = $('historyShopSelect');
  shopSel.innerHTML = '<option value="">Choose shop…</option>' +
    STATE.shops.map(s => `<option value="${s.id}"${s.id === trip.shopId ? ' selected' : ''}>${s.emoji} ${esc(s.name)}</option>`).join('');
  $('historyDateInput').value = trip.tripDate || '';
  $('historyTotalInput').value = trip.totalPennies == null ? '' : (trip.totalPennies / 100).toFixed(2);
  $('historySourceChip').textContent = trip.receiptId ? 'Receipt' : 'List history';
  $('historyLinkedNote').classList.toggle('hidden', !trip.receiptId);
  $('historyDeleteBtn').textContent = trip.receiptId ? 'Delete receipt & history' : 'Delete history entry';
  $('historyRows').innerHTML = trip.items.map(item => {
    const price = item.lineTotalPennies == null ? '' : (item.lineTotalPennies / 100).toFixed(2);
    return `<li class="reviewRow">
      <input class="reviewInput grow" value="${esc(item.name)}" aria-label="History item name" onchange="saveHistoryItemField('${item.id}', 'name', this.value)">
      <input class="reviewInput qty" inputmode="decimal" value="${item.quantity ?? 1}" aria-label="History quantity" onchange="saveHistoryItemField('${item.id}', 'quantity', this.value)">
      <input class="reviewInput unit" value="${esc(item.unit || '')}" aria-label="History unit" onchange="saveHistoryItemField('${item.id}', 'unit', this.value)">
      <input class="reviewInput price" inputmode="decimal" value="${price}" placeholder="£" aria-label="History price" onchange="saveHistoryItemField('${item.id}', 'lineTotalPennies', this.value)">
      <button class="rowDel" type="button" title="Delete item" onclick="deleteHistoryItem('${item.id}')">✕</button>
    </li>`;
  }).join('');
}

async function saveHistoryTrip() {
  const trip = STATE.activeHistoryTrip;
  if (!trip) return null;
  const tripId = trip.id;
  const payload = {
    shopId: $('historyShopSelect').value || null,
    tripDate: $('historyDateInput').value || null,
    totalPennies: parsePenceInput($('historyTotalInput').value),
  };
  const patch = () => receiptFetch(`/api/history/${tripId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  STATE.historyPatchPromise = STATE.historyPatchPromise.then(patch, patch);
  const data = await STATE.historyPatchPromise;
  if (data && STATE.activeHistoryTrip?.id === tripId) {
    STATE.activeHistoryTrip = data;
    STATE.historyTrips = STATE.historyTrips.map(t => t.id === tripId ? data : t);
  }
  return data;
}

async function saveHistoryItemField(itemId, field, rawValue) {
  const trip = STATE.activeHistoryTrip;
  if (!trip) return;
  const tripId = trip.id;
  let value = rawValue;
  if (field === 'quantity') value = Number(rawValue);
  if (field === 'lineTotalPennies') value = parsePenceInput(rawValue);
  const patch = () => receiptFetch(`/api/history/${tripId}/items/${itemId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ [field]: value }),
  });
  STATE.historyPatchPromise = STATE.historyPatchPromise.then(patch, patch);
  const data = await STATE.historyPatchPromise;
  if (!data) { renderHistoryEditor(); return; }
  if (STATE.activeHistoryTrip?.id === tripId) {
    STATE.activeHistoryTrip = data;
    STATE.historyTrips = STATE.historyTrips.map(t => t.id === tripId ? data : t);
  }
}

async function deleteHistoryItem(itemId) {
  const trip = STATE.activeHistoryTrip;
  if (!trip || !confirm('Delete this item from history?')) return;
  await STATE.historyPatchPromise;
  const data = await receiptFetch(`/api/history/${trip.id}/items/${itemId}`, { method: 'DELETE' });
  if (!data) return;
  STATE.activeHistoryTrip = data;
  STATE.historyTrips = STATE.historyTrips.map(t => t.id === data.id ? data : t);
  renderHistoryEditor();
  if (trip.receiptId) loadReceipts();
}

async function deleteHistoryTrip() {
  const trip = STATE.activeHistoryTrip;
  if (!trip) return;
  const warning = trip.receiptId
    ? 'Delete this history entry and its linked receipt? This cannot be undone.'
    : 'Delete this history entry? This cannot be undone.';
  if (!confirm(warning)) return;
  await STATE.historyPatchPromise;
  const data = await receiptFetch(`/api/history/${trip.id}`, { method: 'DELETE' });
  if (!data) return;
  toast(data.deletedReceipt ? 'Receipt and history deleted' : 'History entry deleted', 'info');
  closeHistoryTrip();
  await loadHistory();
  if (data.deletedReceipt) loadReceipts();
}

// ════════════════════════════════════════════════════════════
// Products — the item catalog with live purchase stats and merging.
// Merging keeps the chosen product's name; the other names become
// aliases so future purchases still count against the merged product.
// ════════════════════════════════════════════════════════════
async function loadProducts() {
  const data = await receiptFetch('/api/products');
  if (!data) return;
  STATE.products = data.products || [];
  // Drop selections for products that no longer exist (e.g. just merged away).
  const ids = new Set(STATE.products.map(p => p.id));
  STATE.productSelection.forEach(id => { if (!ids.has(id)) STATE.productSelection.delete(id); });
  renderProducts();
}

function productStatsLine(p) {
  const parts = [];
  parts.push(p.purchaseCount === 1 ? '1 purchase' : `${p.purchaseCount} purchases`);
  if (p.lastBoughtAt) {
    const shop = STATE.shops.find(s => s.id === p.lastShopId);
    parts.push(`last ${p.lastBoughtAt.slice(0, 10)}${shop ? ` at ${shop.name}` : ''}`);
  }
  if (p.totalSpendPennies != null) parts.push(`${formatPence(p.totalSpendPennies)} total`);
  return parts.join(' · ');
}

function renderProducts() {
  const hasProducts = STATE.products.length > 0;
  $('productsEmpty').classList.toggle('hidden', hasProducts);
  $('productsControls').classList.toggle('hidden', !hasProducts);
  const q = STATE.productSearch.trim().toLowerCase();
  const visible = !q ? STATE.products : STATE.products.filter(p =>
    p.name.toLowerCase().includes(q) ||
    p.canonicalName.includes(q) ||
    p.aliases.some(a => a.includes(q)));

  $('productsList').innerHTML = visible.map(p => {
    const sel = STATE.productSelection.has(p.id);
    const price = p.lastUnitPricePennies != null ? formatPence(p.lastUnitPricePennies) : '';
    const aliasLine = p.aliases.length
      ? `<span class="hint">also: ${esc(p.aliases.join(', '))}</span>` : '';
    return `
      <li class="productRow${sel ? ' selected' : ''}">
        <input type="checkbox" class="productCheck" aria-label="Select ${esc(p.name)}"
          ${sel ? ' checked' : ''} onchange="toggleProductSelected('${p.id}')">
        <span class="productMeta">
          <strong>${esc(p.name)}</strong>
          ${aliasLine}
          <span class="hint">${esc(productStatsLine(p))}</span>
        </span>
        <span class="productPrice">${price}</span>
      </li>`;
  }).join('');
  renderProductMergeBar();
}

function toggleProductSelected(id) {
  if (STATE.productSelection.has(id)) STATE.productSelection.delete(id);
  else STATE.productSelection.add(id);
  renderProducts();
}

function renderProductMergeBar() {
  const bar = $('productMergeBar');
  const selected = STATE.products.filter(p => STATE.productSelection.has(p.id));
  bar.classList.toggle('hidden', selected.length < 2);
  if (selected.length < 2) return;
  const targetSel = $('productMergeTarget');
  const previous = targetSel.value;
  targetSel.innerHTML = selected.map(p =>
    `<option value="${esc(p.id)}">Keep: ${esc(p.name)}</option>`).join('');
  if (selected.some(p => p.id === previous)) targetSel.value = previous;
  $('productMergeBtn').textContent = `Merge ${selected.length}`;
}

function cancelProductSelection() {
  STATE.productSelection.clear();
  renderProducts();
}

async function mergeSelectedProducts() {
  const selected = STATE.products.filter(p => STATE.productSelection.has(p.id));
  if (selected.length < 2) return;
  const targetId = $('productMergeTarget').value;
  const target = selected.find(p => p.id === targetId);
  if (!target) return;
  const sources = selected.filter(p => p.id !== targetId);
  const names = sources.map(p => `"${p.name}"`).join(', ');
  if (!confirm(`Merge ${names} into "${target.name}"? Their purchase history combines and this cannot be undone.`)) return;
  const data = await receiptFetch('/api/products/merge', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ targetId, sourceIds: sources.map(p => p.id) }),
  });
  if (!data) return;
  toast(`Merged into "${target.name}"`, 'success');
  STATE.productSelection.clear();
  await loadProducts();
}

// ════════════════════════════════════════════════════════════
// Inline item-name editing (create tab only)
// ════════════════════════════════════════════════════════════
function editItemName(span, id) {
  const original = span.textContent.trim();
  const input = document.createElement('input');
  input.type = 'text';
  input.value = original;
  input.className = 'itemRowNameEdit';
  span.replaceWith(input);
  input.focus();
  input.select();

  let done = false;

  const commit = () => {
    if (done) return;
    done = true;
    const newName = input.value.trim() || original;
    input.replaceWith(span);
    if (newName === original) return;
    span.textContent = newName;
    const item = STATE.items.find(i => i.id === id);
    if (item) item.item = newName;
    api('updateItem', { id, item: newName });
    renderShoppingList();
  };

  const cancel = () => {
    if (done) return;
    done = true;
    input.replaceWith(span);
  };

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { e.preventDefault(); cancel(); }
  });
  input.addEventListener('blur', commit);
}

// ════════════════════════════════════════════════════════════
// Drag-and-drop reordering (Sortable.js)
// ════════════════════════════════════════════════════════════
function initAllSortables() {
  if (typeof Sortable === 'undefined') return;

  // Destroy stale instances (DOM nodes they reference are gone after re-render)
  Object.values(SORTABLES).forEach(s => { try { s.destroy(); } catch (e) {} });
  Object.keys(SORTABLES).forEach(k => delete SORTABLES[k]);

  const base = {
    animation:        200,
    handle:           '.dragHandle',
    delay:            150,          // ms hold before drag starts
    delayOnTouchOnly: true,         // instant on mouse, 150 ms hold on touch
    ghostClass:       'sortable-ghost',
    chosenClass:      'sortable-chosen',
  };

  // ── Settings shop list (all shops) ──────────────────────
  const shopsList = $('shopsList');
  if (shopsList && shopsList.children.length) {
    SORTABLES.settings = new Sortable(shopsList, {
      ...base,
      onEnd() {
        const newIds = [...shopsList.querySelectorAll('.shopManageItem')]
                         .map(el => el.dataset.id);
        reorderShops(newIds);
      },
    });
  }

  // ── Create tab columns (enabled shops only) ─────────────
  const createSections = $('createSections');
  if (createSections && createSections.children.length) {
    SORTABLES.create = new Sortable(createSections, {
      ...base,
      onEnd() {
        const newIds = [...createSections.querySelectorAll('.shopSection')]
                         .map(el => el.dataset.shop);
        reorderShops(newIds);
      },
    });
  }

  // ── Shopping tab groups (shops that have items) ──────────
  const shoppingList = $('shoppingList');
  if (shoppingList && shoppingList.children.length) {
    SORTABLES.shop = new Sortable(shoppingList, {
      ...base,
      onEnd() {
        const newIds = [...shoppingList.querySelectorAll('.shopGroup')]
                         .map(el => el.dataset.shop);
        reorderShops(newIds);
      },
    });
  }

  // ── Items within each create-tab section ─────────────────
  const itemBase = {
    animation:        150,
    handle:           '.itemDragHandle',
    filter:           '.saving',       // don't drag items still being saved
    delay:            150,
    delayOnTouchOnly: true,
    ghostClass:       'sortable-ghost',
    chosenClass:      'sortable-chosen',
  };

  document.querySelectorAll('.sectionItems').forEach(el => {
    const shopId = el.id.replace('sectionItems_', '');
    SORTABLES['section_' + shopId] = new Sortable(el, {
      ...itemBase,
      onEnd() {
        const newIds = [...el.querySelectorAll('.itemRow')].map(r => r.dataset.id);
        reorderItems(newIds, shopId);
      },
    });
  });

  // ── Items within each shopping-tab group ──────────────────
  document.querySelectorAll('.shopItems').forEach(el => {
    const shopId = el.closest('.shopGroup')?.dataset.shop;
    if (!shopId) return;
    SORTABLES['shopItems_' + shopId] = new Sortable(el, {
      ...itemBase,
      onEnd() {
        const newIds = [...el.querySelectorAll('.shopItem')].map(r => r.dataset.id);
        reorderItems(newIds, shopId);
      },
    });
  });
}

// ════════════════════════════════════════════════════════════
// Event wiring
// ════════════════════════════════════════════════════════════
function wire() {
  // Hosted vs legacy/static mode. CSS uses body.hosted to hide legacy-only
  // controls (Apps Script URL, server AI key) that don't apply to the /api backend.
  document.body.classList.toggle('hosted', isHostedMode());

  // Tabs
  $$('.tab').forEach(tab => tab.addEventListener('click', () => switchTab(tab.dataset.tab)));

  // Header buttons
  $('refreshBtn').addEventListener('click', loadAll);

  // Logout — only meaningful (and only shown) when served by the FastAPI host.
  // Hidden by default in the markup so it never appears in legacy GitHub Pages mode.
  const logoutBtn = $('logoutBtn');
  if (logoutBtn && isHostedMode()) {
    logoutBtn.classList.remove('hidden');
    logoutBtn.addEventListener('click', () => { window.location.assign('/logout'); });
  }

  // Receipts tab segmented control
  $$('.segment').forEach(seg =>
    seg.addEventListener('click', () => switchSegment(seg.dataset.segment)));

  // Receipts upload/review (hosted mode only — no backend for this in legacy static mode)
  if (isHostedMode()) {
    const onFilePicked = input => {
      const file = input.files && input.files[0];
      input.value = ''; // allow re-selecting the same file later
      if (file) uploadReceiptFile(file);
    };
    [
      'receiptCameraInput', 'receiptCameraInput2',
      'receiptLibraryInput', 'receiptLibraryInput2',
    ].forEach(id => {
      const input = $(id);
      input.addEventListener('change', () => onFilePicked(input));
    });

    $('reviewShopSelect').addEventListener('change', saveReceiptShopDate);
    $('reviewDateInput').addEventListener('change', saveReceiptShopDate);
    $('reviewTotalInput').addEventListener('change', saveReceiptShopDate);
    $('reviewBackBtn').addEventListener('click', async () => {
      closeReceiptReview();
      await loadReceipts();
    });
    $('reviewAddRowBtn').addEventListener('click', addReceiptReviewItem);
    $('reviewDiscardBtn').addEventListener('click', discardReceiptReview);
    $('reviewSaveBtn').addEventListener('click', acceptReceiptReview);
    $('reviewRetryBtn').addEventListener('click', retryReceiptReview);

    $('historyBackBtn').addEventListener('click', closeHistoryTrip);
    $('historyShopSelect').addEventListener('change', saveHistoryTrip);
    $('historyDateInput').addEventListener('change', saveHistoryTrip);
    $('historyTotalInput').addEventListener('change', saveHistoryTrip);
    $('historyDeleteBtn').addEventListener('click', deleteHistoryTrip);

    $('productSearchInput').addEventListener('input', e => {
      STATE.productSearch = e.target.value;
      renderProducts();
    });
    $('productMergeBtn').addEventListener('click', mergeSelectedProducts);
    $('productMergeCancelBtn').addEventListener('click', cancelProductSelection);

    // "Read with" model picker — kept in sync across both upload locations and the retry row.
    ['receiptAiSelect', 'receiptAiSelect2', 'retryAiSelect'].forEach(id => {
      $(id).addEventListener('change', e => { STATE.receiptAiSelected = e.target.value; renderReceiptAiSelects(); });
    });
    loadReceiptAiOptions();
  } else {
    [
      'receiptCameraInput', 'receiptCameraInput2',
      'receiptLibraryInput', 'receiptLibraryInput2',
    ].forEach(id => { $(id).disabled = true; });
  }

  // Suggestions strip (scaffold) — Hide just collapses it for now.
  const sugDismiss = $('suggestionsDismiss');
  if (sugDismiss) {
    sugDismiss.addEventListener('click', () => $('suggestionsStrip').classList.add('hidden'));
  }

  // Receipts/History "Show example layout" toggles — reveal the inert preview skeleton.
  $$('.previewToggle').forEach(btn => btn.addEventListener('click', () => {
    const preview = btn.nextElementSibling;
    const show = preview.classList.contains('hidden');
    preview.classList.toggle('hidden', !show);
    preview.setAttribute('aria-hidden', show ? 'false' : 'true');
    btn.setAttribute('aria-expanded', show ? 'true' : 'false');
    btn.textContent = show ? 'Hide example layout' : 'Show example layout';
  }));

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
  $('layoutShopSelect').addEventListener('change', loadLayout);
  $('addLayoutRowBtn').addEventListener('click', addLayoutRow);
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
  if (apiConfigured()) {
    loadAll();
  } else {
    toast('Welcome! Open ⚙ Settings to connect a backend.', 'info');
    renderAll(); // render empty state
  }
});
