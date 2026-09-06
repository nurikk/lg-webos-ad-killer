import errno
import json
import os
import io
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


    def test_missing_systemctl_is_a_failure(self):
        original_check_command = diagnose.check_command
        try:
            diagnose.check_command = lambda name: None if name == 'systemctl' else '/bin/' + name
            rep = diagnose.DiagnosticReporter()
            diagnose.check_utilities(rep)
            self.assertEqual(rep.fails, 1)
        finally:
            diagnose.check_command = original_check_command

    def test_default_filesystem_check_does_not_touch_tmp_or_mount(self):
        with TemporaryDirectory() as directory:
            target = os.path.join(directory, 'target')
            attack_path = '/tmp/ad_killer_diag_test_{0}'.format(os.getpid())
            with open(target, 'w') as target_file:
                target_file.write('unchanged')
            if os.path.lexists(attack_path):
                self.skipTest('test path already exists')
            os.symlink(target, attack_path)
            original_mkdtemp = diagnose.tempfile.mkdtemp
            original_run_cmd = diagnose.run_cmd
            try:
                diagnose.tempfile.mkdtemp = lambda *args, **kwargs: self.fail('default check created a temporary directory')
                diagnose.run_cmd = lambda args: self.fail('default check ran {0}'.format(args))
                diagnose.check_filesystem_and_mounts(diagnose.DiagnosticReporter())
                with open(target) as target_file:
                    self.assertEqual(target_file.read(), 'unchanged')
            finally:
                diagnose.tempfile.mkdtemp = original_mkdtemp
                diagnose.run_cmd = original_run_cmd
                os.unlink(attack_path)

    def test_bind_mount_probe_unmounts_once_after_acquisition(self):
        with TemporaryDirectory() as directory:
            probe_dir = os.path.join(directory, 'probe')
            os.mkdir(probe_dir)
            calls = []
            original_mkdtemp = diagnose.tempfile.mkdtemp
            original_run_cmd = diagnose.run_cmd

            def fake_run_cmd(args):
                calls.append(args)
                return 0, ''

            try:
                diagnose.tempfile.mkdtemp = lambda **kwargs: probe_dir
                diagnose.run_cmd = fake_run_cmd
                diagnose.check_bind_mount(diagnose.DiagnosticReporter())
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0][0], 'mount')
                self.assertEqual(calls[1][0], 'umount')
            finally:
                diagnose.tempfile.mkdtemp = original_mkdtemp
                diagnose.run_cmd = original_run_cmd

    def test_unicode_unknown_shelf_is_reported(self):
        with TemporaryDirectory() as directory:
            cfg_path = os.path.join(directory, 'detailconfig.json')
            unknown = u'HOME_SH_\u2603'
            data = {
                'smartConfig': [{
                    'ai_home': {
                        'ai_home_info': [
                            {'shelfId': 'HOME_SH_APPS'},
                            {'shelfId': 'HOME_SH_HOMEDASHBOARD'},
                            {'shelfId': unknown},
                        ]
                    }
                }]
            }
            with open(cfg_path, 'w') as config_file:
                json.dump(data, config_file)
            original_source = diagnose.OVERRIDE_SOURCE
            original_stdout = diagnose.sys.stdout
            try:
                diagnose.OVERRIDE_SOURCE = cfg_path
                if diagnose.sys.version_info[0] == 2:
                    output = io.BytesIO()
                    expected = unknown.encode('utf-8')
                else:
                    output = io.StringIO()
                    expected = unknown
                diagnose.sys.stdout = output
                rep = diagnose.DiagnosticReporter()
                diagnose.check_shelves_config(rep)
                self.assertEqual(rep.fails, 0)
                self.assertIn(expected, output.getvalue())
            finally:
                diagnose.OVERRIDE_SOURCE = original_source
                diagnose.sys.stdout = original_stdout

    def test_malformed_shelf_id_is_a_diagnostic_failure(self):
        with TemporaryDirectory() as directory:
            cfg_path = os.path.join(directory, 'detailconfig.json')
            data = {
                'smartConfig': [{
                    'ai_home': {
                        'ai_home_info': [
                            {'shelfId': 'HOME_SH_APPS'},
                            {'shelfId': {}},
                            {'shelfId': 'HOME_SH_HOMEDASHBOARD'},
                        ]
                    }
                }]
            }
            with open(cfg_path, 'w') as config_file:
                json.dump(data, config_file)
            original_source = diagnose.OVERRIDE_SOURCE
            try:
                diagnose.OVERRIDE_SOURCE = cfg_path
                rep = diagnose.DiagnosticReporter()
                diagnose.check_shelves_config(rep)
                self.assertEqual(rep.fails, 1)
            finally:
                diagnose.OVERRIDE_SOURCE = original_source

    def test_process_readlink_errors_are_ignored(self):
        original_readlink = diagnose.os.readlink

        def fail_readlink(path):
            raise OSError(errno.EIO, 'proc I/O error')

        try:
            diagnose.os.readlink = fail_readlink
            self.assertIsNone(diagnose.exact_process_path(42))
        finally:
            diagnose.os.readlink = original_readlink

if __name__ == '__main__':
    unittest.main()
