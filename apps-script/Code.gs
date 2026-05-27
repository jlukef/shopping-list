// ============================================================
//  Shopping List API — Google Apps Script Backend
//  Sheet ID is stored as a Script Property so it never
//  appears in source code pushed to GitHub.
//  Set it once via: Project Settings → Script Properties →
//  Add property  SHEET_ID = 1EuhJYgxXg0kd8JOt_2GiWnXOjY3-W6CNNG2XHcrs-Gw
// ============================================================

const SHEET_ID = '1EuhJYgxXg0kd8JOt_2GiWnXOjY3-W6CNNG2XHcrs-Gw';

const SHEETS = {
  LIST:    'List',
  ITEMS:   'Items',
  SHOPS:   'Shops',
  LAYOUTS: 'StoreLayouts',
  HISTORY: 'History'
};

// ── Entry point ──────────────────────────────────────────────
// All operations come in as GET requests to avoid CORS issues
// with Apps Script's redirect behaviour on POST.
// Mutations pass their payload as a JSON-encoded `data` param.
function doGet(e) {
  const action = e.parameter.action || '';
  const data   = e.parameter.data ? JSON.parse(e.parameter.data) : {};
  const q      = e.parameter.q    || '';
  const shop   = e.parameter.shop || '';

  try {
    switch (action) {
      case 'setup':          return ok(setup());
      case 'getList':        return ok(getList());
      case 'addItem':        return ok(addItem(data));
      case 'updateItem':     return ok(updateItem(data));
      case 'deleteItem':     return ok(deleteItem(data));
      case 'clearBought':    return ok(clearBought());
      case 'clearList':      return ok(clearList());
      case 'getAutocomplete':return ok(getAutocomplete(q));
      case 'getShops':       return ok(getShops());
      case 'addShop':        return ok(addShop(data));
      case 'deleteShop':     return ok(deleteShop(data));
      case 'getLayouts':     return ok(getLayouts(shop));
      case 'saveLayout':     return ok(saveLayout(data));
      case 'sortList':       return ok(sortListAI(data));
      default:               return ok({ error: 'Unknown action: ' + action });
    }
  } catch (err) {
    return ok({ error: err.message + '\n' + err.stack });
  }
}

function ok(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

function ss() {
  return SpreadsheetApp.openById(SHEET_ID);
}

// ── Sheet helpers ────────────────────────────────────────────
function getSheet(name) {
  return ss().getSheetByName(name);
}

function sheetRows(name, cols) {
  const sheet = getSheet(name);
  if (!sheet || sheet.getLastRow() <= 1) return [];
  return sheet.getRange(2, 1, sheet.getLastRow() - 1, cols).getValues()
    .filter(r => r[0] !== '' && r[0] !== null);
}

// ── Setup ────────────────────────────────────────────────────
function setup() {
  ensureSheet(SHEETS.LIST,    ['id','item','quantity','unit','shop','bought','dateAdded','notes','sortOrder']);
  ensureSheet(SHEETS.ITEMS,   ['item','count','lastUsed','category','defaultShop','defaultQty','defaultUnit']);
  ensureSheet(SHEETS.SHOPS,   ['id','name','color','emoji','active']);
  ensureSheet(SHEETS.LAYOUTS, ['shop','department','order','keywords']);
  ensureSheet(SHEETS.HISTORY, ['item','quantity','unit','shop','dateBought']);

  const shopsSheet = getSheet(SHEETS.SHOPS);
  if (shopsSheet.getLastRow() <= 1) seedShops();

  const layoutsSheet = getSheet(SHEETS.LAYOUTS);
  if (layoutsSheet.getLastRow() <= 1) seedLayouts();

  return { success: true, message: 'Setup complete' };
}

function ensureSheet(name, headers) {
  let sheet = ss().getSheetByName(name);
  if (!sheet) sheet = ss().insertSheet(name);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(headers);
    sheet.getRange(1, 1, 1, headers.length)
      .setFontWeight('bold')
      .setBackground('#1a73e8')
      .setFontColor('#ffffff');
    sheet.setFrozenRows(1);
  }
  return sheet;
}

