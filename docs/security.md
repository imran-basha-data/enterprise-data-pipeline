# Security notes

## Secrets and configuration

- All configuration comes from environment variables. `.env` is git-ignored and only `.env.example`, with placeholder values, is committed.
- Passwords are redacted from connection strings before logging (`Settings.redacted_database_url`). The pipeline never logs the DSN itself.
- CI uses a throwaway password scoped to the ephemeral service container. No repository secrets are needed.
- Gitleaks scans the **full git history** on every CI run.

## Database access

- The pipeline needs `CREATE` on its own database (for migrations) and DML on the `ops`, `raw`, `staging` and `mart` schemas. It does not need superuser.
- In production, split the roles: a migration role that owns the schemas, a pipeline role with DML only, and a read-only role for analysts limited to `mart`.
- SQL identifiers are composed with `psycopg.sql.Identifier`, and every value is passed as a bound parameter. No SQL is built from source data.
- Docker Compose binds PostgreSQL to `127.0.0.1` only.

## Containers

- Multi-stage build: compilers and build tooling never reach the runtime image.
- The runtime image runs as a dedicated non-root user (uid 10001). CI asserts this.
- Compose runs the pipeline with a read-only root filesystem, `cap_drop: [ALL]` and `no-new-privileges`.
- `.dockerignore` excludes `.git`, `.env*`, tests, local data and reports from the build context.
- Corporate CA bundles can be passed as a BuildKit secret, never as a build argument or a copied file, so they are never stored in a layer.

## Dependencies

- Runtime and development dependencies are pinned to exact versions in `requirements*.txt`.
- `pip-audit --strict` checks the runtime pins against known vulnerabilities in CI.
- `bandit` statically analyses the source in CI. The one suppressed finding (B311) is the seeded `random.Random` used to generate synthetic data. It is annotated where it occurs and is never used for anything security-related.

## Data handling

- All data in this repository is synthetic. Supplier and warehouse names are fictitious, and the data contains no personal information.
- Quarantined records keep their original payload for audit. With real data, apply the same access controls and retention policy to `ops.quarantine` and `raw.landed_records` as to the source systems, because they can contain sensitive values.

## Not covered

- TLS to PostgreSQL is not enforced by default. Add `sslmode=require` (or `verify-full`) to `EDP_DATABASE_URL` for any non-local database.
- Container image vulnerability scanning (e.g. Trivy) is not part of CI.
- There is no secrets manager integration. Environment variables are expected to be injected by the runtime platform.
