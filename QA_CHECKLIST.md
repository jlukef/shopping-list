# Phase 4 Acceptance Checklist

Use this checklist to manually verify application behaviour for the SQLite clean-start migration.

## First Login & Fresh State
- [ ] **Expected Outcome:** Upon first login, the user sees an empty shopping list. There is no prompt to configure Google Sheets or Apps Script.
- [ ] **Expected Outcome:** The list of shops defaults to exactly seven entries: Morrisons, Aldi, Lidl, Butcher, Fruit and Veg Shop, Boots/Superdrug, Other.
- [ ] **Expected Outcome:** The UI empty states and first-use copy invite the user to add their first item, without mentioning migration or missing configuration.

## Core CRUD
- [ ] **Expected Outcome:** User can add a new item to the list, and it persists in the SQLite database.
- [ ] **Expected Outcome:** User can edit an item's quantity and unit, and changes persist.
- [ ] **Expected Outcome:** User can mark an item as bought, and the UI immediately reflects it.
- [ ] **Expected Outcome:** User can delete an item from the list completely.

## History Integrity
- [ ] **Expected Outcome:** When an item is marked bought for the first time, a history record is created with the item's name, quantity, unit, shop, and UTC timestamp.
- [ ] **Expected Outcome:** Repeatedly toggling an item to `bought:true` (e.g., unchecking and checking again) does not create duplicate history records.
- [ ] **Expected Outcome:** Clearing bought items (`clearBought`) removes them from the active list without duplicating history already recorded.

## Sort & Layout Behavior
- [ ] **Expected Outcome:** Items are sorted locally based on the seeded keyword layouts for each shop.
- [ ] **Expected Outcome:** Autocomplete (`getAutocomplete`) works from the new SQLite `items` dictionary.
- [ ] **Expected Outcome:** No external AI dependency is called for sorting, and no API keys are exposed to the browser.

## Restart Persistence
- [ ] **Expected Outcome:** Restarting the FastAPI server does not lose data (shopping list, items, history, or active sessions).

## Legacy Fallback
- [ ] **Expected Outcome:** Legacy static/Sheets mode still functions correctly if explicitly configured in settings, ensuring an immediate fallback if needed.

## Hosted/VPS (Requires Jamie Approval - Not Run Locally)
- [ ] **Expected Outcome:** The application runs exclusively on `127.0.0.1:8770` behind Caddy.
- [ ] **Expected Outcome:** Security/Privacy rules apply: `/robots.txt` disallows all crawling, `X-Robots-Tag` is sent, no secrets committed.
- [ ] **Expected Outcome:** The app is separate from the tax app environment.
- [ ] **Expected Outcome:** Rollback: User can revert to the Google Apps Script version smoothly if major blockers appear.