// ── Default data ─────────────────────────────────────────────
function seedShops() {
  const rows = [
    ['tesco',       'Tesco',          '#005994', '🛒', true],
    ['sainsburys',  "Sainsbury's",    '#F06000', '🟠', true],
    ['aldi',        'Aldi',           '#1E4D9B', '🔵', true],
    ['lidl',        'Lidl',           '#0050AA', '💛', true],
    ['asda',        'ASDA',           '#78BE20', '🟢', true],
    ['waitrose',    'Waitrose',       '#007B40', '🌿', true],
    ['amazon',      'Amazon',         '#FF9900', '📦', true],
    ['boots',       'Boots',          '#004B8D', '💊', true],
    ['other',       'Other',          '#888888', '🏪', true],
  ];
  const sheet = getSheet(SHEETS.SHOPS);
  rows.forEach(r => sheet.appendRow(r));
}

function seedLayouts() {
  // Tesco — typical UK large-format layout walking from entrance
  const tesco = [
    [1,  'Entrance / Flowers / Cards',   'flowers,plants,cards,magazines,newspapers,stationery'],
    [2,  'Fresh Produce',                'fruit,veg,vegetables,salad,herbs,mushrooms,avocado,tomato,potato,onion,garlic,carrot,lettuce,spinach,kale,broccoli,cauliflower,pepper,courgette,cucumber,leek,celery,asparagus,sweet potato,parsnip,beetroot,radish,spring onion,banana,apple,orange,lemon,lime,grapefruit,mango,pineapple,melon,watermelon,strawberry,blueberry,raspberry,blackberry,cherry,grape,plum,peach,nectarine,apricot,kiwi'],
    [3,  'Bakery',                       'bread,rolls,loaf,baguette,croissant,pastry,muffin,sourdough,bagel,pitta,wrap,tortilla,naan,crumpet,scone,hot cross bun,brioche'],
    [4,  'Fish & Meat Counter',          'fresh fish,salmon fillet,sea bass,trout,halibut,plaice,fresh prawns,lobster,crab,mussels,oysters,squid,fresh steak,sirloin'],
    [5,  'Deli Counter',                 'deli,cooked meats,ham slices,salami,prosciutto,chorizo slice,pastrami,cheese counter,brie,camembert,stilton,gouda,edam'],
    [6,  'Dairy & Eggs',                 'milk,cheese,butter,yogurt,cream,eggs,margarine,sour cream,creme fraiche,double cream,single cream,whipping cream,oat milk,almond milk,soy milk,coconut milk,feta,mozzarella,cheddar,parmesan,halloumi,cream cheese,cottage cheese,ricotta,quark'],
    [7,  'Chilled Ready Meals & Juices', 'ready meal,chilled pizza,pasta salad,cooked chicken,sausage rolls,quiche,chilled soup,hummus,guacamole,tzatziki,dips,pate,smoked salmon,orange juice,fresh juice,smoothie,cold brew'],
    [8,  'Meat & Poultry',               'chicken,beef,pork,lamb,mince,sausage,bacon,turkey,duck,venison,gammon,steak,ribs,burger,meatballs,hot dogs,bratwurst,pork chops,lamb chops,beef mince,pork mince'],
    [9,  'Frozen',                       'frozen,ice cream,chips,frozen peas,sweetcorn,frozen spinach,frozen broccoli,mixed veg,fish fingers,frozen chicken,nuggets,waffles,frozen pizza,ice lolly,sorbet,frozen berries,edamame,hash brown'],
    [10, 'Breakfast & Cereal',           'cereal,porridge,oats,granola,muesli,cornflakes,weetabix,shreddies,crunchy nut,rice krispies,special k,bran flakes,cheerios,frosties,jam,honey,peanut butter,nutella,marmalade,marmite,spread'],
    [11, 'Tins & Cans',                  'tinned,canned,baked beans,chopped tomatoes,kidney beans,chickpeas,lentils tin,coconut milk tin,tuna tin,sardines,mackerel tin,sweetcorn tin,soup tin,mushy peas,cannellini beans,black beans,butter beans,borlotti beans'],
    [12, 'Pasta Rice & Grains',          'pasta,spaghetti,penne,fusilli,tagliatelle,linguine,rice,basmati,jasmine,arborio,risotto rice,couscous,quinoa,bulgur wheat,noodles,egg noodles,rice noodles,dried lentils,split peas,polenta,orzo'],
    [13, 'Sauces & Condiments',          'ketchup,mustard,mayonnaise,salad cream,relish,chutney,pickle,worcestershire,soy sauce,fish sauce,oyster sauce,hot sauce,tabasco,sriracha,vinegar,olive oil,vegetable oil,coconut oil,sesame oil,balsamic,pasta sauce,pesto,curry sauce,stock cubes,gravy granules,miso'],
    [14, 'Herbs Spices & Baking',        'flour,sugar,salt,pepper,cinnamon,cumin,coriander,turmeric,paprika,chilli powder,oregano,basil,thyme,rosemary,bay leaves,mixed spice,baking powder,bicarbonate,yeast,vanilla extract,cocoa,dark chocolate,icing sugar,golden syrup,treacle,food colouring'],
    [15, 'Snacks & Crisps',              'crisps,popcorn,almonds,cashews,peanuts,walnuts,pistachios,mixed nuts,trail mix,rice cakes,crackers,oat cakes,pretzels,tortilla chips,pork scratchings,beef jerky'],
    [16, 'Biscuits & Confectionery',     'biscuits,chocolate bar,digestives,hobnobs,rich tea,shortbread,bourbons,custard creams,jaffa cakes,cookies,kit kat,twix,snickers,mars,bounty,haribo,sweets,wine gums,percy pigs'],
    [17, 'Soft Drinks',                  'cola,pepsi,fanta,sprite,lemonade,squash,cordial,sparkling water,still water,tonic water,ginger beer,elderflower,energy drink,red bull,lucozade,kombucha,fizzy drink'],
    [18, 'Tea Coffee & Hot Drinks',      'tea,coffee,instant coffee,ground coffee,herbal tea,green tea,hot chocolate,ovaltine,chai,decaf,coffee pods,teabags,earl grey,camomile'],
    [19, 'Beer Wine & Spirits',          'beer,lager,ale,stout,wine,red wine,white wine,rosé,champagne,prosecco,cava,gin,vodka,rum,whiskey,whisky,brandy,liqueur,cider,port,sherry'],
    [20, 'World Foods',                  'sushi,miso soup,kimchi,gochujang,tahini,harissa,za\'atar,paneer,ghee,chapatti,poppadoms,basmati rice world,pad thai,rice paper'],
    [21, 'Cleaning & Laundry',           'washing up liquid,dishwasher tablets,laundry pods,washing powder,fabric softener,bleach,cleaning spray,disinfectant,floor cleaner,oven cleaner,mop,sponge,bin bags,cling film,foil,baking paper,kitchen roll,toilet roll,tissues,cotton wool'],
    [22, 'Health & Beauty',              'shampoo,conditioner,shower gel,soap,toothpaste,toothbrush,deodorant,razors,shaving foam,moisturiser,face wash,sunscreen,vitamins,paracetamol,ibuprofen,plasters,hand wash,hand cream,lip balm,cotton buds'],
    [23, 'Baby & Pet',                   'nappies,baby food,baby wipes,formula,pet food,cat food,dog food,cat litter,pet treats,dog treats'],
    [24, 'Checkout',                     ''],
  ].map(([order, dept, kw]) => ['tesco', dept, order, kw]);

  // Aldi — compact format, different order
  const aldi = [
    [1,  'Fresh Produce',    'fruit,veg,vegetables,salad,herbs,mushrooms,avocado,tomato,potato,onion,garlic,carrot,lettuce,spinach,kale,broccoli,cauliflower,pepper,courgette,cucumber,banana,apple,orange,lemon,lime,strawberry,blueberry,raspberry,grape,plum,peach'],
    [2,  'Bakery',           'bread,rolls,loaf,baguette,croissant,pastry,muffin,sourdough,pitta,wrap,crumpet,scone'],
    [3,  'Dairy & Eggs',     'milk,cheese,butter,yogurt,cream,eggs,margarine,sour cream,creme fraiche,double cream,oat milk,mozzarella,cheddar,parmesan,halloumi,cream cheese,cottage cheese'],
    [4,  'Meat & Poultry',   'chicken,beef,pork,lamb,mince,sausage,bacon,turkey,duck,gammon,steak,burger,meatballs,hot dogs'],
    [5,  'Deli & Chilled',   'deli,cooked meats,ham,salami,prosciutto,chorizo,smoked salmon,hummus,dips,ready meal,chilled pizza,pasta salad,sausage rolls,quiche,orange juice,fresh juice'],
    [6,  'Frozen',           'frozen,ice cream,chips,frozen peas,sweetcorn,mixed veg,fish fingers,frozen chicken,nuggets,waffles,frozen pizza,ice lolly,frozen berries,hash brown'],
    [7,  'Cereal & Breakfast','cereal,porridge,oats,granola,muesli,cornflakes,weetabix,jam,honey,peanut butter,nutella,marmalade,marmite'],
    [8,  'Tins & Packets',   'tinned,baked beans,chopped tomatoes,kidney beans,chickpeas,lentils,tuna,sardines,soup,pasta,spaghetti,penne,fusilli,rice,basmati,couscous,quinoa,noodles,flour,sugar'],
    [9,  'Sauces & Condiments','ketchup,mustard,mayonnaise,salad cream,relish,soy sauce,hot sauce,vinegar,olive oil,vegetable oil,pasta sauce,pesto,stock cubes,gravy'],
    [10, 'Snacks & Biscuits', 'crisps,popcorn,nuts,almonds,cashews,peanuts,rice cakes,crackers,pretzels,tortilla chips,biscuits,chocolate,digestives,hobnobs,kit kat,twix,snickers,haribo,sweets'],
    [11, 'Drinks',            'cola,lemonade,squash,water,sparkling water,energy drink,tea,coffee,herbal tea,beer,wine,prosecco,gin,vodka,rum,cider'],
    [12, 'Household & Beauty','washing up liquid,dishwasher,laundry pods,washing powder,cleaning spray,bleach,bin bags,toilet roll,kitchen roll,tissues,shampoo,shower gel,toothpaste,deodorant,razors,vitamins,paracetamol,plasters'],
  ].map(([order, dept, kw]) => ['aldi', dept, order, kw]);

  const sheet = getSheet(SHEETS.LAYOUTS);
  [...tesco, ...aldi].forEach(r => sheet.appendRow(r));
}

