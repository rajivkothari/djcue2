/**
 * ============================================================================
 *  BUSINESS EXPENSE LEDGER  —  Google Sheets builder + helpers
 * ============================================================================
 *
 *  WHAT THIS DOES
 *  --------------
 *  Running setup() once turns a blank Google Spreadsheet into a clean,
 *  accountant-ready expense tracker with 7 tabs:
 *
 *    1. Expenses          – the main ledger (one row per expense)
 *    2. Categories        – editable category list + tax hints (feeds dropdown)
 *    3. Monthly Summary   – dashboard + month / category totals (auto)
 *    4. Missing Evidence  – rows missing a receipt/invoice (auto)
 *    5. Needs Review      – rows flagged for you or your accountant (auto)
 *    6. Accountant Export – only clean, ready-to-file rows (auto)
 *    7. Instructions      – how to use it
 *
 *  It also:
 *    - auto-generates an Expense ID (EXP-00001, EXP-00002, ...)
 *    - auto-fills the Month from the Date (e.g. 2026-06)
 *    - stamps Date Added + Last Updated
 *    - adds dropdowns, conditional formatting, currency/date formats,
 *      a frozen header row and a filter.
 *
 *  HOW TO INSTALL  (≈ 2 minutes)
 *  -----------------------------
 *    1. Create a new Google Sheet (sheets.new) and name it
 *       e.g. "Business Expense Ledger".
 *    2. Extensions ▸ Apps Script.
 *    3. Delete any sample code, paste THIS whole file, and Save.
 *    4. In the function dropdown choose "setup" and click ▶ Run.
 *    5. Approve the one-time authorization prompt (it is your own script).
 *    6. Return to the sheet. Reload the tab once so the
 *       "💼 Expense Ledger" menu appears.
 *
 *  After that, just type expenses into the Expenses tab — everything else
 *  updates itself. See the SETUP.md doc for screenshots-style detail.
 * ============================================================================
 */

/* ----------------------------- CONFIG ------------------------------------ */

const CFG = {
  currencyFmt: '$#,##0.00',   // change to your currency, e.g. '£#,##0.00'
  dateFmt:     'yyyy-mm-dd',
  monthFmt:    'yyyy-MM',     // how the Month column is stored (sorts cleanly)
  tsFmt:       'yyyy-mm-dd hh:mm',
  idPrefix:    'EXP-',
  idPad:       5,             // EXP-00001
  maxRows:     2000,          // how far down dropdowns / formatting reach
};

// Colors used for the header and conditional formatting.
const COLORS = {
  headerBg: '#0b5394', headerFg: '#ffffff',
  redBg:    '#f4cccc', redFg:    '#990000',
  yellowBg: '#fff2cc', yellowFg: '#7f6000',
  greenBg:  '#d9ead3', greenFg:  '#274e13',
  grayBg:   '#d9d9d9', grayFg:   '#666666',
  band:     '#f3f6fb',
  title:    '#073763',
};

const SHEET = {
  EXPENSES:     'Expenses',
  CATEGORIES:   'Categories',
  SUMMARY:      'Monthly Summary',
  MISSING:      'Missing Evidence',
  REVIEW:       'Needs Review',
  EXPORT:       'Accountant Export',
  INSTRUCTIONS: 'Instructions',
};

// Main ledger columns, in order.
const EXPENSE_HEADERS = [
  'Expense ID', 'Date', 'Month', 'Vendor / Merchant', 'Amount',
  'Category', 'Subcategory', 'Business Purpose', 'Client / Project',
  'Payment Method', 'Paid From Account', 'Evidence Type', 'Evidence Link',
  'Evidence Status', 'Tax Review Status', 'Reimbursable?', 'Reimbursed?',
  'Notes', 'Date Added', 'Last Updated',
];
const DATA_START_ROW = 2;

// 1-based column positions (keep in sync with EXPENSE_HEADERS above).
const COL = {
  ID: 1, DATE: 2, MONTH: 3, VENDOR: 4, AMOUNT: 5, CATEGORY: 6, SUBCATEGORY: 7,
  PURPOSE: 8, CLIENT: 9, PAYMENT: 10, ACCOUNT: 11, EVTYPE: 12, EVLINK: 13,
  EVSTATUS: 14, TAXSTATUS: 15, REIMBURSABLE: 16, REIMBURSED: 17, NOTES: 18,
  ADDED: 19, UPDATED: 20,
};

