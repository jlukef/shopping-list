# Shopping List — User-Facing Flow Design

Author: Claude (product/UX lane). Status: **design proposal, no code changed.**
Companion to `BACKEND_MIGRATION_PLAN.md`. Codex owns final review and all server/identity rules.

This document describes the user-facing flows only — what the user sees and does, and the data
each step needs. It does not choose a backend stack, design session crypto, change deployment
assumptions, or define server-side identity/matching rules. Where a UX choice implies backend
behaviour, it is flagged **[Codex owns]**.

Design constraints inherited from the existing app:
- Mobile-first; two users (Jamie + wife) sharing one household list.
- Reuse the existing optimistic-UI pattern (`commitAdd`) and toast feedback.
- Keep the current List / Shopping structure and add a Receipts area without a full rewrite.

### Decisions already made by Codex (2026-06-30) — this doc now assumes these

- **One login per person**, not a shared household login. Enables `added_by_user_id` /
  `bought_by_user_id` without extra UX cost.
- **Device preferences stay in `localStorage` for Phase 1**; move server-side after the DB
  foundation (Phase 2+).
- **OCR is treated as potentially asynchronous** from the start, even if first implementation is sync.
- **Canonicalisation runs server-side once the DB exists.** The frontend may *suggest and confirm*
  matches but never owns identity rules.
- **Suggestions are household-global in the UI**, with room for per-user signals underneath.
- **Fresh-start SQLite cutover (Jamie, 2026-06-30):** the Google Sheets data is *not* migrated. The
  first database starts **empty**, pre-seeded with **seven editable default shops** in this order —
  Morrisons, Aldi, Lidl, Butcher, Fruit and Veg Shop, Boots/Superdrug, Other (with editable colours,
  emoji, departments and keywords). First-use copy must invite the first item — no mention of Sheets,
  migration, or missing configuration. In hosted mode the legacy Apps Script URL / "Run setup" / server
  AI-key controls are hidden (the app uses its own `/api` backend and local keyword sorting).

---

## 1. Login

**Goal:** server-enforced access before any list data is shown. No frontend-only gating.

Screen: a single centred card on an otherwise empty page.
- App name + small logo.
- Username field (Codex confirmed separate `jamie` / wife accounts; usernames, not emails, are fine).
- Password field with show/hide toggle (phones make typos easy).
- "Keep me signed in" checkbox, default **on** — Codex set session lifetime ~30 days, so this
  matches: an unticked box would mean a shorter/session-only cookie. **[Codex owns]** the exact
  cookie behaviour for ticked vs unticked.
- Primary "Sign in" button.
- One inline error region below the button.

Behaviour:
- Submitting disables the button and shows a spinner inside it (reuse `.savingDot` style).
- On failure, show one generic message: **"Wrong username or password."** Never reveal whether the
  username exists. **[Codex owns]** — same error/timing for unknown-user vs bad-password.
- **No public signup.** Accounts are created by a one-off admin/CLI command (Codex decision), so the
  login screen has no "create account" link. A forgotten password is handled by Jamie directly.
- After repeated rapid failures: "Too many attempts, wait a moment." **[Codex owns]** the rate limit.

Edge cases / copy:
- Offline at login: "Can't reach the server. Check your connection." (distinct from bad password).
- Session expired mid-use: bounce to login with a quiet banner "Your session expired, please sign in
  again." Preserve the tab they were on so re-entry feels seamless.

---

## 2. First successful login

**Goal:** make the very first authenticated load feel correct, not empty/broken.

- On a user's first ever login, show a one-time, dismissible welcome strip atop the List tab:
  "Welcome — this is your shared shopping list. Add items below." No modal, no wizard.
- Restore existing `localStorage` preferences (default shop, enabled shops, shop order) if present.
  If absent (new device), fall back to: all shops enabled, default shop = first shop. Per Codex,
  these stay device-local in Phase 1 and follow the user across devices only after Phase 2.
- Empty list: show a friendly per-column empty state ("Nothing here yet"), not blank space.
- Returning logins skip the welcome strip and land on the last-used tab.

