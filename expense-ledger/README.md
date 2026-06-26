# Business Expense Ledger (Google Sheets)

A clean, **accountant-ready** expense tracker. Every business expense gets one
row in the ledger and a linked receipt/invoice. Simple enough to maintain in
~15 minutes a week, structured enough to hand to your accountant at tax time.

> This is **not** full accounting software. It's a tidy ledger with proof
> attached, so a tax professional can review your expenses fast.

---

## What you get

A single Google Sheets workbook with **7 tabs**:

| Tab | What it's for | You edit it? |
|-----|---------------|:---:|
| **Expenses** | The main ledger — one row per expense | ✅ Yes |
| **Categories** | Your category list + tax hints (feeds the dropdown) | ✅ Yes |
| **Monthly Summary** | Dashboard + totals by month and by category | ⚙️ Auto |
| **Missing Evidence** | Rows still missing a receipt/invoice | ⚙️ Auto |
| **Needs Review** | Rows flagged for you or your accountant | ⚙️ Auto |
| **Accountant Export** | Only clean, ready-to-file rows — *this is what you send* | ⚙️ Auto |
| **Instructions** | How to use it (also lives in the sheet) | ⚙️ Auto |

**Automated for you:**

- 🆔 **Expense ID** auto-generated (`EXP-00001`, `EXP-00002`, …)
- 📅 **Month** auto-filled from the Date (e.g. `2026-06`)
- ⏱️ **Date Added** / **Last Updated** timestamps
- 🎨 **Conditional formatting** (red = missing evidence, yellow = needs review,
  green = ready, gray = personal/excluded)
- ⬇️ **Dropdowns**, currency/date formats, frozen header, and a filter

---

## Quick start (≈ 2 minutes)

1. Create a new Google Sheet → [sheets.new](https://sheets.new). Name it
   **Business Expense Ledger**.
2. **Extensions ▸ Apps Script**.
3. Delete the placeholder code, paste in
   [`apps-script/Code.gs`](apps-script/Code.gs), and **Save** (💾).
4. In the function dropdown pick **`setup`** → click **▶ Run**.
5. Approve the one-time authorization (it's your own script running in your
   account — see [docs/SETUP.md](docs/SETUP.md) if the warning looks scary).
6. Go back to the sheet and **reload the browser tab**. A **💼 Expense Ledger**
   menu appears. Done.

Full walkthrough (with the "is this safe?" explanation and a no-script manual
fallback): **[docs/SETUP.md](docs/SETUP.md)**.

---

## The weekly habit (the whole point)

**Each week (~15 min):**
1. `💼 Expense Ledger ▸ Add expense row` (or just type on the next empty row).
2. Fill **Date, Vendor, Amount, Category, Business Purpose, Payment Method**.
3. Save the receipt to Drive, paste its share link into **Evidence Link**.
4. Set **Evidence Type** + **Evidence Status** (`Attached` once it's linked).
5. Set **Tax Review Status**: `Ready` if you're sure, else `Needs Review` /
   `Ask Accountant`.

**Each month (~30 min):** clear out the **Missing Evidence** and **Needs
Review** tabs, then sanity-check **Monthly Summary** against your statements.

**At tax time:** open **Accountant Export** → **File ▸ Download ▸ PDF/CSV** →
send. It already contains only clean, ready rows.

---

## Storing receipts

A receipt/invoice for every row is the entire value of this system. See
**[docs/DRIVE-AND-NAMING.md](docs/DRIVE-AND-NAMING.md)** for:

- the recommended **Google Drive folder structure**, and
- a **file-naming convention** that ties each file back to its `Expense ID`,
  e.g. `2026-06-14_Sweetwater_249.00_DJEquipment_EXP-00042.pdf`.

---

## The Expenses columns

`Expense ID` · `Date` · `Month` · `Vendor / Merchant` · `Amount` · `Category` ·
`Subcategory` · `Business Purpose` · `Client / Project` · `Payment Method` ·
`Paid From Account` · `Evidence Type` · `Evidence Link` · `Evidence Status` ·
`Tax Review Status` · `Reimbursable?` · `Reimbursed?` · `Notes` · `Date Added` ·
`Last Updated`

Dropdown fields: Category, Payment Method, Evidence Type, Evidence Status,
Tax Review Status, Reimbursable?, Reimbursed?. (Subcategory is free text.)

---

## Files in this folder

```
expense-ledger/
├── README.md                     ← you are here
├── apps-script/
│   ├── Code.gs                   ← paste this into Apps Script, run setup()
│   └── appsscript.json           ← Apps Script project manifest
└── docs/
    ├── SETUP.md                  ← step-by-step install (+ manual fallback)
    └── DRIVE-AND-NAMING.md       ← Drive folders + receipt naming convention
```

> ⚠️ The tax hints in the Categories tab are **general guidance, not tax
> advice.** Confirm anything uncertain with your accountant — that's exactly
> what the `Needs Review` and `Ask Accountant` statuses are for.
