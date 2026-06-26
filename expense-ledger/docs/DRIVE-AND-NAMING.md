# Google Drive folders & receipt naming

The ledger is only as good as the proof behind it. This is the simplest system
that keeps a receipt/invoice for **every** row and makes it findable in seconds.

---

## Recommended Drive folder structure

```
📁 Business Finances/
├── 📄 Business Expense Ledger        ← the Google Sheet itself
│
├── 📁 Receipts & Evidence/
│   ├── 📁 2026/
│   │   ├── 📁 2026-01 January/
│   │   ├── 📁 2026-02 February/
│   │   ├── 📁 2026-03 March/
│   │   │   └── … through 2026-12 December
│   └── 📁 2025/
│
├── 📁 Bank & Card Statements/
│   ├── 📁 2026/
│   └── 📁 2025/
│
├── 📁 Contracts & Agreements/        ← gig contracts, vendor agreements, W-9s
│
├── 📁 Mileage Logs/                  ← monthly/annual mileage sheets or photos
│
└── 📁 Tax Exports/                   ← what you send the accountant each year
    ├── 📁 2025 Tax Year/
    └── 📁 2026 Tax Year/
```

**Why this shape**
- **One folder per month** keeps each folder small and matches the `Month`
  column in the ledger — easy to reconcile against a statement.
- **Statements separate from receipts** so a card statement (covering many
  expenses) isn't mixed in with individual receipts.
- **Tax Exports** is the clean hand-off folder: each year you drop in the
  Accountant Export PDF/CSV plus the year's statements.

> Receipts going into Drive aren't deductible by *being* in Drive — they're your
> proof if anyone ever asks. Keep them for as long as your tax authority
> requires (commonly ~7 years; confirm with your accountant).

---

## File-naming convention

```
YYYY-MM-DD_Vendor_Amount_Category_ExpenseID.ext
```

**Examples**
```
2026-06-14_Sweetwater_249.00_DJEquipment_EXP-00042.pdf
2026-06-03_Adobe_59.99_Software_EXP-00031.pdf
2026-06-21_Uber_18.40_Travel_EXP-00055.jpg
2026-06-30_ChaseBiz_Statement_EXP-NA.pdf      ← a statement (no single ID)
```

**Rules**
1. **Date first, ISO format** (`YYYY-MM-DD`) so files sort chronologically.
2. **Vendor** with no spaces — `GuitarCenter`, `Sweetwater`, `Adobe`.
3. **Amount** with no currency symbol — `249.00`.
4. **Category** short, no spaces — `DJEquipment`, `Software`, `Travel`.
5. **Expense ID last** (`EXP-00042`) — this is the link back to the ledger row.
   Use `EXP-NA` for files that aren't a single expense (e.g. a statement).
6. Separate fields with underscores `_`; keep the original extension.

**The payoff:** the filename alone tells you everything, and you can search
Drive for `EXP-00042` to jump straight to a row's proof — or search the ledger's
`Evidence Link` to jump to the file. They point at each other.

---

## The 60-second weekly evidence routine

1. **Pay / get the receipt.** Snap a photo, save the PDF, or forward the email
   confirmation.
2. **Drop it** in the right month folder under `Receipts & Evidence/2026/…`.
3. **Rename it** using the convention above (include the `Expense ID` from the
   ledger row you just added).
4. **Right-click ▸ Share ▸ Copy link**, then paste it into the row's
   **Evidence Link** cell.
5. Set **Evidence Status = Attached**. The row turns green-ready once Tax Review
   Status is `Ready` too — and it'll appear in **Accountant Export**.

**No receipt exists?** (some bank fees, auto-charges)
→ set **Evidence Status = Not Applicable** and note why. Those still count as
clean for the accountant export.

**Waiting on a vendor?** → **Evidence Status = Requested** (shows yellow in
*Missing Evidence* until it arrives).

---

## Tips

- **Phone shortcut:** add the current month's Drive folder to your phone's
  Google Drive *Starred* so you can drop a photo in seconds at the gig.
- **Auto-forward receipts:** set up a Gmail filter to label email receipts
  (e.g. `Receipts`) so nothing slips through; attach the PDF or screenshot to
  the ledger weekly.
- **Statements are backup, not primary.** Use individual receipts where you can;
  fall back to the highlighted line on a `Bank/Card Statement` when a receipt is
  genuinely unavailable.
- **Mileage** isn't a receipt — keep a running **Mileage Log** (date, miles,
  from→to, purpose) in `Mileage Logs/` and link it; set Evidence Type =
  `Mileage Log`.
