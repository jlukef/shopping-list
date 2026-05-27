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
  items:          [],   // full list from API
  shops:          [],   // shop objects {id,name,color,emoji}
  layouts:        {},   // { shopId: [{shop,department,order,keywords}] }
  activeShopFilter: null,   // shopId or null = all
  acTimeout:      null,
  acSelected:     -1,
  loading:        false,
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
  renderAll();
}

async function loadLayouts(shopId) {
  const res = await api('getLayouts', {}, { shop: shopId });
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
  renderShopSelects();
  renderCreateList();
  renderShoppingList();
  renderSettingsShops();
  renderShopFilterChips();
}

/* ── Shop selects & chips ─────────────────────────────────── */
function renderShopSelects() {
  const shops = STATE.shops;
  // Add-item form shop select
  const sel = $('shopSelect');
  const cur = sel.value || CFG.defaultShop;
  sel.innerHTML = shops.map(s =>
    `<option value="${s.id}" ${s.id === cur ? 'selected' : ''}>${s.emoji} ${s.name}</option>`
  ).join('');

  // Settings default shop select
  const defSel = $('defaultShopSelect');
  const defCur = defSel.value || CFG.defaultShop;
  defSel.innerHTML = shops.map(s =>
    `<option value="${s.id}" ${s.id === defCur ? 'selected' : ''}>${s.emoji} ${s.name}</option>`
  ).join('');

  // Layout editor shop select
  const laySel = $('layoutShopSelect');
  laySel.innerHTML = shops.map(s =>
    `<option value="${s.id}">${s.emoji} ${s.name}</option>`
  ).join('');
}

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

/* ── Create-tab list ──────────────────────────────────────── */
function renderCreateList() {
  const list = $('createList');
  const empty = $('emptyCreate');
  const count = $('createCount');
  const items = STATE.items;

  count.textContent = `${items.length} item${items.length !== 1 ? 's' : ''}`;

  if (!items.length) {
    list.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  const shopMap = shopColorMap();
  list.innerHTML = items.map(item => {
    const shop = shopMap[item.shop] || { color: '#888', emoji: '🏪', name: item.shop };
    const qty  = formatQty(item.quantity, item.unit);
    return `
      <li class="itemCard${item.bought ? ' bought' : ''}" data-id="${item.id}">
        <span class="shopDot" style="background:${shop.color}" title="${shop.name}"></span>
        <span class="itemName">${esc(item.item)}</span>
        ${qty ? `<span class="itemQty">${esc(qty)}</span>` : ''}
        <button class="deleteBtn" data-id="${item.id}" title="Remove">✕</button>
      </li>`;
  }).join('');

  list.querySelectorAll('.deleteBtn').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); removeItem(btn.dataset.id); });
  });
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
async function addItemToList() {
  const name  = $('itemInput').value.trim();
  if (!name) { $('itemInput').focus(); return; }

  const item = {
    item:     name,
    quantity: parseFloat($('qtyInput').value) || 1,
    unit:     $('unitSelect').value,
    shop:     $('shopSelect').value || CFG.defaultShop,
    notes:    $('notesInput').value.trim(),
  };

  const res = await api('addItem', item);
  if (!res) return;

  // Optimistic local update
  STATE.items.push({ ...item, id: res.id, bought: false, sortOrder: 999 });
  renderCreateList();
  renderShoppingList();

  // Reset form
  $('itemInput').value  = '';
  $('notesInput').value = '';
  $('qtyInput').value   = '1';
  $('unitSelect').value = '';
  $('itemInput').focus();
  hideAutocomplete();
}

async function removeItem(id) {
  const res = await api('deleteItem', { id });
  if (!res) return;
  STATE.items = STATE.items.filter(i => i.id !== id);
  renderCreateList();
  renderShoppingList();
}

async function toggleBought(id) {
  const item = STATE.items.find(i => i.id === id);
  if (!item) return;
  const newVal = !item.bought;
  item.bought = newVal;
  renderShoppingList();
  renderCreateList();
  const res = await api('updateItem', { id, bought: newVal });
  if (!res) {
    item.bought = !newVal; // revert
    renderShoppingList();
    renderCreateList();
  }
}

async function clearBought() {
  if (!confirm('Remove all ✓ bought items from the list?')) return;
  const res = await api('clearBought');
  if (!res) return;
  STATE.items = STATE.items.filter(i => !i.bought);
  renderCreateList();
  renderShoppingList();
  toast('Bought items cleared', 'success');
}

