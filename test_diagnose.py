import errno
import json
import os
import shutil
import tempfile
import unittest

SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'diagnose.py')

try:
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader('diagnose', SCRIPT_PATH)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    diagnose = importlib.util.module_from_spec(spec)
    loader.exec_module(diagnose)
except (ImportError, AttributeError):
    import imp
    diagnose = imp.load_source('diagnose', SCRIPT_PATH)

try:
    TemporaryDirectory = tempfile.TemporaryDirectory
except AttributeError:
    import contextlib

    @contextlib.contextmanager
    def TemporaryDirectory():
        directory = tempfile.mkdtemp()
        try:
            yield directory
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class DiagnoseTests(unittest.TestCase):
    def test_reporter_counts(self):
        rep = diagnose.DiagnosticReporter()
        rep.ok('Cat', 'pass msg')
        rep.warn('Cat', 'warn msg')
        rep.fail('Cat', 'fail msg')
        self.assertEqual(rep.passes, 1)
        self.assertEqual(rep.warns, 1)
        self.assertEqual(rep.fails, 1)

    def test_check_shelves_config_valid(self):
        with TemporaryDirectory() as d:
            cfg_path = os.path.join(d, 'detailconfig.json')
            data = {
                'smartConfig': [{
                    'ai_home': {
                        'ai_home_info': [
                            {'shelfId': 'HOME_SH_APPS'},
                            {'shelfId': 'HOME_SH_HOMEDASHBOARD'},
                            {'shelfId': 'HOME_SH_CONTENTPROMOTION'},
                            {'shelfId': 'CUSTOM_FUTURE_SHELF'},
                        ]
                    }
                }]
            }
            with open(cfg_path, 'w') as f:
                json.dump(data, f)

            orig_source = diagnose.OVERRIDE_SOURCE
            try:
                diagnose.OVERRIDE_SOURCE = cfg_path
                rep = diagnose.DiagnosticReporter()
                diagnose.check_shelves_config(rep)
                self.assertGreater(rep.passes, 0)
                self.assertEqual(rep.fails, 0)
            finally:
                diagnose.OVERRIDE_SOURCE = orig_source

    def test_check_shelves_config_missing_required(self):
        with TemporaryDirectory() as d:
            cfg_path = os.path.join(d, 'detailconfig.json')
            data = {
                'smartConfig': [{
                    'ai_home': {
                        'ai_home_info': [
                            {'shelfId': 'HOME_SH_APPS'},
                        ]
                    }
                }]
            }
            with open(cfg_path, 'w') as f:
                json.dump(data, f)

            orig_source = diagnose.OVERRIDE_SOURCE
            try:
                diagnose.OVERRIDE_SOURCE = cfg_path
                rep = diagnose.DiagnosticReporter()
                diagnose.check_shelves_config(rep)
                self.assertGreater(rep.fails, 0)
            finally:
                diagnose.OVERRIDE_SOURCE = orig_source


if __name__ == '__main__':
    unittest.main()
