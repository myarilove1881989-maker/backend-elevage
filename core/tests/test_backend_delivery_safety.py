import json
import os
from pathlib import Path
import runpy
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.state import ProjectState
from django.apps import apps
from django.utils import translation
from django.middleware.csrf import _get_new_csrf_string
from django.test import Client, SimpleTestCase, override_settings

from config.database_config import build_databases, production_environment

ROOT = Path(settings.BASE_DIR)

class ProductionConfigurationTests(SimpleTestCase):
    def test_missing_database_cannot_fall_back_to_sqlite(self):
        with self.assertRaises(ImproperlyConfigured):
            build_databases(None, debug=False, production=True, base_dir=ROOT)

    def test_production_forbids_sqlite_and_debug(self):
        for url, debug in [("sqlite:///:memory:", False),
                ("postgresql://synthetic:synthetic@127.0.0.1/synthetic", True)]:
            with self.subTest(url=url, debug=debug), self.assertRaises(ImproperlyConfigured):
                build_databases(url, debug=debug, production=True, base_dir=ROOT)

    def test_explicit_production_markers_fail_closed(self):
        self.assertTrue(production_environment({"APP_ENV": "production"}))
        self.assertTrue(production_environment({"RENDER": "true"}))
        self.assertTrue(production_environment({"DJANGO_SETTINGS_MODULE": "config.settings_production"}))
        self.assertFalse(production_environment({"APP_ENV": "synthetic"}))

    def test_bad_url_does_not_reveal_credentials(self):
        marker = "synthetic-sensitive-placeholder"
        with self.assertRaises(ImproperlyConfigured) as error:
            build_databases("postgresql://synthetic:" + marker + "@localhost:invalid/db",
                debug=False, production=True, base_dir=ROOT)
        self.assertNotIn(marker, str(error.exception))

    def test_tls_preserves_ca_and_hostname_checks(self):
        for mode in ["verify-ca", "verify-full", "require", "prefer", "allow", "disable"]:
            with self.subTest(mode=mode):
                result = build_databases("postgresql://synthetic:synthetic@127.0.0.1/synthetic?sslmode=" + mode +
                    "&sslrootcert=synthetic-public-ca.pem", debug=False, production=True, base_dir=ROOT)
                for alias in ["default", "health"]:
                    self.assertEqual(result[alias]["OPTIONS"]["sslmode"], mode if mode in ["verify-ca", "verify-full"] else "require")
                    self.assertEqual(result[alias]["OPTIONS"]["sslrootcert"], "synthetic-public-ca.pem")
                health = result["health"]
                self.assertEqual(health["CONN_MAX_AGE"], 0)
                self.assertFalse(health["ATOMIC_REQUESTS"])
                self.assertEqual(health["OPTIONS"]["connect_timeout"], 2)
                self.assertIn("default_transaction_read_only=on", health["OPTIONS"]["options"])
                self.assertIn("statement_timeout=1000", health["OPTIONS"]["options"])
                self.assertIn("lock_timeout=1000", health["OPTIONS"]["options"])
                self.assertNotIn("default_transaction_read_only=on", result["default"]["OPTIONS"].get("options", ""))

    def test_production_module_does_not_override_offline_authentication(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://synthetic:synthetic@127.0.0.1/synthetic", "DEBUG": "False"}):
            config = runpy.run_module("config.settings_production", run_name="_stage3_production")
        self.assertIn("rest_framework_simplejwt.token_blacklist", config["INSTALLED_APPS"])
        self.assertEqual(config["REST_FRAMEWORK"]["DEFAULT_AUTHENTICATION_CLASSES"], ("core.credential_services.CredentialJWTAuthentication",))
        self.assertFalse(config["OFFLINE_FINE_PERMISSIONS_ENABLED"])
        self.assertEqual(config["SECURE_REDIRECT_EXEMPT"], [r"\\Aapi/health/\\Z".replace("\\\\", "\\")])
        self.assertIn("django.middleware.csrf.CsrfViewMiddleware", config["MIDDLEWARE"])

    def test_gunicorn_config_forces_production_without_migration(self):
        with patch.dict(os.environ, {"APP_ENV": "synthetic", "DJANGO_SETTINGS_MODULE": "stage3_test_settings"}):
            config = runpy.run_path(str(ROOT / "deployment/gunicorn.conf.py"))
            self.assertEqual(os.environ["DJANGO_SETTINGS_MODULE"], "config.settings_production")
            self.assertEqual(os.environ["APP_ENV"], "production")
        self.assertEqual(config["workers"], 1)
        self.assertEqual(config["threads"], 2)
        self.assertEqual(config["worker_class"], "gthread")
        self.assertEqual(config["graceful_timeout"], 25)
        self.assertFalse(config["preload_app"])

@override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"\Aapi/health/\Z"])
class HealthEndpointTests(SimpleTestCase):
    def setUp(self):
        self.db = MagicMock()
        self.cursor = MagicMock()
        self.cursor.fetchone.return_value = (1,)
        self.db.cursor.return_value.__enter__.return_value = self.cursor
        self.connection_patch = patch("core.health.connections", {"health": self.db})
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        self.client = Client(enforce_csrf_checks=True)

    def test_get_is_unauthenticated_minimal_and_read_only(self):
        with patch("django.contrib.sessions.backends.db.SessionStore.load",
                side_effect=AssertionError("No session SQL is allowed")):
            response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.cursor.execute.assert_called_once_with("SELECT 1")
        self.db.close.assert_called_once()
        self.assertIn("no-store", response["Cache-Control"])
        self.assertFalse(response.cookies)

    def test_head_has_no_body(self):
        response = self.client.head("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.cursor.execute.assert_called_once_with("SELECT 1")

    def test_database_error_is_generic_503_and_closed(self):
        self.db.cursor.side_effect = DatabaseError("synthetic-do-not-disclose")
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "synthetic-do-not-disclose", status_code=503)
        self.db.close.assert_called_once()

    def test_unexpected_result_is_not_ready(self):
        self.cursor.fetchone.return_value = (0,)
        self.assertEqual(self.client.get("/api/health/").status_code, 503)

    def test_connection_close_failure_is_not_ready(self):
        self.db.close.side_effect = DatabaseError("synthetic")
        self.assertEqual(self.client.get("/api/health/").status_code, 503)

    def test_unsafe_methods_without_csrf_are_denied_before_database(self):
        for method in ["post", "put", "patch", "delete"]:
            self.assertEqual(getattr(self.client, method)("/api/health/").status_code, 403)
        self.db.cursor.assert_not_called()

    def test_unsafe_methods_with_valid_csrf_are_405_before_database(self):
        token = _get_new_csrf_string()
        self.client.cookies["csrftoken"] = token
        for method in ["post", "put", "patch", "delete"]:
            response = getattr(self.client, method)("/api/health/", secure=True,
                HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://testserver")
            self.assertEqual(response.status_code, 405)
        self.db.cursor.assert_not_called()

    def test_redirect_exception_does_not_cover_neighbors(self):
        for path in ["/api/health", "/api/health//", "/api/health/extra", "/admin/"]:
            self.assertEqual(self.client.get(path).status_code, 301)
        self.db.cursor.assert_not_called()

class SourceMigrationContractTests(SimpleTestCase):
    def test_all_source_migration_dependencies_are_consistent_without_a_database(self):
        loader = MigrationLoader(None)
        loader.graph.ensure_not_cyclic()
        core = [node for node in loader.disk_migrations if node[0] == "core"]
        self.assertEqual(len(core), 37)
        self.assertEqual(loader.graph.leaf_nodes("core"), [("core", "0037_offline_business_revocation")])
        self.assertEqual(len([node for node in loader.disk_migrations if node[0] == "token_blacklist"]), 12)

    def test_models_match_the_migration_graph(self):
        # Match makemigrations' no_translations wrapper; lazy French labels
        # otherwise create false differences in Django's own auth/admin models.
        with translation.override(None):
            loader = MigrationLoader(None)
            changes = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(graph=loader.graph)
        self.assertEqual(changes, {})