// Dropdown option lists.
const LIST = {
  evidenceType:   ['Receipt', 'Invoice', 'Email Confirmation', 'Bank/Card Statement',
                   'Screenshot', 'Mileage Log', 'Contract / Agreement', 'Other'],
  evidenceStatus: ['Attached', 'Missing', 'Requested', 'Not Applicable', 'Needs Review'],
  taxStatus:      ['Ready', 'Needs Review', 'Ask Accountant', 'Exclude', 'Personal / Non-Business'],
  payment:        ['Business Credit Card', 'Business Debit Card', 'Business Bank Account',
                   'Personal Credit Card', 'Personal Debit Card', 'Cash',
                   'PayPal', 'Venmo', 'Zelle', 'Other'],
  yesNo:          ['Yes', 'No'],
};

// Categories tab content: [Category, Examples / typical subcategories, Tax hints].
const CATEGORY_ROWS = [
  ['Advertising & Marketing', 'Flyers, social ads, promo photos, business cards, sponsored posts', 'Generally fully deductible. Keep the ad receipt or campaign screenshot.'],
  ['Software / Subscriptions', 'Serato, rekordbox, Adobe, Canva, Dropbox, accounting apps', 'Deduct monthly/annual fees. Note business-use % if also personal.'],
  ['Office Supplies', 'Paper, ink, pens, folders, shipping labels', 'Small consumables — keep receipts.'],
  ['Equipment', 'General business equipment and gear', 'Larger items may need to be depreciated rather than expensed — Ask Accountant.'],
  ['Meals', 'Client/vendor meals, meals while traveling for gigs', 'Often 50% deductible. Note who you met and the business reason.'],
  ['Travel', 'Flights, hotels, rideshare, parking for out-of-town gigs', 'Must be business travel. Keep itineraries and receipts.'],
  ['Mileage', 'Business miles driven to/from gigs', 'Use the standard mileage rate. Log date, miles and purpose (see Mileage Log).'],
  ['Contractors', 'MCs, lighting techs, second DJs, editors you pay', 'May require a 1099 if paid $600+ in a year. Keep invoices and a W-9.'],
  ['Professional Services', 'Accountant, lawyer, bookkeeper fees', 'Deductible professional fees.'],
  ['Education / Training', 'Courses, masterclasses, conferences, tutorials', 'Must maintain or improve skills for your current business.'],
  ['Phone / Internet', 'Cell phone, home internet (business portion)', 'Deduct the business-use percentage only.'],
  ['Bank Fees', 'Monthly account fees, wire fees, overdraft', 'Business account fees are deductible.'],
  ['Payment Processing Fees', 'Square, Stripe, PayPal, Venmo business fees', 'Deduct the processor fees taken out of your gig income.'],
  ['Insurance', 'Liability, equipment and business insurance', 'Business insurance premiums.'],
  ['Dues & Memberships', 'DJ associations, performing-rights orgs, pro memberships', 'Professional dues only (not personal social clubs).'],
  ['Shipping / Postage', 'Shipping gear, mailing contracts, postage', 'Keep shipping receipts.'],
  ['Repairs / Maintenance', 'Gear servicing, cable/connector repair', 'Repairs to business property.'],
  ['Home Office', 'Portion of rent/utilities for a dedicated studio/office', 'Strict rules apply — track sq ft and %, and Ask Accountant.'],
  ['Music / Licensing', 'Music pools, track purchases, sample packs, licensing', 'Music bought for your sets and business.'],
  ['DJ Equipment', 'Controllers, mixers, turntables, headphones, flight cases', 'Large purchases may be depreciated — Ask Accountant.'],
  ['Lighting Equipment', 'Moving heads, par cans, lasers, fog machines, stands', 'Large purchases may be depreciated — Ask Accountant.'],
  ['Event Supplies', 'Gaffer tape, batteries, zip ties, table covers, signage', 'Consumables used at events.'],
  ['Vehicle / Gig Travel', 'Tolls, parking, fuel for gigs (if NOT using Mileage)', 'Do not double-count with Mileage — pick one method per trip.'],
  ['Cloud Hosting', 'AWS, GCP, web hosting, storage for your site/app', 'Business hosting costs.'],
  ['AI Tools', 'ChatGPT, Claude, AI music tools, API usage', 'Business AI subscriptions and usage.'],
  ['Data Services', 'APIs, datasets, analytics, market data', 'Data used in your business.'],
  ['Domain / Website', 'Domain renewals, Squarespace/Wix, plugins, SSL', 'Website costs for your DJ business.'],
  ['Other / Needs Review', 'Anything that does not fit a category above', 'Set Tax Review Status to Needs Review or Ask Accountant.'],
];

/* ============================== SETUP ===================================== */

/**
 * One-time (and safe-to-re-run) builder. Builds/repairs every tab.
 * Re-running will NOT erase data you typed into Expenses or Categories.
 */
