"""Read-only release checks. Logs counts and hashes, never business records."""

import hashlib
import json
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


def table_snapshot(cursor, table, columns=None, through_id=None):
    quote = connection.ops.quote_name
    available = [field.name for field in connection.introspection.get_table_description(cursor, table)]
    columns = columns or [name for name in available if name not in {"last_login", "last_activity"}]
    if set(columns) - set(available):
        raise CommandError(f"Historical columns missing from {table}")
    cursor.execute(f"SELECT COUNT(*) FROM {quote(table)}")
    total_count = cursor.fetchone()[0]
    if "id" in available and through_id is None:
        cursor.execute(f"SELECT MAX({quote('id')}) FROM {quote(table)}")
        through_id = cursor.fetchone()[0] or 0
    selected = ", ".join(quote(name) for name in columns)
    order = quote("id") if "id" in available else selected
    where = f" WHERE {quote('id')} <= %s" if through_id is not None else ""
    cursor.execute(f"SELECT {selected} FROM {quote(table)}{where} ORDER BY {order}", [through_id] if where else [])
    digest = hashlib.sha256()
    count = 0
    while rows := cursor.fetchmany(500):
        for row in rows:
            digest.update(json.dumps(row, default=str, ensure_ascii=False).encode("utf-8"))
            digest.update(b"\n")
            count += 1
    return {"columns": columns, "count": count, "sha256": digest.hexdigest(), "through_id": through_id, "total_count": total_count}


def database_snapshot(baseline=None):
    set_read_only = connection.vendor == "postgresql" and not connection.in_atomic_block
    with transaction.atomic(), connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            if set_read_only:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cursor.execute("SELECT current_setting('server_version')")
            version = cursor.fetchone()[0]
        else:
            version = "local-validation"
        tables = set(connection.introspection.table_names(cursor))
        wanted = baseline["tables"] if baseline else sorted(name for name in tables if name.startswith("core_"))
        if set(wanted) - tables:
            raise CommandError("Historical tables missing")
        snapshots = {
            table: table_snapshot(
                cursor, table,
                baseline["tables"][table]["columns"] if baseline else None,
                baseline["tables"][table]["through_id"] if baseline else None,
            )
            for table in wanted
        }
        cursor.execute("SELECT app, name FROM django_migrations ORDER BY app, name")
        migrations = [list(row) for row in cursor.fetchall()]
        return {"engine": connection.vendor, "version": version, "tables": snapshots, "migrations": migrations}


class Command(BaseCommand):
    help = "Read-only PostgreSQL inventory and historical-data comparison for a release."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--write", type=Path)
        mode.add_argument("--compare", type=Path)
        parser.add_argument("--allow-sqlite", action="store_true", help="For isolated local validation only.")

    def handle(self, *args, **options):
        if connection.vendor != "postgresql" and not options["allow_sqlite"]:
            raise CommandError("Production audit requires PostgreSQL")
        baseline = None
        if options["compare"]:
            baseline = json.loads(options["compare"].read_text())
        result = database_snapshot(baseline)
        if options["write"]:
            descriptor = os.open(options["write"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w") as output:
                json.dump(result, output)
        differences = []
        if baseline:
            differences = [
                table for table, original in baseline["tables"].items()
                if any(result["tables"][table][key] != original[key] for key in ("columns", "count", "sha256", "through_id"))
            ]
        summary = {
            "phase": "after" if baseline else "before",
            "engine": result["engine"], "version": result["version"],
            "debug": settings.DEBUG,
            "counts": {table: value["count"] for table, value in result["tables"].items()},
            "current_counts": {table: value["total_count"] for table, value in result["tables"].items()},
            "migrations": result["migrations"],
            "historical_data_unchanged": not differences if baseline else None,
            "changed_tables": differences,
        }
        self.stdout.write("ELEVAGE_PRODUCTION_AUDIT " + json.dumps(summary, sort_keys=True))
        if differences:
            raise CommandError("Historical data changed: " + ", ".join(differences))