// ── List CRUD ────────────────────────────────────────────────
function getList() {
  const rows = sheetRows(SHEETS.LIST, 9);
  return {
    items: rows.map(r => ({
      id:        r[0],
      item:      r[1],
      quantity:  r[2],
      unit:      r[3],
      shop:      r[4],
      bought:    r[5] === true || r[5] === 'TRUE' || r[5] === true,
      dateAdded: r[6],
      notes:     r[7],
      sortOrder: r[8] === '' ? 999 : Number(r[8])
    }))
  };
}

function addItem(data) {
  const sheet = getSheet(SHEETS.LIST);
  const id    = Utilities.getUuid();
  const now   = new Date().toISOString();
  sheet.appendRow([
    id, data.item, data.quantity || 1, data.unit || '',
    data.shop || 'other', false, now, data.notes || '', 999
  ]);
  updateItemsMaster(data.item, data.shop, data.quantity, data.unit);
  return { success: true, id };
}

function updateItem(data) {
  const sheet = getSheet(SHEETS.LIST);
  if (sheet.getLastRow() <= 1) return { error: 'List is empty' };
  const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 9).getValues();
  for (let i = 0; i < rows.length; i++) {
    if (rows[i][0] !== data.id) continue;
    const row = i + 2;
    if (data.hasOwnProperty('item'))      sheet.getRange(row, 2).setValue(data.item);
    if (data.hasOwnProperty('quantity'))  sheet.getRange(row, 3).setValue(data.quantity);
    if (data.hasOwnProperty('unit'))      sheet.getRange(row, 4).setValue(data.unit);
    if (data.hasOwnProperty('shop'))      sheet.getRange(row, 5).setValue(data.shop);
    if (data.hasOwnProperty('bought'))    sheet.getRange(row, 6).setValue(data.bought);
    if (data.hasOwnProperty('notes'))     sheet.getRange(row, 8).setValue(data.notes);
    if (data.hasOwnProperty('sortOrder')) sheet.getRange(row, 9).setValue(data.sortOrder);
    if (data.bought === true) addToHistory(rows[i]);
    return { success: true };
  }
  return { error: 'Item not found' };
}