function setup() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  buildCategories_(ss);     // build first: Expenses' Category dropdown points here
  buildExpenses_(ss);
  buildMonthlySummary_(ss);
  buildMissingEvidence_(ss);
  buildNeedsReview_(ss);
  buildAccountantExport_(ss);
  buildInstructions_(ss);
  removeStockSheets_(ss);   // drop the leftover default "Sheet1" if it is empty
  orderTabs_(ss);
  ss.getSheetByName(SHEET.EXPENSES).activate();
  ss.toast('Setup complete. Reload the tab to load the "💼 Expense Ledger" menu.',
           'Business Expense Ledger', 8);
}

/* ---------------------------- EXPENSES TAB ------------------------------- */

function buildExpenses_(ss) {
  const sh = getOrCreateSheet_(ss, SHEET.EXPENSES);
  const cols = EXPENSE_HEADERS.length;

  // Header row.
  sh.getRange(1, 1, 1, cols).setValues([EXPENSE_HEADERS]);
  styleHeader_(sh, 1, cols);
  sh.setFrozenRows(1);
  if (sh.getMaxColumns() > cols) sh.deleteColumns(cols + 1, sh.getMaxColumns() - cols);

  // Column widths.
  const widths = [90, 95, 70, 165, 95, 165, 135, 230, 145, 155, 150,
                  140, 230, 120, 150, 105, 100, 250, 135, 135];
  widths.forEach((w, i) => sh.setColumnWidth(i + 1, w));

  // Number / date formats over the data area.
  const rows = CFG.maxRows;
  sh.getRange(DATA_START_ROW, COL.DATE, rows, 1).setNumberFormat(CFG.dateFmt);
  sh.getRange(DATA_START_ROW, COL.AMOUNT, rows, 1).setNumberFormat(CFG.currencyFmt);
  sh.getRange(DATA_START_ROW, COL.ADDED, rows, 1).setNumberFormat(CFG.tsFmt);
  sh.getRange(DATA_START_ROW, COL.UPDATED, rows, 1).setNumberFormat(CFG.tsFmt);
  sh.getRange(DATA_START_ROW, 1, rows, cols).setVerticalAlignment('middle');

  // Dropdowns.
  const catRange = ss.getSheetByName(SHEET.CATEGORIES).getRange('A2:A200');
  setValidationFromRange_(sh, COL.CATEGORY, catRange);
  setValidationFromList_(sh, COL.PAYMENT, LIST.payment);
  setValidationFromList_(sh, COL.EVTYPE, LIST.evidenceType);
  setValidationFromList_(sh, COL.EVSTATUS, LIST.evidenceStatus);
  setValidationFromList_(sh, COL.TAXSTATUS, LIST.taxStatus);
  setValidationFromList_(sh, COL.REIMBURSABLE, LIST.yesNo);
  setValidationFromList_(sh, COL.REIMBURSED, LIST.yesNo);

  // Conditional formatting (applied to the status columns).
  const evRange  = sh.getRange(DATA_START_ROW, COL.EVSTATUS, rows, 1);
  const taxRange = sh.getRange(DATA_START_ROW, COL.TAXSTATUS, rows, 1);
  sh.setConditionalFormatRules([
    // Evidence Status
    cfRule_('Missing',                 COLORS.redBg,    COLORS.redFg,    true,  evRange),
    cfRule_('Needs Review',            COLORS.yellowBg, COLORS.yellowFg, false, evRange),
    cfRule_('Requested',               COLORS.yellowBg, COLORS.yellowFg, false, evRange),
    cfRule_('Attached',                COLORS.greenBg,  COLORS.greenFg,  false, evRange),
    // Tax Review Status
    cfRule_('Ready',                   COLORS.greenBg,  COLORS.greenFg,  true,  taxRange),
    cfRule_('Ask Accountant',          COLORS.yellowBg, COLORS.yellowFg, false, taxRange),
    cfRule_('Needs Review',            COLORS.yellowBg, COLORS.yellowFg, false, taxRange),
    cfRule_('Exclude',                 COLORS.grayBg,   COLORS.grayFg,   false, taxRange),
    cfRule_('Personal / Non-Business', COLORS.grayBg,   COLORS.grayFg,   false, taxRange),
  ]);

  // Light row banding for readability (applied to data rows only, so it does
  // not overwrite the styled header; status colors sit on top of it).
  sh.getBandings().forEach(b => b.remove());
  sh.getRange(DATA_START_ROW, 1, rows, cols)
    .applyRowBanding(SpreadsheetApp.BandingTheme.LIGHT_GREY, false, false);

  // Filter on the header row.
  const existing = sh.getFilter();
  if (existing) existing.remove();
  sh.getRange(1, 1, rows + 1, cols).createFilter();

  // Seed two example rows only on a fresh sheet, then delete-me hint.
  if (sh.getLastRow() < DATA_START_ROW) {
    const ex = [
      [ '', new Date(2026, 5, 3), '', 'Adobe', 59.99, 'Software / Subscriptions',
        'Creative Cloud', 'Editing promo videos and flyers for gigs', '',
        'Business Credit Card', 'Amex Business ' + String.fromCharCode(8226).repeat(2) + '1234',
        'Receipt', '(paste the Drive share link here)', 'Attached', 'Ready', 'No', 'No',
        'EXAMPLE ROW - delete me. This one is clean, so it appears in Accountant Export.',
        '', '' ],
      [ '', new Date(2026, 5, 10), '', 'Guitar Center', 412.00, 'DJ Equipment',
        'Cables & stands', 'Replacement XLR cables + speaker stand for Saturday wedding',
        'Ramirez Wedding', 'Business Debit Card', 'Chase Biz ' + String.fromCharCode(8226).repeat(2) + '5678',
        'Receipt', '', 'Missing', 'Needs Review', 'No', 'No',
        'EXAMPLE ROW - delete me. No receipt yet, so it shows in Missing Evidence + Needs Review.',
        '', '' ],
    ];
    sh.getRange(DATA_START_ROW, 1, ex.length, cols).setValues(ex);
    for (let r = DATA_START_ROW; r < DATA_START_ROW + ex.length; r++) processRow_(sh, r);
  }
}

