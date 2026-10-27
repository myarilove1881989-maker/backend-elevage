import importlib.util
import io
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.management.base import CommandError

if not settings.configured:
    settings.configure(USE_I18N=False)

MODULE_PATH = Path(__file__).resolve().parents[1] / "management" / "commands" / "createsuperuser_if_not_exists.py"
spec = importlib.util.spec_from_file_location("s1_superuser_command", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SuperuserEnvironmentTests(unittest.TestCase):
    def invoke(self, environment, created=True):
        output, errors = io.StringIO(), io.StringIO()
        command = module.Command(stdout=output, stderr=errors)
        user = Mock(is_staff=False, is_superuser=False, email="old@example.invalid")
        model = Mock()
        model.objects.get_or_create.return_value = (user, created)
        return command, user, model, output, errors

    def test_missing_empty_and_whitespace_refuse_before_model_access(self):
        for environment in ({}, {"DJANGO_SUPERUSER_PASSWORD": ""}, {"DJANGO_SUPERUSER_PASSWORD": " \t\n"}):
            with self.subTest(case=len(environment)):
                command, user, model, output, errors = self.invoke(environment)
                with patch.dict(os.environ, environment, clear=True), patch.object(module, "get_user_model", return_value=model) as get_model:
                    with self.assertRaises(CommandError):
                        command.handle()
                get_model.assert_not_called()
                model.objects.get_or_create.assert_not_called()
                user.set_password.assert_not_called()
                user.save.assert_not_called()
                self.assertEqual(output.getvalue() + errors.getvalue(), "")

    def test_explicit_synthetic_password_creates_without_secret_output(self):
        secret = "S1-synthetic-only-" + "example.invalid"
        environment = {"DJANGO_SUPERUSER_PASSWORD": secret}
        command, user, model, output, errors = self.invoke(environment)
        with patch.dict(os.environ, environment, clear=True), patch.object(module, "get_user_model", return_value=model):
            command.handle()
        user.set_password.assert_called_once_with(secret)
        user.save.assert_called_once_with()
        self.assertTrue(user.is_staff and user.is_superuser)
        self.assertNotIn(secret, output.getvalue() + errors.getvalue())

    def test_existing_user_password_is_not_reset(self):
        secret = "S1-synthetic-only-" + "example.invalid"
        command, user, model, output, errors = self.invoke({}, created=False)
        with patch.dict(os.environ, {"DJANGO_SUPERUSER_PASSWORD": secret}, clear=True), patch.object(module, "get_user_model", return_value=model):
            command.handle()
        user.set_password.assert_not_called()
        user.save.assert_called_once()
        self.assertNotIn(secret, output.getvalue() + errors.getvalue())


if __name__ == "__main__":
    unittest.main()