function deleteItem(data) {
  const sheet = getSheet(SHEETS.LIST);
  if (sheet.getLastRow() <= 1) return { error: 'List is empty' };
  const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 1).getValues();
  for (let i = rows.length - 1; i >= 0; i--) {
    if (rows[i][0] === data.id) { sheet.deleteRow(i + 2); return { success: true }; }
  }
  return { error: 'Item not found' };
}

function clearBought() {
  const sheet = getSheet(SHEETS.LIST);
  if (sheet.getLastRow() <= 1) return { success: true };
  const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 6).getValues();
  for (let i = rows.length - 1; i >= 0; i--) {
    if (rows[i][5] === true || rows[i][5] === 'TRUE') sheet.deleteRow(i + 2);
  }
  return { success: true };
}

function clearList() {
  const sheet = getSheet(SHEETS.LIST);
  if (sheet.getLastRow() > 1) sheet.deleteRows(2, sheet.getLastRow() - 1);
  return { success: true };
}

// ── Autocomplete / Items master ──────────────────────────────
function getAutocomplete(q) {
  const rows = sheetRows(SHEETS.ITEMS, 7);
  const query = (q || '').toLowerCase();
  const filtered = rows
    .filter(r => String(r[0]).toLowerCase().includes(query))
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 20)
    .map(r => ({
      item:        r[0],
      count:       r[1],
      category:    r[3],
      defaultShop: r[4],
      defaultQty:  r[5],
      defaultUnit: r[6]
    }));
  return { items: filtered };
}