/* --------------------------- CATEGORIES TAB ------------------------------ */

function buildCategories_(ss) {
  const sh = getOrCreateSheet_(ss, SHEET.CATEGORIES);
  const headers = ['Category', 'Examples / Typical Subcategories', 'Tax Notes / Tips'];

  sh.getRange(1, 1, 1, headers.length).setValues([headers]);
  styleHeader_(sh, 1, headers.length);
  sh.setFrozenRows(1);

  // Only seed the list if the tab is empty, so we never clobber your edits.
  if (sh.getLastRow() < 2) {
    sh.getRange(2, 1, CATEGORY_ROWS.length, 3).setValues(CATEGORY_ROWS);
  }
  sh.setColumnWidth(1, 200);
  sh.setColumnWidth(2, 380);
  sh.setColumnWidth(3, 430);
  const used = Math.max(sh.getLastRow(), 2);
  sh.getRange(2, 1, used - 1, 3).setVerticalAlignment('top').setWrap(true);

  const note = sh.getRange(1, 5);
  note.setValue('Tip: add or rename categories in column A and they appear in the '
    + 'Expenses ▸ Category dropdown automatically. Tax notes are general guidance, '
    + 'not advice — confirm with your accountant.');
  note.setFontColor(COLORS.grayFg).setFontStyle('italic').setWrap(true);
  sh.setColumnWidth(5, 360);
}

/* ------------------------- MONTHLY SUMMARY TAB --------------------------- */