async function clearList() {
  if (!confirm('Clear the entire shopping list?')) return;
  const res = await api('clearList');
  if (!res) return;
  STATE.items = [];
  renderCreateList();
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
// Autocomplete
// ════════════════════════════════════════════════════════════
function onItemInputKey(e) {
  const list = $('autocompleteList');
  const items = list.querySelectorAll('li');

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    STATE.acSelected = Math.min(STATE.acSelected + 1, items.length - 1);
    highlightAc(items);
    return;
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    STATE.acSelected = Math.max(STATE.acSelected - 1, -1);
    highlightAc(items);
    return;
  }
  if (e.key === 'Enter') {
    if (STATE.acSelected >= 0 && items[STATE.acSelected]) {
      items[STATE.acSelected].click();
    } else {
      addItemToList();
    }
    return;
  }
  if (e.key === 'Escape') { hideAutocomplete(); return; }

  clearTimeout(STATE.acTimeout);
  STATE.acTimeout = setTimeout(() => fetchAutocomplete($('itemInput').value), 250);
}

async function fetchAutocomplete(q) {
  if (!q.trim() || !CFG.scriptUrl) { hideAutocomplete(); return; }
  const res = await apiQ('getAutocomplete', { q });
  if (!res || !res.items.length) { hideAutocomplete(); return; }
  showAutocomplete(res.items);
}

function showAutocomplete(items) {
  const list = $('autocompleteList');
  const shopMap = shopColorMap();
  STATE.acSelected = -1;

  list.innerHTML = items.map(item => {
    const shop = item.defaultShop ? shopMap[item.defaultShop] : null;
    const qty  = formatQty(item.defaultQty, item.defaultUnit);
    return `
      <li data-item='${JSON.stringify(item).replace(/'/g, '&#39;')}'>
        <span class="acItem">${esc(item.item)}</span>
        <span class="acMeta">
          ${qty ? `<span>${esc(qty)}</span>` : ''}
          ${shop ? `<span class="acShopTag">${shop.emoji} ${esc(shop.name)}</span>` : ''}
        </span>
      </li>`;
  }).join('');

  list.querySelectorAll('li').forEach(li => {
    li.addEventListener('mousedown', e => { e.preventDefault(); selectAcItem(li); });
  });

  list.classList.remove('hidden');
}

function selectAcItem(li) {
  const item = JSON.parse(li.dataset.item);
  $('itemInput').value = item.item;
  if (item.defaultQty)  $('qtyInput').value   = item.defaultQty;
  if (item.defaultUnit) $('unitSelect').value  = item.defaultUnit;
  if (item.defaultShop) $('shopSelect').value  = item.defaultShop;
  hideAutocomplete();
  $('itemInput').focus();
}

function hideAutocomplete() {
  $('autocompleteList').classList.add('hidden');
  $('autocompleteList').innerHTML = '';
  STATE.acSelected = -1;
}

function highlightAc(items) {
  items.forEach((el, i) => el.classList.toggle('selected', i === STATE.acSelected));
  if (STATE.acSelected >= 0) items[STATE.acSelected].scrollIntoView({ block: 'nearest' });
}

// ════════════════════════════════════════════════════════════
// Settings modal
// ════════════════════════════════════════════════════════════
async function openSettings() {
  $('scriptUrlInput').value    = CFG.scriptUrl;
  $('defaultShopSelect').value = CFG.defaultShop;
  $('claudeKeyInput').value    = '';  // never pre-fill a key field
  // Show whether a key is already stored server-side
  $('claudeKeyStatus').textContent = '';
  if (CFG.scriptUrl) {
    const res = await apiQ('getApiKeySet');
    if (res) {
      $('claudeKeyStatus').textContent = res.set
        ? `✅ Key set (${res.preview})`
        : '⚠️ No key saved yet';
    }
  }
  $('settingsModal').classList.remove('hidden');
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

  // Add item
  $('addBtn').addEventListener('click', addItemToList);
  $('itemInput').addEventListener('keydown', onItemInputKey);
  $('itemInput').addEventListener('blur', () => setTimeout(hideAutocomplete, 150));

  // Batch fill shop/qty/unit defaults from autocomplete selection
  $('itemInput').addEventListener('input', () => {
    clearTimeout(STATE.acTimeout);
    STATE.acTimeout = setTimeout(() => fetchAutocomplete($('itemInput').value), 250);
  });

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
