"""
Migration dependency guard — pure Python, no Django DB needed.

`makemigrations` pins a new migration to the *latest* Alliance Auth
migration installed where it runs. Generated on Auth 5.6 that is
``eveonline`` 0025, which only exists from Auth 5.1.4, so ``migrate``
would fail on the older 5.x releases this app supports. Hand-edit any new
migration's ``eveonline`` dependency back to one listed here.
"""

# Standard Library
import importlib
import pkgutil
import unittest

# Oldest supported Alliance Auth is 5.0.0. These eveonline migrations ship in
# every 5.x release; anything newer breaks `migrate` on early 5.x.
SUPPORTED_EVEONLINE = {
    "0021_alter_eveallianceinfo_options_and_more",
}


def _migration_modules():
    package = importlib.import_module("aa_pvetracker.migrations")
    for info in pkgutil.iter_modules(package.__path__):
        if info.name[:4].isdigit():
            yield info.name, importlib.import_module(f"{package.__name__}.{info.name}")


class MigrationDependencyTests(unittest.TestCase):
    def test_eveonline_dependencies_exist_on_every_supported_auth(self):
        found = False
        for name, module in _migration_modules():
            for app_label, migration in module.Migration.dependencies:
                if app_label != "eveonline":
                    continue
                found = True
                with self.subTest(migration=name):
                    self.assertIn(
                        migration,
                        SUPPORTED_EVEONLINE,
                        f"{name} depends on eveonline {migration}, which Auth 5.0.0 does not have",
                    )
        self.assertTrue(found, "expected at least one eveonline dependency")


if __name__ == "__main__":
    unittest.main()