function buildMonthlySummary_(ss) {
  const sh = getOrCreateSheet_(ss, SHEET.SUMMARY);
  sh.clear();
  sh.getBandings().forEach(b => b.remove());

  title_(sh, 'A1:I1', 'Monthly Summary & Dashboard');
  sh.setFrozenRows(1);

  // --- KPI block (A3:B8) -------------------------------------------------
  sh.getRange('A3').setValue('Quick Numbers').setFontWeight('bold').setFontSize(12);
  const kpis = [
    ['Grand Total (all expenses)', '=SUM(Expenses!E2:E)'],
    ['Number of Expenses',         '=COUNTA(Expenses!A2:A)'],
    ['Missing Evidence (count)',   '=COUNTIF(Expenses!N2:N,"Missing")'],
    ['Needs Review / Ask Accountant',
        '=COUNTIF(Expenses!O2:O,"Needs Review")+COUNTIF(Expenses!O2:O,"Ask Accountant")'],
    ['Ready for Accountant Export',
        '=COUNTIFS(Expenses!N2:N,"Attached",Expenses!O2:O,"Ready")'
        + '+COUNTIFS(Expenses!N2:N,"Not Applicable",Expenses!O2:O,"Ready")'],
  ];
  for (let i = 0; i < kpis.length; i++) {
    const r = 4 + i;
    sh.getRange(r, 1).setValue(kpis[i][0]).setFontWeight('bold');
    sh.getRange(r, 2).setFormula(kpis[i][1]).setHorizontalAlignment('left');
  }
  sh.getRange('B4').setNumberFormat(CFG.currencyFmt);   // grand total = currency
  sh.getRange('B5:B8').setNumberFormat('0');            // the rest are counts

  // --- Section titles + headers (row 10 / 11) ---------------------------
  sh.getRange('A10').setValue('Totals by Month').setFontWeight('bold').setFontSize(12);
  sh.getRange('D10').setValue('Totals by Category').setFontWeight('bold').setFontSize(12);
  sh.getRange('G10').setValue('Month × Category').setFontWeight('bold').setFontSize(12);
  sh.getRange('A11:B11').setValues([['Month', 'Total']]);
  sh.getRange('D11:E11').setValues([['Category', 'Total']]);
  sh.getRange('G11:I11').setValues([['Month', 'Category', 'Total']]);
  [['A11:B11'], ['D11:E11'], ['G11:I11']].forEach(a =>
    sh.getRange(a[0]).setFontWeight('bold').setBackground('#e8eef7'));

  // --- QUERY blocks (row 12 down) ---------------------------------------
  sh.getRange('A12').setFormula(
    '=IFERROR(QUERY(Expenses!$A$2:$T,"select C, sum(E) where C is not null and E is not null '
    + 'group by C order by C label sum(E) \'\'",0),"No data yet")');
  sh.getRange('D12').setFormula(
    '=IFERROR(QUERY(Expenses!$A$2:$T,"select F, sum(E) where F is not null and E is not null '
    + 'group by F order by sum(E) desc label sum(E) \'\'",0),"No data yet")');
  sh.getRange('G12').setFormula(
    '=IFERROR(QUERY(Expenses!$A$2:$T,"select C, F, sum(E) where C is not null and E is not null '
    + 'group by C, F order by C, F label sum(E) \'\'",0),"No data yet")');

  // Currency on the total columns.
  sh.getRange('B12:B').setNumberFormat(CFG.currencyFmt);
  sh.getRange('E12:E').setNumberFormat(CFG.currencyFmt);
  sh.getRange('I12:I').setNumberFormat(CFG.currencyFmt);

  const w = [210, 130, 28, 210, 130, 28, 110, 210, 130];
  w.forEach((px, i) => sh.setColumnWidth(i + 1, px));
}

/* ----------------------- AUTO-FILTERED LIST TABS ------------------------- */

function buildMissingEvidence_(ss) {
  buildQueryTab_(ss, {
    name: SHEET.MISSING,
    title: 'Missing Evidence  —  attach a receipt/invoice, then update Evidence Status',
    headers: ['Expense ID', 'Date', 'Month', 'Vendor / Merchant', 'Amount',
              'Category', 'Evidence Type', 'Evidence Link', 'Evidence Status', 'Notes'],
    query: 'select A,B,C,D,E,F,L,M,N,R '
         + "where N='Missing' or N='Requested' or N='Needs Review' order by B",
    empty: 'Nothing here right now. Every expense has its evidence sorted out. ' + String.fromCharCode(0x2705),
    amountCol: 5,
  });
}

function buildNeedsReview_(ss) {
  buildQueryTab_(ss, {
    name: SHEET.REVIEW,
    title: 'Needs Review  —  decide the tax treatment, or ask your accountant',
    headers: ['Expense ID', 'Date', 'Month', 'Vendor / Merchant', 'Amount',
              'Category', 'Tax Review Status', 'Business Purpose', 'Evidence Status', 'Notes'],
    query: 'select A,B,C,D,E,F,O,H,N,R '
         + "where O='Needs Review' or O='Ask Accountant' order by B",
    empty: 'Nothing to review. ' + String.fromCharCode(0x2705),
    amountCol: 5,
  });
}

function buildAccountantExport_(ss) {
  buildQueryTab_(ss, {
    name: SHEET.EXPORT,
    title: 'Accountant Export  —  clean rows only (Evidence Attached/N-A AND Tax = Ready). '
         + 'File ▸ Download to send.',
    headers: ['Expense ID', 'Date', 'Month', 'Vendor / Merchant', 'Amount', 'Category',
              'Subcategory', 'Business Purpose', 'Client / Project', 'Payment Method',
              'Evidence Type', 'Evidence Link', 'Notes'],
    query: 'select A,B,C,D,E,F,G,H,I,J,L,M,R '
         + "where (N='Attached' or N='Not Applicable') and O='Ready' order by B",
    empty: 'No rows are accountant-ready yet. Mark expenses Evidence = Attached and Tax = Ready.',
    amountCol: 5,
  });
}

/**
 * Builds a read-only tab whose body is a single QUERY against Expenses.
 * cfg = {name, title, headers[], query, empty, amountCol}
 */
