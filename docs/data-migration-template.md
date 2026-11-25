# Data migration plan: <app>, <change>

Copy this file into the app's `docs/` folder for any change that moves, reshapes or deletes stored data (new table layout, import from
an old system, merging accounts). Delete the guidance in *italics* as you fill it in.

| Field | Value |
|---|---|
| App / environment | |
| Owner of this change | |
| Date and window | |
| Data affected (tables, rows, size) | |
| Personal data involved? | *Yes/no. If yes, see "Privacy" below.* |

## 1. Why and what changes
*One paragraph: what the old shape is, what the new shape is, and why.*

## 2. Before you start
- [ ] Fresh backup taken: `python -m avk backup run`, then `python -m avk backup verify` shows the app's row counts.
- [ ] Rehearsed on stg with a copy of realistic data (`python -m avk stg up <app>`; load data with the seed folder).
- [ ] Row counts and checksums recorded for the tables that matter: `SELECT count(*) ...` and one total (for example a sum of amounts).
- [ ] The migration is a Flyway file, reviewed in a pull request; it never edits an old migration.
- [ ] Old and new code can both run against the half-migrated database (expand first, contract later), so the blue/green switch stays safe.

## 3. Steps
1. *Expand:* add the new columns/tables (backward compatible). Deploy.
2. *Copy:* fill the new structure from the old (batched, repeatable).
3. *Verify:* counts and checksums match the numbers recorded above.
4. *Switch:* deploy the code that reads the new structure.
5. *Contract:* only after a quiet period (suggest 7 days), remove the old structure in a later release.

## 4. How we know it worked
*List the checks and the expected numbers. A migration is not done until these match.*

## 5. Rollback
Decide this **before** starting, and write down the point of no return.

- **Before step 4 (switch):** nothing to undo for users. Deploy the previous release (`python -m avk rollback <app>`); the old structure is still intact.
- **After step 4, before step 5:** roll the code back the same way; the old structure still has the data written up to the switch.
  Data written *after* the switch lives only in the new structure: copy it back with the reverse script (write it now and test it on stg),
  or accept losing it and say so in the change notice.
- **After step 5 (old structure removed):** the only way back is a restore: `python -m avk backup restore <app> --from local --to scratch`
  first to inspect, then `--to prod` with the typed confirmation. Everything since the backup is lost, so keep step 5 for last.
- **Who decides to roll back, and by when:** *name and time limit.*

## 6. Privacy
*If personal data is moved, copied or deleted: confirm the privacy policy still describes where it lives and how long it is kept
(`legal.retention`, `legal.data_location`), and that deleted accounts stay deleted in the new structure and in backups' retention.*

## 7. After
- [ ] Notes added to the app's README or RUNBOOK; temporary scripts removed.
- [ ] Old structure removal scheduled (step 5) with a date.

*Not legal advice.*
