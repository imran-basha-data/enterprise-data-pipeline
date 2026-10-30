"""Command-line interface: ``edp <command>``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from edp import __version__
from edp.config import ConfigError, Settings
from edp.db import MigrationError, PipelineLockedError, apply_migrations
from edp.generator import GeneratorConfig, generate
from edp.logging_setup import configure_logging, get_logger
from edp.pipeline import run_pipeline

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2
EXIT_LOCKED = 3

log = get_logger("edp.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edp", description="Manufacturing and supply-chain data pipeline."
    )
    parser.add_argument("--version", action="version", version=f"edp {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Create or upgrade the database schema.")

    gen = sub.add_parser("generate", help="Write synthetic source files to a landing folder.")
    gen.add_argument("--output", type=Path, default=None, help="Defaults to EDP_LANDING_DIR.")
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--batches", type=int, default=3)
    gen.add_argument("--days-per-batch", type=int, default=30)
    gen.add_argument("--orders-per-day", type=int, default=12)
    gen.add_argument("--shipments-per-day", type=int, default=8)
    gen.add_argument("--defect-rate", type=float, default=0.02)
    gen.add_argument(
        "--no-schema-drift", action="store_true", help="Do not add an extra column in batch 3."
    )

    run = sub.add_parser("run", help="Ingest new files and refresh models.")
    run.add_argument("--landing-dir", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "generate":
        configure_logging("INFO", "text")
        output = args.output or Path(os.environ.get("EDP_LANDING_DIR", "data/landing"))
        manifest = generate(
            output,
            GeneratorConfig(
                seed=args.seed,
                batches=args.batches,
                days_per_batch=args.days_per_batch,
                orders_per_day=args.orders_per_day,
                shipments_per_day=args.shipments_per_day,
                defect_rate=args.defect_rate,
                schema_drift_batch=None if args.no_schema_drift else 3,
            ),
        )
        print(
            json.dumps(
                {
                    "output": str(output),
                    "files": len(manifest.files),
                    "rows": sum(f.rows_written for f in manifest.files),
                    "injected_defects": manifest.expected_quarantine(),
                    "exact_duplicates": manifest.expected_exact_duplicates(),
                },
                indent=2,
            )
        )
        return EXIT_OK

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    configure_logging(settings.log_level, settings.log_format)

    if args.command == "migrate":
        try:
            applied = apply_migrations(settings.database_url)
        except MigrationError as exc:
            log.error("migration_failed", extra={"fields": {"error": str(exc)}})
            return EXIT_FAILED
        log.info("migrations_complete", extra={"fields": {"applied": applied}})
        return EXIT_OK

    if args.command == "run":
        if args.landing_dir is not None:
            settings = Settings(**{**settings.__dict__, "landing_dir": args.landing_dir})
        apply_migrations(settings.database_url)
        try:
            summary = run_pipeline(settings)
        except PipelineLockedError as exc:
            log.error("run_locked", extra={"fields": {"error": str(exc)}})
            return EXIT_LOCKED
        return EXIT_OK if summary.status == "succeeded" else EXIT_FAILED

    return EXIT_FAILED  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