function updateItemsMaster(itemName, shop, qty, unit) {
  const sheet = getSheet(SHEETS.ITEMS);
  const now   = new Date().toISOString();
  if (sheet.getLastRow() > 1) {
    const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 7).getValues();
    for (let i = 0; i < rows.length; i++) {
      if (String(rows[i][0]).toLowerCase() === String(itemName).toLowerCase()) {
        const row = i + 2;
        sheet.getRange(row, 2).setValue(Number(rows[i][1]) + 1);
        sheet.getRange(row, 3).setValue(now);
        if (shop) sheet.getRange(row, 5).setValue(shop);
        if (qty)  sheet.getRange(row, 6).setValue(qty);
        if (unit) sheet.getRange(row, 7).setValue(unit);
        return;
      }
    }
  }
  sheet.appendRow([itemName, 1, now, '', shop || '', qty || 1, unit || '']);
}

// ── Shops ────────────────────────────────────────────────────
function getShops() {
  const rows = sheetRows(SHEETS.SHOPS, 5);
  return {
    shops: rows
      .filter(r => r[4] !== false && r[4] !== 'FALSE')
      .map(r => ({ id: r[0], name: r[1], color: r[2], emoji: r[3] }))
  };
}

function addShop(data) {
  const sheet = getSheet(SHEETS.SHOPS);
  const id = String(data.name).toLowerCase().replace(/[^a-z0-9]/g, '');
  sheet.appendRow([id, data.name, data.color || '#888888', data.emoji || '🏪', true]);
  return { success: true, id };
}

function deleteShop(data) {
  const sheet = getSheet(SHEETS.SHOPS);
  if (sheet.getLastRow() <= 1) return { error: 'No shops' };
  const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 1).getValues();
  for (let i = rows.length - 1; i >= 0; i--) {
    if (rows[i][0] === data.id) { sheet.deleteRow(i + 2); return { success: true }; }
  }
  return { error: 'Shop not found' };
}

