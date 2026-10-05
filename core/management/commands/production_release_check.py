"""Controlled PostgreSQL checks in a disposable tenant; never use a test runner on production."""

import json
import re
import threading
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from core import egg_services
from core.models import (
    AffectationMouvementOeufs, Client, CollecteOeufs, Espece, Lot,
    Mouvement, MouvementOeufs, User, Vente, VenteOeufs,
)


def cleanup_test_user(username):
    if not re.fullmatch(r"__release_qa_[0-9a-f]{16}(?:_[a-z]+)?", username):
        raise CommandError("Cleanup is limited to release-test users")
    user = User.objects.filter(username=username).first()
    if user is None:
        return 0
    if user.is_staff or user.is_superuser or user.email != username + "@example.invalid":
        raise CommandError("Cleanup refused for a non-test account")
    farm = user.exploitation
    if farm is None or farm.proprietaire_id != user.pk or farm.users.count() != 1:
        raise CommandError("Cleanup refused for a shared or unowned tenant")
    with transaction.atomic():
        AffectationMouvementOeufs.objects.filter(mouvement__exploitation=farm).delete()
        VenteOeufs.objects.filter(exploitation=farm).delete()
        Mouvement.objects.filter(lot__exploitation=farm).delete()
        deleted, _ = user.delete()
    return deleted


def create_fixture(user, name):
    species = Espece.objects.create(nom="QA Poule", exploitation=user.exploitation)
    lot = Lot.objects.create(
        nom=name, exploitation=user.exploitation, espece=species,
        date_debut=timezone.localdate(), type_production="OEUFS", statut_production="PONTE",
    )
    customer = Client.objects.create(nom="QA Client", exploitation=user.exploitation)
    collection = CollecteOeufs.objects.create(
        lot=lot, exploitation=user.exploitation, nombre_collecte=100,
        collecte_at=timezone.now() - timedelta(days=1),
    )
    egg_services.sync_collection_stock_movement(collection)
    return lot, customer


def sale_data(lot, customer):
    return {
        "lot": lot, "client": customer.pk, "conditionnement": "UNITE",
        "nombre_conditionnements": 80, "oeufs_par_conditionnement": 1,
        "prix_unitaire_conditionnement": Decimal("1.00"),
    }


def tenant_counts(user):
    return [
        Vente.objects.filter(lot__exploitation=user.exploitation).count(),
        VenteOeufs.objects.filter(exploitation=user.exploitation).count(),
        MouvementOeufs.objects.filter(exploitation=user.exploitation).count(),
        AffectationMouvementOeufs.objects.filter(mouvement__exploitation=user.exploitation).count(),
    ]


def database_checks():
    if connection.vendor != "postgresql":
        raise CommandError("Live concurrency checks require PostgreSQL")
    username = "__release_qa_" + uuid.uuid4().hex[:16] + "_db"
    user = User.objects.create_user(username=username, email=username + "@example.invalid")
    try:
        lot, customer = create_fixture(user, "QA Concurrency")
        lock_ready, b_ready, permit_commit = threading.Event(), threading.Event(), threading.Event()
        results, pids = {}, {}

        def worker(label):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[label] = cursor.fetchone()[0]
                    cursor.execute("SET lock_timeout = '8s'")
                    cursor.execute("SET statement_timeout = '12s'")
                worker_user = User.objects.get(pk=user.pk)
                if label == "A":
                    with transaction.atomic():
                        Lot.objects.select_for_update().get(pk=lot.pk)
                        egg_services.create_egg_sale(validated_data=sale_data(lot, customer), user=worker_user)
                        lock_ready.set()
                        if not permit_commit.wait(8):
                            raise RuntimeError("Commit coordination timed out")
                    results[label] = "committed"
                else:
                    if not lock_ready.wait(8):
                        raise RuntimeError("Lock coordination timed out")
                    b_ready.set()
                    try:
                        egg_services.create_egg_sale(validated_data=sale_data(lot, customer), user=worker_user)
                    except ValueError as error:
                        if "insuffisant" not in str(error).lower():
                            raise
                        results[label] = "insufficient_stock"
                    else:
                        results[label] = "committed"
            except Exception as error:
                results[label] = "unexpected:" + type(error).__name__
            finally:
                connection.close()

        a = threading.Thread(target=worker, args=("A",), daemon=True)
        b = threading.Thread(target=worker, args=("B",), daemon=True)
        observed_lock = False
        try:
            a.start()
            if not lock_ready.wait(8):
                raise CommandError("Worker A did not acquire the test lock")
            b.start()
            if not b_ready.wait(8):
                raise CommandError("Worker B did not reach the test lock")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", [pids["B"]])
                    row = cursor.fetchone()
                if row and row[0] == "Lock":
                    observed_lock = True
                    break
                time.sleep(0.025)
        finally:
            permit_commit.set()
            a.join(timeout=15)
            if b.ident is not None:
                b.join(timeout=15)
        if a.is_alive() or b.is_alive() or pids.get("A") == pids.get("B") or not observed_lock:
            raise CommandError("Two-connection PostgreSQL lock verification failed")
        if results != {"A": "committed", "B": "insufficient_stock"}:
            raise CommandError("Concurrent sale results were unexpected")
        remaining = egg_services.get_egg_stock(user.exploitation, lot)
        allocations = AffectationMouvementOeufs.objects.filter(mouvement__lot=lot)
        allocated = allocations.aggregate(total=Sum("quantite"))["total"]
        duplicates = allocations.values("mouvement_id", "collecte_id").annotate(n=Count("id")).filter(n__gt=1).exists()
        if remaining != 20 or allocated != 80 or duplicates or Vente.objects.filter(lot=lot).count() != 1:
            raise CommandError("FIFO inventory invariants failed")

        rollback_lot, rollback_customer = create_fixture(user, "QA Rollback")
        before = tenant_counts(user)
        real_affect = egg_services.affect_egg_exit

        def fail_after_allocation(*args, **kwargs):
            real_affect(*args, **kwargs)
            raise RuntimeError("Controlled release-test failure")

        failed_as_expected = False
        with patch("core.egg_services.affect_egg_exit", side_effect=fail_after_allocation):
            try:
                egg_services.create_egg_sale(validated_data=sale_data(rollback_lot, rollback_customer), user=user)
            except RuntimeError as error:
                if str(error) != "Controlled release-test failure":
                    raise
                failed_as_expected = True
        if not failed_as_expected or tenant_counts(user) != before or egg_services.get_egg_stock(user.exploitation, rollback_lot) != 100:
            raise CommandError("Transaction rollback verification failed")
        return {"two_connections": True, "postgresql_lock_observed": True, "concurrency": results,
                "remaining": remaining, "allocated": allocated, "rollback": True}
    finally:
        cleanup_test_user(username)


class Command(BaseCommand):
    help = "Controlled release checks and cleanup, limited to disposable release-test tenants."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--database-tests", action="store_true")
        mode.add_argument("--cleanup-user")

    def handle(self, *args, **options):
        if options["cleanup_user"]:
            result = {"cleanup_completed": True, "deleted_objects": cleanup_test_user(options["cleanup_user"])}
        else:
            result = database_checks()
            result["test_tenant_cleaned"] = True
        self.stdout.write("ELEVAGE_RELEASE_CHECK " + json.dumps(result, sort_keys=True))
