# Test data hook

`avk seed <app>` loads synthetic data into **stg only**. It refuses `--env prod`, and prod data is never copied to stg.

## Default

Every `seed/*.sql` file in the app's folder (`..\<app>\seed\`), in name order, is run against the app's stg database. Files
must be safe to run more than once (use `insert ... on conflict do nothing` or check first).

## Hook contract (for a smarter generator later)

A generator replaces the default by providing a command in the app's `avk.yaml`:

```yaml
seed: {command: ["python", "seed/generate.py"]}
```

The command receives the stg connection through environment variables and must write rows only through them:

| Variable | Value |
|---|---|
| `AVK_SEED_JDBC_URL` | `jdbc:postgresql://pg-stg:5432/<app>` |
| `AVK_SEED_PG_URL` | `postgresql://<app>_rw:...@127.0.0.1:5440/<app>` (host-side, published on loopback) |
| `AVK_SEED_ENV` | always `stg` |

It must exit non-zero on failure and print no credentials. It must not read prod and must not connect anywhere but
`127.0.0.1:5440`. The platform checks `AVK_SEED_ENV` before running it; the hook is not implemented yet (the default
SQL loader covers Phase 4), so this file is the interface to keep stable.
