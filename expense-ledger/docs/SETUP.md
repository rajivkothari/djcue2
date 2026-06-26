# Setup Guide

Two ways to build the workbook:

- **Option A — Apps Script (recommended).** Paste one file, click Run, done.
  Builds all 7 tabs with dropdowns, conditional formatting, formulas, the
  auto Expense ID / Month / timestamps, and the custom menu.
- **Option B — Manual build.** No script. You create the tabs and dropdowns by
  hand. Slower, and you lose auto-IDs/timestamps, but it works.

---

## Option A — Apps Script (recommended)

### 1. Create the spreadsheet
Go to **[sheets.new](https://sheets.new)** (or Drive ▸ New ▸ Google Sheets).
Rename it something like **Business Expense Ledger 2026**.

### 2. Open the script editor
**Extensions ▸ Apps Script.** A new tab opens with a `Code.gs` containing an
empty `myFunction()`.

### 3. Paste the code
Select all the placeholder code and delete it. Open
[`../apps-script/Code.gs`](../apps-script/Code.gs), copy the **entire** file,
paste it in, and click **💾 Save**.

### 4. Run `setup`
At the top, set the function dropdown to **`setup`** and click **▶ Run**.

### 5. Approve authorization (one time)
Google will ask you to authorize the script:

1. Click **Review permissions** and pick your Google account.
2. You may see *"Google hasn't verified this app."* This is normal for a
   personal script that you pasted yourself — it isn't published. Click
   **Advanced ▸ Go to (project name) (unsafe)**.
3. Click **Allow**.

> **Why does it need permission?** The script only edits **this spreadsheet**
> (creating tabs, dropdowns, formatting, and stamping IDs/timestamps). It does
> not touch your other files, email, or anything else. You can read every line
> in `Code.gs` first.

Run finishes in a few seconds — you'll see a *"Setup complete"* toast in the
sheet.

### 6. Reload to get the menu
Switch back to the spreadsheet and **reload the browser tab**. A new
**💼 Expense Ledger** menu appears next to *Help*. Setup is finished.

### 7. Try it
- Click **💼 Expense Ledger ▸ Add expense row**, type a **Date**, and watch the
  **Expense ID**, **Month**, and timestamps fill in.
- Two clearly-labeled **EXAMPLE** rows are pre-filled so you can see the colors
  and the auto-tabs working. **Delete them** once you've looked
  (select the rows ▸ right-click ▸ Delete rows).

### Using it day to day

| Menu item | What it does |
|---|---|
| **① Set up / rebuild workbook** | Re-runs the builder. Safe — it never erases data you typed into Expenses or Categories. |
| **➕ Add expense row** | Jumps to the next empty row, cursor on Date. |
| **🔄 Refresh IDs, Months & timestamps** | Backfills any blanks. **Run this after a large copy/paste import** (the auto-fill skips pastes over 40 rows for speed). |
| **🔴 / 🟡 / 🟢 Go to …** | Jump to Missing Evidence / Needs Review / Accountant Export. |

### Optional tweaks (top of `Code.gs`)
- **Currency:** change `currencyFmt` (e.g. `'£#,##0.00'`, `'€#,##0.00'`).
- **Time zone:** set it in `appsscript.json` **and** in the sheet
  (File ▸ Settings ▸ Time zone) so Months land on the right day.
- **Add categories:** just type them into column A of the **Categories** tab —
  no code change needed; they appear in the dropdown automatically.

---

## Option B — Manual build (no script)

You won't get auto Expense IDs / Month / timestamps or the menu, but the
structure, dropdowns, color-coding, and auto-tabs all still work.

### 1. Create the tabs
Make 7 tabs named exactly: `Expenses`, `Categories`, `Monthly Summary`,
`Missing Evidence`, `Needs Review`, `Accountant Export`, `Instructions`.

### 2. Expenses headers (row 1)
Paste this across `A1:T1`, then **View ▸ Freeze ▸ 1 row**:

```
Expense ID	Date	Month	Vendor / Merchant	Amount	Category	Subcategory	Business Purpose	Client / Project	Payment Method	Paid From Account	Evidence Type	Evidence Link	Evidence Status	Tax Review Status	Reimbursable?	Reimbursed?	Notes	Date Added	Last Updated
```

(That's tab-separated — paste into A1 and it spreads across the row.)

### 3. Auto Month from Date (formula version)
Put this in **C2** (instead of script-filled values):

```
=ARRAYFORMULA(IF(B2:B="","",TEXT(B2:B,"yyyy-mm")))
```

For Expense IDs without a script, type them yourself (`EXP-00001`, …) or use
`="EXP-"&TEXT(ROW()-1,"00000")` in A2 and fill down.

### 4. Categories tab
In `A1`: `Category`. List all categories down column A starting `A2`
(Advertising & Marketing, Software / Subscriptions, … Other / Needs Review).

### 5. Dropdowns (Data ▸ Data validation)
Select the column range (e.g. `F2:F1000`) → **Data ▸ Data validation** →
*Criteria: Dropdown (from a range / list of items)*:

- **Category** (`F`): from range `Categories!A2:A200`
- **Payment Method** (`J`): `Business Credit Card, Business Debit Card, Business Bank Account, Personal Credit Card, Personal Debit Card, Cash, PayPal, Venmo, Zelle, Other`
- **Evidence Type** (`L`): `Receipt, Invoice, Email Confirmation, Bank/Card Statement, Screenshot, Mileage Log, Contract / Agreement, Other`
- **Evidence Status** (`N`): `Attached, Missing, Requested, Not Applicable, Needs Review`
- **Tax Review Status** (`O`): `Ready, Needs Review, Ask Accountant, Exclude, Personal / Non-Business`
- **Reimbursable?** (`P`) and **Reimbursed?** (`Q`): `Yes, No`

### 6. Conditional formatting (Format ▸ Conditional formatting)
On **Evidence Status** (`N2:N1000`), *Text is exactly*:
`Missing` → red fill; `Needs Review` / `Requested` → yellow; `Attached` → green.

On **Tax Review Status** (`O2:O1000`), *Text is exactly*:
`Ready` → green; `Ask Accountant` / `Needs Review` → yellow;
`Exclude` / `Personal / Non-Business` → gray.

### 7. Auto-tabs (paste these formulas in cell A1 — or A3 with your own headers)

**Monthly Summary** (totals by month, then by category):
```
=QUERY(Expenses!A2:T,"select C, sum(E) where C is not null and E is not null group by C order by C label sum(E) 'Total'",0)
=QUERY(Expenses!A2:T,"select F, sum(E) where F is not null and E is not null group by F order by sum(E) desc label sum(E) 'Total'",0)
```

**Missing Evidence:**
```
=QUERY(Expenses!A2:T,"select A,B,C,D,E,F,L,M,N,R where N='Missing' or N='Requested' or N='Needs Review' order by B",0)
```

**Needs Review:**
```
=QUERY(Expenses!A2:T,"select A,B,C,D,E,F,O,H,N,R where O='Needs Review' or O='Ask Accountant' order by B",0)
```

**Accountant Export:**
```
=QUERY(Expenses!A2:T,"select A,B,C,D,E,F,G,H,I,J,L,M,R where (N='Attached' or N='Not Applicable') and O='Ready' order by B",0)
```

### 8. Polish
- Format **Amount** (`E`) as currency, **Date** (`B`) as a date.
- **View ▸ Freeze ▸ 1 row** on every tab.
- **Data ▸ Create a filter** on Expenses.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| No **💼 Expense Ledger** menu | Reload the browser tab. If still missing, re-run `setup` from the Apps Script editor. |
| IDs/Months didn't fill after a paste | Run **💼 Expense Ledger ▸ Refresh IDs, Months & timestamps**. |
| Month is blank | The **Date** cell must be a real date, not text. Re-type it, or check File ▸ Settings ▸ Locale. |
| Auto-tab shows `#REF!`/`#VALUE!` | Don't type into the auto tabs — they're formula-driven. Re-run `setup` to restore them. |
| Dropdown rejects a value | It must match exactly. Add new options in **Categories** (for Category) or adjust the list in `Code.gs`. |
| `#N/A` "no results" in an auto tab | That just means nothing matches yet (e.g. no missing evidence). It resolves itself once matching rows exist. |