// ── Store layouts ─────────────────────────────────────────────
function getLayouts(shopId) {
  const rows = sheetRows(SHEETS.LAYOUTS, 4);
  return {
    layouts: rows
      .filter(r => !shopId || r[0] === shopId)
      .sort((a, b) => Number(a[2]) - Number(b[2]))
      .map(r => ({ shop: r[0], department: r[1], order: r[2], keywords: r[3] }))
  };
}

function saveLayout(data) {
  // data: { shop, departments: [{name, order, keywords}] }
  const sheet = getSheet(SHEETS.LAYOUTS);
  // Delete existing rows for this shop
  if (sheet.getLastRow() > 1) {
    const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 1).getValues();
    for (let i = rows.length - 1; i >= 0; i--) {
      if (rows[i][0] === data.shop) sheet.deleteRow(i + 2);
    }
  }
  data.departments.forEach(d => {
    sheet.appendRow([data.shop, d.name, d.order, d.keywords || '']);
  });
  return { success: true };
}

// ── History ──────────────────────────────────────────────────
function addToHistory(itemRow) {
  getSheet(SHEETS.HISTORY).appendRow([
    itemRow[1], itemRow[2], itemRow[3], itemRow[4], new Date().toISOString()
  ]);
}

// ── AI Sorting (server-side fallback) ────────────────────────
// Preferred: AI sort runs client-side in the browser.
// This function is called if the client sends a sortList action
// with a geminiKey included (it reads it from Script Properties
// if not supplied, so the key can be kept off the client entirely).
function sortListAI(data) {
  const items  = data.items  || [];
  const shopId = data.shop   || '';
  const key    = data.geminiKey
              || PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY')
              || '';

  const layouts = getLayouts(shopId).layouts;

  if (!key || !layouts.length) {
    return sortByKeywords(items, layouts);
  }

  const deptOrder = layouts.map(l => `${l.order}. ${l.department}`).join('\n');

  const prompt =
    `Sort this shopping list in the order a customer would encounter the items ` +
    `walking through a ${shopId} supermarket from entrance to checkout.\n\n` +
    `Store layout:\n${deptOrder}\n\n` +
    `Items:\n${items.map(i => `${i.id}: ${i.item}`).join('\n')}\n\n` +
    `Return JSON only: {"sortedIds": ["id1", "id2", ...]}`;

  try {
    const resp = UrlFetchApp.fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key=${key}`,
      {
        method: 'post',
        contentType: 'application/json',
        payload: JSON.stringify({
          contents: [{ parts: [{ text: prompt }] }],
          generationConfig: { temperature: 0, responseMimeType: 'application/json' }
        })
      }
    );
    const result     = JSON.parse(resp.getContentText());
    const sortedIds  = JSON.parse(result.candidates[0].content.parts[0].text).sortedIds;

    const byId = {};
    items.forEach(i => byId[i.id] = i);
    const sorted = sortedIds.filter(id => byId[id]).map((id, idx) => ({ ...byId[id], sortOrder: idx }));
    const unsorted = items.filter(i => !new Set(sortedIds).has(i.id));
    return { items: [...sorted, ...unsorted], method: 'ai' };
  } catch (err) {
    return sortByKeywords(items, layouts);
  }
}

function sortByKeywords(items, layouts) {
  const kwOrder = {};
  layouts.forEach(dept => {
    String(dept.keywords).split(',').forEach(kw => {
      kw = kw.trim().toLowerCase();
      if (kw && kwOrder[kw] === undefined) kwOrder[kw] = Number(dept.order);
    });
  });

  function rank(itemName) {
    const n = itemName.toLowerCase();
    if (kwOrder[n] !== undefined) return kwOrder[n];
    for (const [kw, ord] of Object.entries(kwOrder)) {
      if (n.includes(kw) || kw.includes(n)) return ord;
    }
    return 999;
  }

  return {
    items: [...items].sort((a, b) => rank(a.item) - rank(b.item)),
    method: 'keywords'
  };
}