function buildQueryTab_(ss, cfg) {
  const sh = getOrCreateSheet_(ss, cfg.name);
  sh.clear();
  sh.getBandings().forEach(b => b.remove());
  const filt = sh.getFilter();
  if (filt) filt.remove();

  const n = cfg.headers.length;
  title_(sh, [1, 1, 1, n], cfg.title);

  sh.getRange(2, 1, 1, n).setValues([cfg.headers]);
  styleHeader_(sh, 2, n);
  sh.setFrozenRows(2);

  const q = '=IFERROR(QUERY(Expenses!$A$2:$T,"' + cfg.query + '",0),"' + cfg.empty + '")';
  sh.getRange(3, 1).setFormula(q);

  sh.getRange(3, cfg.amountCol, CFG.maxRows, 1).setNumberFormat(CFG.currencyFmt);
  sh.getRange(3, 2, CFG.maxRows, 1).setNumberFormat(CFG.dateFmt);  // Date col

  // Reasonable widths.
  const def = [95, 95, 70, 170, 95, 160, 150, 230, 150, 150, 150, 230, 250];
  for (let i = 0; i < n; i++) sh.setColumnWidth(i + 1, def[i] || 150);
}

/* --------------------------- INSTRUCTIONS TAB ---------------------------- */

function buildInstructions_(ss) {
  const sh = getOrCreateSheet_(ss, SHEET.INSTRUCTIONS);
  sh.clear();
  sh.getBandings().forEach(b => b.remove());
  sh.setHiddenGridlines(true);

  // Lines: '#' = big header, '*' = bold sub-header, '' = blank, else body.
  const lines = [
    '#Business Expense Ledger — How to Use This',
    '',
    'Goal: every business expense has one row here AND a linked receipt/invoice,',
    'so your accountant can review everything quickly at tax time.',
    'This is a clean ledger with proof attached — not full accounting software.',
    '',
    '*The 7 tabs',
    'Expenses          — the main ledger. You type here.',
    'Categories        — edit your category list + tax hints (feeds the dropdown).',
    'Monthly Summary   — dashboard + totals by month and category (auto).',
    'Missing Evidence  — rows still missing a receipt/invoice (auto).',
    'Needs Review      — rows flagged for you or your accountant (auto).',
    'Accountant Export — only clean, ready-to-file rows (auto). This is what you send.',
    'Instructions      — this page.',
    'Tip: the auto tabs are formula-driven. Type only in Expenses and Categories.',
    '',
    '*What fills in automatically',
    'Expense ID    — EXP-00001, EXP-00002 ... (generated when you add a row).',
    'Month         — taken from the Date (e.g. 2026-06).',
    'Date Added    — stamped once, when the row is first filled.',
    'Last Updated  — stamped every time you edit the row.',
    'After a big copy/paste import, run: 💼 Expense Ledger ▸ Refresh.',
    '',
    '*Weekly routine (about 15 minutes)',
    '1. Open the Expenses tab. Use 💼 Expense Ledger ▸ Add expense row, or just type on the next empty row.',
    '2. Fill Date, Vendor, Amount, Category, Business Purpose, Payment Method.',
    '3. Save the receipt to Drive (see the Drive & naming guide), then paste its link into Evidence Link.',
    '4. Set Evidence Type and Evidence Status (Attached once the file is linked).',
    '5. Set Tax Review Status: Ready if you are confident, otherwise Needs Review or Ask Accountant.',
    '',
    '*Monthly routine (about 30 minutes)',
    '1. Open Missing Evidence — chase or upload each receipt, then set Evidence Status = Attached.',
    '2. Open Needs Review — resolve each row (set it to Ready, Exclude, or leave a note for the accountant).',
    '3. Skim Monthly Summary to sanity-check totals against your card/bank statements.',
    '',
    '*Tax time',
    '1. Make sure Missing Evidence and Needs Review are empty (or down to known items).',
    '2. Open Accountant Export — it already shows only clean, ready rows.',
    '3. File ▸ Download ▸ PDF or CSV (or share the whole sheet) and send it to your accountant.',
    '',
    '*Status meanings',
    'Evidence Status — Attached: file linked | Missing: no proof yet (red) | Requested: asked the vendor |',
    '                   Not Applicable: no receipt exists (e.g. some bank fees) | Needs Review: unsure (yellow).',
    'Tax Review Status — Ready: good to file (green) | Needs Review / Ask Accountant: flagged (yellow) |',
    '                     Exclude: leave off the return | Personal / Non-Business: not a business expense (gray).',
    '',
    '*Color key',
    'Red = Evidence Missing.  Yellow = Needs Review / Ask Accountant / Requested.',
    'Green = Ready / Attached.  Gray = Personal / Non-Business or Exclude.',
    '',
    '*Good habits',
    '• Capture the receipt the moment you pay — photo or forward the email confirmation.',
    '• One expense = one row = one evidence file. Put the Expense ID in the file name.',
    '• Keep business and personal on separate cards where you can.',
    '• Tax notes in this sheet are general guidance, not professional advice.',
    '',
    'Need to rebuild a tab? Run: 💼 Expense Ledger ▸ Set up / rebuild workbook.',
  ];

  const out = lines.map(t => [t.replace(/^[#*]/, '')]);
  sh.getRange(1, 1, out.length, 1).setValues(out);

  for (let i = 0; i < lines.length; i++) {
    const cell = sh.getRange(i + 1, 1);
    if (lines[i].startsWith('#')) {
      cell.setFontSize(16).setFontWeight('bold').setFontColor(COLORS.title);
    } else if (lines[i].startsWith('*')) {
      cell.setFontSize(12).setFontWeight('bold').setFontColor(COLORS.headerBg);
    } else {
      cell.setFontColor('#333333');
    }
  }
  sh.setColumnWidth(1, 760);
  sh.setFrozenRows(1);
  if (sh.getMaxColumns() > 1) sh.deleteColumns(2, sh.getMaxColumns() - 1);
}

/* ============================ EDIT AUTOMATION ============================= */

/**
 * Simple trigger: fires on every manual edit. Fills ID / Month / timestamps
 * for the edited row(s) in the Expenses tab. (Script edits do not re-trigger
 * this, so there is no infinite loop.)
 */
function onEdit(e) {
  try {
    const sh = e.range.getSheet();
    if (sh.getName() !== SHEET.EXPENSES) return;
    const startRow = e.range.getRow();
    if (startRow < DATA_START_ROW) return;          // header / above
    const numRows = e.range.getNumRows();
    if (numRows > 40) {                              // big paste — do it in bulk instead
      sh.getParent().toast('Large paste detected — run "Expense Ledger ▸ Refresh" '
        + 'to fill IDs, Months and timestamps.', 'Business Expense Ledger', 6);
      return;
    }
    for (let r = startRow; r < startRow + numRows; r++) processRow_(sh, r);
  } catch (err) {
    // Never block typing; surface nothing.
  }
}

/** Fill ID, Month, Date Added and Last Updated for a single row, if it has data. */
function processRow_(sh, row) {
  const cols = EXPENSE_HEADERS.length;
  const v = sh.getRange(row, 1, 1, cols).getValues()[0];
  const hasData = v[COL.DATE - 1] || v[COL.VENDOR - 1] || v[COL.AMOUNT - 1] || v[COL.CATEGORY - 1];
  if (!hasData) return;

  const now = new Date();
  if (!v[COL.ID - 1]) sh.getRange(row, COL.ID).setValue(nextExpenseId_(sh));

  const d = v[COL.DATE - 1];
  if (d instanceof Date) {
    const tz = sh.getParent().getSpreadsheetTimeZone();
    sh.getRange(row, COL.MONTH).setValue(Utilities.formatDate(d, tz, CFG.monthFmt));
  }
  if (!v[COL.ADDED - 1]) sh.getRange(row, COL.ADDED).setValue(now);
  sh.getRange(row, COL.UPDATED).setValue(now);
}

/** Next sequential Expense ID, based on the highest existing one. */
function nextExpenseId_(sh) {
  const last = sh.getLastRow();
  let max = 0;
  if (last >= DATA_START_ROW) {
    const ids = sh.getRange(DATA_START_ROW, COL.ID, last - DATA_START_ROW + 1, 1).getValues();
    for (let i = 0; i < ids.length; i++) {
      const s = String(ids[i][0]);
      if (s.indexOf(CFG.idPrefix) === 0) {
        const num = parseInt(s.substring(CFG.idPrefix.length), 10);
        if (!isNaN(num) && num > max) max = num;
      }
    }
  }
  return CFG.idPrefix + String(max + 1).padStart(CFG.idPad, '0');
}

/* ============================== MENU ===================================== */

function onOpen() {
  SpreadsheetApp.getUi().createMenu('💼 Expense Ledger')
    .addItem('① Set up / rebuild workbook', 'setup')
    .addSeparator()
    .addItem('➕ Add expense row', 'addExpenseRow')
    .addItem('🔄 Refresh IDs, Months & timestamps', 'refreshAll')
    .addSeparator()
    .addItem('🔴 Go to Missing Evidence', 'goMissing')
    .addItem('🟡 Go to Needs Review', 'goReview')
    .addItem('🟢 Go to Accountant Export', 'goExport')
    .addToUi();
}

/** Jump to the next empty Expenses row and park the cursor on Date. */
function addExpenseRow() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName(SHEET.EXPENSES);
  sh.activate();
  const target = Math.max(sh.getLastRow(), 1) + 1;
  sh.getRange(target, COL.DATE).activate();
  ss.toast('New row ready. Enter the Date to start — ID, Month & timestamps fill in automatically.',
           'Business Expense Ledger', 5);
}

/** Backfill ID / Month / timestamps across all rows (use after a bulk paste). */
function refreshAll() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName(SHEET.EXPENSES);
  const last = sh.getLastRow();
  if (last < DATA_START_ROW) { ss.toast('No expenses to refresh yet.'); return; }

  const cols = EXPENSE_HEADERS.length;
  const n = last - DATA_START_ROW + 1;
  const rng = sh.getRange(DATA_START_ROW, 1, n, cols);
  const vals = rng.getValues();
  const tz = ss.getSpreadsheetTimeZone();
  const now = new Date();

  let max = 0;
  vals.forEach(r => {
    const s = String(r[COL.ID - 1]);
    if (s.indexOf(CFG.idPrefix) === 0) {
      const num = parseInt(s.substring(CFG.idPrefix.length), 10);
      if (!isNaN(num) && num > max) max = num;
    }
  });

  let touched = 0;
  vals.forEach(r => {
    const hasData = r[COL.DATE - 1] || r[COL.VENDOR - 1] || r[COL.AMOUNT - 1] || r[COL.CATEGORY - 1];
    if (!hasData) return;
    touched++;
    if (!r[COL.ID - 1]) r[COL.ID - 1] = CFG.idPrefix + String(++max).padStart(CFG.idPad, '0');
    if (r[COL.DATE - 1] instanceof Date) r[COL.MONTH - 1] = Utilities.formatDate(r[COL.DATE - 1], tz, CFG.monthFmt);
    if (!r[COL.ADDED - 1]) r[COL.ADDED - 1] = now;
    if (!r[COL.UPDATED - 1]) r[COL.UPDATED - 1] = now;
  });

  rng.setValues(vals);
  ss.toast('Refreshed ' + touched + ' expense row(s).', 'Business Expense Ledger', 5);
}

function goMissing() { SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET.MISSING).activate(); }
function goReview()  { SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET.REVIEW).activate(); }
function goExport()  { SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET.EXPORT).activate(); }

