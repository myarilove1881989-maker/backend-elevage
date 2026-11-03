# Stocks & Santé - Phase 1 backend foundation

## Scope and status

Internal, owner-only Python services. No new HTTP routes, Flutter screens,
operator grants, offline dispatch handlers, vaccine follow-up or agenda reminders.
No financial arithmetic changed. No production activation is authorized.

Baseline: backend 598a5bf3c7f8f82689432b92eb1187fb212e933b;
Flutter 200a602cc5e6b1e1ea6738f2574d3b3cb051de4d (unchanged).
Local feature branch: feature/stocks-sante-phase1-20261102.

## Models and invariants

- SupplyProduct: tenant-bound UUID, FEED (kg) / VACCINE (integer doses), configurable
  alert threshold, name and archival. Tenant and kind cannot change through save.
- SupplyOperation: tenant + operation UUID unique, author, canonical request hash,
  occurrence/recording times, reason, optional unique reversal of an original.
- SupplyReceipt: optional protected link to an existing, active Depense; phase 1
  supports one receipt per expense, never creates/updates an expense or Achat.
- SupplyVial: individual UUID and receipt ordinal; manufacturer batch and expiry
  are immutable receipt attributes. One immutable opening with optional seconds
  of usability; no invented default protocol/duration.
- SupplyMovement: immutable signed quantity, source position, destination lot,
  product, species, vial and operation line. No stored editable balance.

All service writes acquire the existing Exploitation row lock. SQL constraints
protect operation/line/receipt uniqueness, quantity signs and referential links.
PostgreSQL triggers block UPDATE/DELETE of operation, receipt, vial, opening and
movement facts. Direct arbitrary database INSERT is not a supported interface;
tenant, capacity and aggregate balance checks belong to the transactional service.

## Service interface

`create_product(actor, farm_id, kind=..., name=..., alert_threshold='0')`
and `configure_product(...)` are owner-only audited catalog operations.

`execute(actor, farm_id, operation_uuid, kind, payload, reason='', occurred_at=None)`
accepts JSON primitives, exact decimal strings (not binary floats), a stable UUID
and an aware occurrence time (default server time). Replays return the same
operation; changed payload/author/reason/time under the same UUID is refused.

Command payloads (IDs are explicit, never resolved by label):

| Command | Required | Optional |
|---|---|---|
| FEED_RECEIPT | product, species, either kg OR bags + kg_per_bag | lot, expense |
| VACCINE_RECEIPT | product, species, manufacturer_batch, expires_on, vial_count, doses_per_vial | expense |
| DISTRIBUTE | product, species, lot (destination), quantity | source_lot (null = shared species stock) |
| TRANSFER | product, species, quantity | source_lot, target_lot (different positions) |
| LOSS | product, species, quantity | lot, vial (required for vaccines) |
| CORRECT | product, species, signed quantity | lot, vial (required for vaccines) |
| ADMINISTER | product, species, lot, vial, quantity | - |
| OPEN | vial | opened_at (ISO datetime), usable_seconds |
| REVERSE | original (operation PK within this farm) | - |

LOSS, CORRECT and REVERSE require a reason. Positive corrections can represent a
verified initial inventory. DISTRIBUTE debits the declared source; the destination
is a protected FK as well as part of the original command. TRANSFER is a balanced
two-line operation. Partial compensation uses a new, reasoned CORRECT command;
REVERSE compensates all movement lines atomically and cannot reverse OPEN or a
reversal. It refuses a second reversal and any resulting negative balance or vial
overcapacity. Historical reversals are owner-only, reasoned and may refer to a
closed lot; ordinary entries/exits/transfers require open lots. Reopen a closed
source lot before reallocating its remaining feed.

`stock(actor, farm_id, product_id, species_id=None, lot_id=None, at=None)` returns
physical, usable, unit, threshold status and confirmed_business_revision. Balances
are current validated totals; `at` evaluates vaccine usability, NOT historical
inventory. Vaccine lot stock filtering is refused: stock is held in individual
vials, while animal lots identify consumption destinations. Expiry dates are
inclusive in Django's configured timezone; expiry after opening is exclusive.
Administration requires an opened, usable vial; loss may remove unusable doses.
An opened vial without a duration stays subject to manufacturer expiry only.

## Compatibility and offline boundary

Existing distribution, expense, animal purchase, egg, agenda and signed terrain
payloads remain unchanged. Old distributions do not silently decrement the new
ledger. Expense correction/deletion/cancellation and offline reversal are blocked
when a physical receipt exists, pending a coordinated future workflow. Existing
lot closure considers supply activity. No historic quantities are inferred.

Future offline integration MUST reuse TerrainSubmission/outbox, original author,
UUID, dependency mapping, rights validation, receipts and business_revision.
The UUID fingerprint alone is NOT an offline authorization. New operator rights,
cache/projections, client schema negotiation and conflict UX are not activated.
No new queue or transport has been introduced.

## Tests and local execution

Use `./tools/dev.ps1 test -Postgres -Tests core.tests.test_supply_foundation`.
The launcher forces synthetic local configuration, blocks external network and
starts/stops a loopback-only PostgreSQL cluster. SQLite can run the same suite;
PostgreSQL is authoritative for concurrent row locks and immutability triggers.
Selected existing regression suites cover feed/eggs, expenses, offline rights,
terrain application/reversal, lot closure, owner maintenance and agenda/finance.

## Migrations, activation and rollback

0039 adds only supply models/constraints; 0040 adds PostgreSQL history protection;
0041 adds the protected destination FK. No data migration or old financial
column change. Forward/backward schema testing uses empty supply tables in a
synthetic test database and checks preservation of an existing expense.

No production migration, startup guard modification, deploy, push or shared merge.
The existing production migration gate has NOT been expanded for 0039-0041:
this feature branch is NOT a deployable release. A later approved release must
prepare activation, backups, schema gate and client compatibility explicitly.

Before activation, discard only this feature's code through normal review if
needed. After real stock facts exist, keep additive tables and immutable facts;
prefer a forward correction. A code rollback requires schema-compatible startup
and must not erase an outbox, uninstall Android, downgrade an APK or reverse
data-bearing migrations. Database restoration is a separate authorized operation.

## Known limits for the next phase

No UI/API access yet; owner-only internal services; one receipt per expense;
maximum 1000 vials per command; no correction of opening metadata; no medical
protocol engine, historical as-of inventory or client offline synchronization
for new stocks. Initial inventory, allocation UX, coordinated purchase corrections
and multi-line procurement need explicit design before enabling real workflows.

## Validation recorded on 2 November 2026

- PostgreSQL qualification: 150 tests passed in 46.387 s, no skipped tests.
- Final foundation pass after last changes: 27 tests passed in 12.068 s, no skipped tests.
- Both runs: Django system checks clean and no migration drift.
- Foundation coverage includes concurrent overspend refusal, immutable SQL history, additive schema roundtrip, tenant isolation, 500/80/420 kg, 10x20 doses, expiry/opening, corrections and legacy compatibility.
- Earlier SQLite smoke: 20 tests, 18 passed and 2 PostgreSQL-specific checks skipped; final PostgreSQL results are authoritative.
- Private synthetic logs: .local/supply-phase1-postgres.log and .local/supply-phase1-foundation-final.log. No production endpoint or SMTP was used.
- Git diff whitespace check passed; Flutter source and baseline branch unchanged.

