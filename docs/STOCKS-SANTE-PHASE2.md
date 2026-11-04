# Stocks & Santé - Phase 2 (local only)

Base backend: 72a2d7dd4a85b5878c4de9fc91a65aef03fe11ad.
Branches: feature/stocks-sante-phase2-20261102 in both current repositories.

## Connected purchase

Existing POST /api/depenses/create/ accepts an additional feed_supply object and
operation_uuid. Financial fields remain lot/categorie/montant/date/note with the
existing DepenseSerializer. The outer request runs under the existing actor scope,
expenses.create authorization, farm lock and device checks. A single transaction
creates the financial expense, optional new feed product, immutable FEED_PURCHASE
operation, receipt and one normalized kg entry. No Achat or animal movement.

feed_supply uses product UUID OR new_product {id UUID, name}, species ID,
optional physical lot ID (null = species shared stock), kg OR bags + kg_per_bag,
and received_on YYYY-MM-DD. Exact decimal strings, at most three decimal places;
integer bags. Original units/date retained in immutable operation payload.
Accounting lot is independent of physical destination; amounts are not split.

Same farm/UUID/author/payload returns the existing expense. Changed payload under
the same UUID is refused. Farm locking serializes parallel repeats. Existing
legacy expense requests without feed_supply keep their old behavior; no guessed
stock is generated from a category name, expense amount or historic record.

## Category identity

SupplyCategoryBinding assigns purpose FEED to a stable category FK. GET
/api/categories-depense/ includes stock_purpose. Runtime forms never compare labels.
0042 creates the new binding table; 0043 creates mappings only for unambiguous
historical seed labels (one exact Aliment per farm). This does not rewrite existing
categories, expenses or their amounts. Renames keep bindings. Ambiguous or custom
categories require explicit classification before enabling procurement there.
New registrations create their explicit FEED binding with their seeded category.

## Catalog and corrections

GET /api/feed-supplies/ is tenant-bound under stocks.read and returns confirmed
product/species/open-lot catalogs and kg positions, with business revision.

PATCH /api/depenses/supply/{id}/ accepts operation_uuid, quantity_kg (new total)
and reason. It requires the existing owner HTTP expense-update permission. The
original receipt stays immutable; a FEED_CORRECT delta changes physical quantity
only. Total corrected procurement = original receipt + its correction deltas.

Existing owner maintenance actions automatically offer the quantity field for
phase-2 purchases and use reason/revision-bound commands. Normal expenses retain
their financial fields. Their delete action invokes coordinated cancellation,
not a physical DELETE of the original expense.

Existing /api/depenses/cancel/{id}/ performs FEED_CANCEL compensation and existing
ExpenseCancellation/void state atomically. Neither correction nor cancellation
can make its source position negative. Legacy linked ConsommationAliment,
closed accounting/physical lots, foreign references and phase-1-only receipts
require controlled review; no silent rewrite. Generic phase-1 REVERSE cannot bypass
coordinated purchase cancellation. UUID/revision protections cover retries.

## Flutter / offline boundary

The existing online expense form adds fields only for stock_purpose FEED. No new
dependencies. Product creation is included in the same purchase, avoiding orphan
products on a rejected purchase. An explicit retry retains the original UUID and
complete frozen request after a lost response; changing session prevents resend.
Catalogs are confirmed server snapshots, refreshed after a successful supply.

The existing tablet/offline expense form and outbox remain unchanged, apart from
a visible stock-limit notice. Prepared/unified offline sessions never send a new
feed_supply payload through the old signed expense schema. Offline expense entry
does not confirm a physical receipt. Complete stock cache/projections, dependency
mapping and offline reconciliation are deferred to phase 3, using the existing
outbox only. No second queue or inferred backfill has been created. Closing the
online form discards its in-memory draft; inspect expense/history before re-entry
after an uncertain result. No durable automatic HTTP resend is introduced.

## Qualification / release boundary

Use tools/dev.ps1 test with PostgreSQL and targeted Flutter tests. No production
DSN, SMTP, device installation or network business mutation is used by tests.
No production schema gate has been expanded: this is not a deployable release.
No version/flavor, APK, signed grant, financial formula, egg logic or agenda change.
Keep 72a2d7d and immutable facts. Any later release must approve migrations,
compatibility and backup separately; never delete an outbox or downgrade Android
as rollback. No push, shared merge, PR or deployment is authorized here.

## Recorded local qualification

- PostgreSQL regression pass: 148 tests passed in 102.626 s, no skips.
- Subsequent foundation + procurement pass: 46 passed in 23.815 s, no skips.
- Latest procurement pass after final validation changes: 21 passed in 5.692 s, no skips.
- These passes overlap; their counts are not additive. All report no migration drift and clean Django system checks.
- Final Flutter VM/native-storage regression selection: 81 tests passed.
- Chrome: 7 tests passed. The existing bounded local CanvasKit test helper served only the pinned SDK assets to the identified synthetic localhost harness; no application or SDK source patch.
- Targeted Dart analysis: No issues found. The Flutter analyze wrapper could not enumerate the stripped SDK dev folder; the pinned Dart analyzer completed successfully instead.
- Earlier intermediate display/test-scroll failures were corrected before the final passing selections. No real Android device acceptance test or APK build/install was performed.
- Logs are private synthetic evidence in .local/phase2-*.log and are not release artifacts. Production migration gate, releases and shared branches remain untouched.