/* ============================== HELPERS ================================== */

function getOrCreateSheet_(ss, name) {
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

function styleHeader_(sh, row, numCols) {
  sh.getRange(row, 1, 1, numCols)
    .setBackground(COLORS.headerBg).setFontColor(COLORS.headerFg)
    .setFontWeight('bold').setVerticalAlignment('middle').setWrap(true);
  sh.setRowHeight(row, 34);
}

/** title_(sh, a1OrArray, text) — merged, styled banner across the given range. */
function title_(sh, range, text) {
  const r = Array.isArray(range) ? sh.getRange(range[0], range[1], range[2], range[3])
                                 : sh.getRange(range);
  r.merge().setValue(text)
    .setBackground(COLORS.title).setFontColor('#ffffff')
    .setFontWeight('bold').setFontSize(13)
    .setVerticalAlignment('middle').setHorizontalAlignment('left');
  sh.setRowHeight(r.getRow(), 32);
}

function setValidationFromList_(sh, col, list) {
  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(list, true).setAllowInvalid(false).build();
  sh.getRange(DATA_START_ROW, col, CFG.maxRows, 1).setDataValidation(rule);
}

function setValidationFromRange_(sh, col, sourceRange) {
  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInRange(sourceRange, true).setAllowInvalid(false).build();
  sh.getRange(DATA_START_ROW, col, CFG.maxRows, 1).setDataValidation(rule);
}

function cfRule_(text, bg, fg, bold, range) {
  return SpreadsheetApp.newConditionalFormatRule()
    .whenTextEqualTo(text)
    .setBackground(bg).setFontColor(fg).setBold(!!bold)
    .setRanges([range]).build();
}

/** Delete any leftover blank sheet (e.g. the default "Sheet1") not part of the ledger. */
function removeStockSheets_(ss) {
  const keep = Object.keys(SHEET).map(k => SHEET[k]);
  ss.getSheets().forEach(s => {
    if (keep.indexOf(s.getName()) === -1 && s.getLastRow() < 1) {
      try { ss.deleteSheet(s); } catch (e) { /* must keep at least one sheet */ }
    }
  });
}

/** Put the tabs in the intended left-to-right order. */
function orderTabs_(ss) {
  const order = [SHEET.EXPENSES, SHEET.CATEGORIES, SHEET.SUMMARY, SHEET.MISSING,
                 SHEET.REVIEW, SHEET.EXPORT, SHEET.INSTRUCTIONS];
  order.forEach((name, i) => {
    const sh = ss.getSheetByName(name);
    if (sh) { ss.setActiveSheet(sh); ss.moveActiveSheet(i + 1); }
  });
}