---

## 3. Receipt upload

**Goal:** get a receipt photo into the system with minimum friction, from a phone, in-store or at home.

Entry point: a new **"Receipts"** tab in the existing tab bar — it's a peer activity to
List/Shopping, not a settings item.

Upload screen:
- Big primary button **"Take photo"** (`<input type="file" accept="image/*" capture="environment">`).
- Secondary **"Choose from library"** (same input without `capture`).
- Shop pre-fill ("Which shop?") using existing shop chips — defaults to the user's default shop,
  editable. Pre-tagging the shop improves later department mapping and OCR shop-format handling.

After a photo is selected:
- Immediate local thumbnail with "Use this" / "Retake".
- Validate **before** upload: reject non-images / oversized files with clear copy ("That image is
  too large — try again" / "That file isn't a photo"). **[Codex owns]** real size/type limits and
  server-side enforcement; client checks are convenience only.
- On "Use this", upload optimistically: the receipt appears in the list immediately with a
  **"Processing…"** chip + spinner, mirroring the optimistic-add pattern.

Status lifecycle the user sees (maps to `receipts.status`):
`Uploading → Processing → Ready to review → Saved`, plus an error branch **"Couldn't read this receipt."**
Because OCR is async-capable, "Processing" may persist; the user can leave the screen and get a
subtle badge on the Receipts tab when it's ready.

Edge cases / copy:
- Upload fails: keep the local image, show "Upload failed — tap to retry." Never lose their photo.
- Duplicate upload (same shop + total + date): allow it, but show a soft note on the review screen
  that a similar receipt already exists.

---

## 4. OCR review / correction

**Goal:** the trust gate. OCR will miss, invent, and mangle items — the user must confirm before
anything reaches history. This is the single most important screen in the feature.

### Layout (mobile-first, single column)

- **Header:** shop + date, both editable; running item count; total (editable).
- **"View original photo"** collapsible, so the user can cross-check against the paper receipt.
  On wide screens, show the photo pinned beside the list instead of collapsed.
- **Line-item list.** Each row:
  - Editable **name** with the same autocomplete used elsewhere, so messy OCR text can snap to a
    known item.
  - Editable **quantity** + **unit**.
  - Editable **price** (optional).
  - **Confidence flag:** low-confidence rows get an amber dot and sort to the top, so the user fixes
    the risky ones first (maps to `receipt_items.confidence`).
  - **Canonical-match indicator** (see §4.1).
  - Per-row **delete** for OCR junk ("SUBTOTAL", "CLUBCARD", "CHANGE DUE").
- **"+ Add item"** row for things OCR missed entirely.
- **Sticky footer:** "Discard receipt" (secondary) and **"Save N items to history"** (primary).

### Row interaction details (refined)

- **Swipe-left to delete** a row on mobile (matches common list patterns); tap-to-edit fields inline.
- **Undo toast** after any row delete ("Removed 'CLUBCARD' — Undo"), because OCR junk and real items
  can look alike and mis-deletes happen.
- **Bulk affordance** at the bottom: "N rows hidden as offers/totals — review" expands the excluded
  rows (see edge cases) so nothing is silently dropped.
- **Keyboard/scanner ergonomics:** Enter on a field moves to the next row's name, so a user can run
  top-to-bottom quickly on a phone.
- **Dirty-state guard:** if the user navigates away with unsaved edits, confirm "Discard your
  changes to this receipt?"
- **Per-row reset:** a small "revert" on an edited row restores the original OCR value, since people
  sometimes "correct" a row that was actually right.

### 4.1 Item canonicalisation — UX only (server owns identity)

Per Codex, the **server owns the matching algorithm and thresholds**; the frontend only presents the
server's proposal and lets the user confirm or override. So the UX contract is:

- For each row, the server returns a *proposed* canonical item (or "new item") plus a confidence.
  The frontend renders this as a quiet, tappable chip on the row, e.g. **"≈ semi skimmed milk"** or
  **"+ new item"**.
- **High confidence:** show the matched item name subtly; no action needed. The user can still tap to
  override.
- **Medium confidence:** show a gentle confirm affordance — "Is this *semi skimmed milk*?" with
  yes / pick-another. Never auto-merge silently.
- **Low / no match:** default to "+ new item"; tapping opens the same autocomplete to attach it to an
  existing item if the user prefers.
- **Override UI:** tapping the chip opens autocomplete over known canonical items; selecting one sets
  `receipt_items.item_id`; "keep as new" creates a new canonical item. The frontend reports the
  user's choice; it does **not** decide identity rules itself.
- `raw_text` is always preserved and shown on demand ("Originally read as: TESCO SEMI SKIMMED MILK
  2.27L"), so a correction never destroys the audit trail.

Product guidance to pass to Codex for the server rules (recommendation, not a decision):
- Treat `milk`, `semi skimmed milk`, and `whole milk` as **distinct** canonical items. For a shopping
  app, a wrong merge is worse than a missed one — under-merge rather than over-merge.

### Edge cases / copy

- **Totally unreadable receipt:** skip the grid; show "We couldn't read this receipt. You can keep
  the photo and add items manually, or discard it." Offer manual add + discard.
- **Discounts / multibuy / loyalty / points lines:** default to excluding them as non-items, but list
  them greyed at the bottom under "Not added (offers, totals, points)" so the user can promote one
  that was actually a product.
- **Mixed shops on one receipt:** out of scope; assume one receipt = one shop.

---

## 5. Accepting receipt items into history

**Goal:** turn a reviewed receipt into durable, queryable history the user trusts.

On pressing **"Save N items to history"**:
- Optimistically mark the receipt **"Saved"** and toast "Saved N items to history."
- Each accepted row becomes a `shopping_trip_item` under a `shopping_trip` for that shop/date, and
  flips `receipt_items.accepted = true`. **[Codex owns]** the exact write path.
- The receipt stays viewable in the Receipts tab as a saved record (with its photo), so the user can
  later audit "what did that £52 shop actually contain".
- Allow correcting a saved item after the fact (people notice mistakes later); edits update the trip
  item, not the original OCR text. `raw_text` stays immutable for audit.

### History from the normal list, not just receipts (refined per Codex's nuance)

Codex documented the current Apps Script behaviour: `updateItem(bought:true)` already calls
`addToHistory(...)`, but `clearBought()` deletes bought rows **without** writing history. The
takeaway for UX:

- **Marking an item bought on the Shopping tab already produces history** — good; the history view
  will not be empty before OCR ships.
- The gap is **clear-bought**: today it can silently drop items that were never individually marked
  bought. Recommendation for the DB-backed version: clear-bought should write one history/trip record
  per item before removing it, and the UI copy should reflect that it's *archiving to history*, not
  just deleting — e.g. button "Clear bought → history" with a subtitle "moves bought items into your
  shopping history."
- This is purely a copy/behaviour recommendation; **[Codex owns]** the server write.

---

## 6. Shopping-history placement (new — assigned task)

**Goal:** make history easy to reach and genuinely useful, without adding a fourth top-level tab.

Recommendation, with rationale:

- **Primary: a "History" sub-view inside the Receipts tab.** Receipts and history are the same mental
  model ("what we've bought"), and most history rows will originate from receipts or clear-bought.
  A segmented control at the top of the Receipts tab — **[ Receipts | History ]** — keeps the tab bar
  at four items max and groups the two naturally.
- **Secondary entry point: per-item history on demand.** Long-pressing (or a "⋯ → History") on any
  list/shopping item opens "Last bought: 14 Jun at Tesco · usually every ~5 days". This is where
  history pays off in the moment of shopping, so it shouldn't be buried only in a separate view.

History view structure:
- Grouped by trip, newest first: **"Tesco — 14 Jun — 23 items — £52.10"**, expandable to line items.
- Each line item links back to its source receipt (if any) and to that item's canonical history.
- Filters: by shop, and a simple date range. Search by item name reuses existing autocomplete.
- Empty state before any history: "Your shopping history will appear here once you mark items bought
  or save a receipt."

**[Codex owns]** whether `/api/history` returns trip-grouped or flat rows; the UX above assumes
trip-grouped with item drill-down, which matches the `shopping_trips` / `shopping_trip_items` schema.

Implementation note, 2026-06-30: the frontend scaffold now has a top-level **Receipts** tab with a
**[Receipts | History]** segmented control. The Receipts segment shows a (disabled) "Take photo" /
"Choose from library" pair, a "coming soon" caption, and a 1-2-3 **Upload → Review → Save to history**
lifecycle explainer. The History segment explains that marking items bought now starts feeding history,
with full trip grouping arriving alongside the database.

Both segments now also carry an inert, clearly-labelled **PREVIEW** skeleton of the future screens
(everything disabled, example rows tagged as such — no real/fake saved data):
- Receipts preview: a review card with editable-looking shop/date, a "View original photo" panel,
  status-copy chips (Uploading / Processing / Ready / Couldn't read), three item rows with
  name/qty/price + amber/green confidence dots + delete, an "+ Add item" row, an excluded-lines note,
  and a disabled "Save N items to history" footer.
- History preview: trip-grouped cards (shop · date · item count · total, expandable-looking) with
  example item rows.
All display-only — upload, review, and history data endpoints remain future work.

---

## 7. Suggested shopping list items

**Goal:** gently resurface things the household probably needs, without nagging or clutter.

Where suggestions appear:
- A dismissible **"Suggestions"** strip at the top of the List tab, shown only when suggestions
  exist. Not a separate tab — suggestions are only useful while building a list.
- Each suggestion is a chip: item name + a quiet reason ("usually every ~5 days, last bought 6 days
  ago") + **"+"** to add and **"✕"** to dismiss.

Behaviour:
- Tapping **+** adds the item to its usual shop's column via the existing optimistic add; the chip
  animates out.
- Tapping **✕** removes that suggestion for now and (per Codex) feeds a rejection signal so it nags
  less. **[Codex owns]** how feedback is stored/weighted.
- Suggestions are **household-global in the UI** (Codex decision); per-user purchase data can inform
  the cadence underneath without splitting the visible list.
- Cap visible suggestions (top 4–6) to avoid a wall of guesses; "Show more" expands.

Suggestion quality / copy (honest and boring, per the prediction strategy):
- Only suggest items with enough history to have a cadence; never invent.
- Always show *why* in plain language — an unexplained suggestion erodes trust fast.
- Prefer the user's usual shop (`items.default_shop_id`).
- Never auto-add. Suggestions are always opt-in.

Implementation note, 2026-06-30: a hidden **Suggestions strip** scaffold now sits at the top of the
List tab (label + "Hide" action + empty chip container), `display:none` by default. It establishes
placement and copy only and is never populated with invented suggestions; the prediction backend will
later reveal and fill it.

---

## Remaining open questions for Codex / Jamie

Most earlier questions are now decided (see top section). Still open:

1. **Clear-bought copy/behaviour** — confirm clear-bought should archive to history (§5) rather than
   silently delete, once DB-backed.
2. **Receipt image retention** — kept forever / deleted after extraction / configurable? (Open in the
   plan; affects the "View original photo" affordance and the saved-receipt audit value. Claude
   leans "kept, with an optional cleanup later" for audit, but this is Codex/Jamie's call.)
3. **Per-item history entry point** — is long-press acceptable on mobile, or prefer an explicit "⋯"
   menu? (Claude leans explicit menu for discoverability.)

## Files this would later touch (for the implementation lane, not now)

- `docs/index.html` — Receipts tab + History segment is scaffolded; future work adds upload/review screens and suggestions strip.
- `docs/app.js` — auth state, receipt upload/review/accept, history rendering, suggestions.
- `docs/style.css` — new screens reusing existing tokens/patterns.
- Backend (Codex lane) — auth, receipts, history, suggestions endpoints per the plan's API shape.

Design note for Codex review. The first scaffold has been added separately in `docs/`; full receipt/history functionality remains unimplemented.
