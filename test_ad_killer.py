import errno
import json
import os
import io
import shutil
import tempfile
import subprocess
import unittest

SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ad_killer')

try:
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader('ad_killer', SCRIPT_PATH)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('could not load ad_killer')
    ad_killer = importlib.util.module_from_spec(spec)
    loader.exec_module(ad_killer)
except (ImportError, AttributeError):
    import imp
    ad_killer = imp.load_source('ad_killer', SCRIPT_PATH)

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

_symlinks = {}
if not hasattr(os, 'readlink'):
    def _mock_readlink(path):
        normalized = os.path.normpath(path)
        if normalized in _symlinks:
            return _symlinks[normalized]
        raise OSError(errno.ENOENT, 'No such file or directory: ' + path)

    os.readlink = _mock_readlink

try:
    from pathlib import Path
except ImportError:
    class Path(object):
        def __init__(self, path):
            self._path = str(path)

        def __fspath__(self):
            return self._path

        def __str__(self):
            return self._path

        def __div__(self, other):
            return Path(os.path.join(self._path, str(other)))

        def __truediv__(self, other):
            return Path(os.path.join(self._path, str(other)))

        def write_text(self, text):
            with open(self._path, 'w') as f:
                f.write(text)

        def read_text(self):
            with open(self._path, 'r') as f:
                return f.read()

        def mkdir(self):
            os.mkdir(self._path)

        def symlink_to(self, target):
            if hasattr(os, 'symlink'):
                os.symlink(target, self._path)
            else:
                _symlinks[os.path.normpath(self._path)] = target

class AdKillerTests(unittest.TestCase):
    def setUp(self):
        if not hasattr(self, 'assertRaisesRegex'):
            self.assertRaisesRegex = self.assertRaisesRegexp

    def test_clean_shelves_removes_known_promotions_and_preserves_unknown(self):
        data = {
            'smartConfig': [
                {
                    'ai_home': {
                        'ai_home_info': [
                            {'shelfId': 'HOME_SH_APPS'},
                            {'shelfId': 'HOME_SH_CONTENTPROMOTION'},
                            {'shelfId': 'HOME_SH_FUTURE_FEATURE'},
                            {'shelfId': 'HOME_SH_HOMEDASHBOARD'},
                        ]
                    }
                },
                {'unrelated': True},
            ]
        }
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'source.json'
            destination = Path(directory) / 'destination.json'
            source.write_text(json.dumps(data))

            removed = ad_killer.clean_shelves(source, destination)
            result = json.loads(destination.read_text())

        shelves = result['smartConfig'][0]['ai_home']['ai_home_info']
        self.assertEqual(
            [shelf['shelfId'] for shelf in shelves],
            ['HOME_SH_APPS', 'HOME_SH_FUTURE_FEATURE', 'HOME_SH_HOMEDASHBOARD'],
        )
        self.assertEqual(removed, {'HOME_SH_CONTENTPROMOTION'})

    def test_clean_shelves_rejects_config_without_required_shelves(self):
        data = {'smartConfig': [{'ai_home': {'ai_home_info': [{'shelfId': 'HOME_SH_APPS'}]}}]}
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'source.json'
            destination = Path(directory) / 'destination.json'
            source.write_text(json.dumps(data))

            with self.assertRaisesRegex(ValueError, 'required shelves missing'):
                ad_killer.clean_shelves(source, destination)

    def test_matching_pids_uses_exact_executable_paths(self):
        with TemporaryDirectory() as directory:
            proc_root = Path(directory)
            for pid, executable in {
                101: '/usr/sbin/sdx',
                102: '/usr/sbin/sdx-helper',
                103: '/usr/sbin/acr2',
            }.items():
                process = proc_root / str(pid)
                process.mkdir()
                (process / 'exe').symlink_to(executable)

            matches = ad_killer.matching_pids(
                {'/usr/sbin/sdx', '/usr/sbin/acr2'},
                str(proc_root),
            )

        self.assertEqual(matches, [101, 103])

    def test_privacy_profile_includes_opted_out_features_but_not_sdx(self):
        ads = ad_killer.selected_executables('ads')
        privacy = ad_killer.selected_executables('privacy')
        self.assertLess(ads, privacy)
        self.assertIn('/usr/sbin/acr2', ads)
        self.assertIn('/usr/sbin/voiceinput', privacy)
        self.assertIn('/usr/sbin/amazon-alexa-adapter', privacy)
        self.assertIn('/usr/sbin/lg.thinqai.adapter', privacy)
        self.assertIn(
            '/var/palm/jail/amazon.alexa.adapter/usr/sbin/amazon-alexa-adapter',
            privacy,
        )
        self.assertIn(
            '/var/palm/jail/lg.thinqai.adapter/usr/sbin/lg.thinqai.adapter',
            privacy,
        )
        self.assertNotIn(ad_killer.SDX_EXECUTABLE, privacy)
        self.assertNotIn(ad_killer.HOME_EXECUTABLE, privacy)

    def test_parse_args_defaults_and_options(self):
        args = ad_killer.parse_args([])
        self.assertEqual(args.profile, 'privacy')
        self.assertFalse(args.dry_run)
        self.assertFalse(args.disable)
        self.assertFalse(args.enable)
        self.assertFalse(args.refresh_home)

        custom = ad_killer.parse_args(['--profile', 'ads', '--dry-run', '--refresh-home'])
        self.assertEqual(custom.profile, 'ads')
        self.assertTrue(custom.dry_run)
        self.assertTrue(custom.refresh_home)


    def test_launcher_runs_help(self):
        process = subprocess.Popen(
            [SCRIPT_PATH, '--help'], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate()
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn(b'usage: ad_killer', stdout)

    def test_clean_shelves_rejects_malformed_shelf_id(self):
        data = {
            'smartConfig': [{
                'ai_home': {
                    'ai_home_info': [
                        {'shelfId': 'HOME_SH_APPS'},
                        {'shelfId': []},
                        {'shelfId': 'HOME_SH_HOMEDASHBOARD'},
                    ]
                }
            }]
        }
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'source.json'
            destination = Path(directory) / 'destination.json'
            source.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'invalid shelfId'):
                ad_killer.clean_shelves(source, destination)

    def test_block_state_errors_other_than_enoent_propagate(self):
        original_state = ad_killer.BLOCK_STATE
        original_open = getattr(ad_killer, 'open', None)

        def fail_open(*args, **kwargs):
            raise IOError(errno.EACCES, 'permission denied')

        with TemporaryDirectory() as directory:
            try:
                ad_killer.BLOCK_STATE = os.path.join(directory, 'blocked-state')
                ad_killer.open = fail_open
                with self.assertRaises(IOError):
                    ad_killer.previous_block_paths()
            finally:
                ad_killer.BLOCK_STATE = original_state
                if original_open is None:
                    del ad_killer.open
                else:
                    ad_killer.open = original_open

    def test_block_state_deletion_errors_other_than_enoent_propagate(self):
        original_state = ad_killer.BLOCK_STATE
        original_unlink = ad_killer.os.unlink
        original_previous = ad_killer.previous_block_paths
        original_run = ad_killer.run

        with TemporaryDirectory() as directory:
            state_path = os.path.join(directory, 'blocked-state')
            with open(state_path, 'w') as state_file:
                state_file.write('unused\n')

            def fail_unlink(path):
                if path == state_path:
                    raise OSError(errno.EROFS, 'read-only filesystem')
                return original_unlink(path)

            try:
                ad_killer.BLOCK_STATE = state_path
                ad_killer.previous_block_paths = lambda: []
                ad_killer.run = lambda *args, **kwargs: self.fail('external command executed')
                ad_killer.os.unlink = fail_unlink
                with self.assertRaises(OSError):
                    ad_killer.remove_process_blocks()
            finally:
                ad_killer.BLOCK_STATE = original_state
                ad_killer.previous_block_paths = original_previous
                ad_killer.run = original_run
                ad_killer.os.unlink = original_unlink

    def test_enable_marker_errors_other_than_enoent_propagate(self):
        original_unlink = ad_killer.os.unlink
        original_parse_args = ad_killer.parse_args

        def fail_unlink(path):
            if path == ad_killer.DISABLE_MARKER:
                raise OSError(errno.EACCES, 'permission denied')
            return original_unlink(path)

        try:
            ad_killer.os.unlink = fail_unlink
            ad_killer.parse_args = lambda: ad_killer.Namespace(
                disable=False, dry_run=False, enable=True, refresh_home=False, profile='privacy'
            )
            with self.assertRaises(OSError):
                ad_killer.main()
        finally:
            ad_killer.os.unlink = original_unlink
            ad_killer.parse_args = original_parse_args

    def test_watchdog_esrch_is_ignored(self):
        original_pid_file = ad_killer.LEGACY_WATCHDOG_PID
        original_open = getattr(ad_killer, 'open', None)
        builtin_open = open
        original_kill = ad_killer.os.kill

        def fake_open(path, *args, **kwargs):
            if path == '/proc/42/cmdline':
                return io.BytesIO(b'ad_killer\0--watch')
            return builtin_open(path, *args, **kwargs)

        def missing_process(pid, signal_number):
            raise OSError(errno.ESRCH, 'process disappeared')

        with TemporaryDirectory() as directory:
            pid_file = os.path.join(directory, 'watchdog.pid')
            with open(pid_file, 'w') as state_file:
                state_file.write('42')
            try:
                ad_killer.LEGACY_WATCHDOG_PID = pid_file
                ad_killer.open = fake_open
                ad_killer.os.kill = missing_process
                ad_killer.stop_legacy_watchdog()
                self.assertFalse(os.path.exists(pid_file))
            finally:
                ad_killer.LEGACY_WATCHDOG_PID = original_pid_file
                ad_killer.os.kill = original_kill
                if original_open is None:
                    del ad_killer.open
                else:
                    ad_killer.open = original_open

    def test_process_readlink_errors_are_ignored(self):
        original_readlink = ad_killer.os.readlink

        def fail_readlink(path):
            raise OSError(errno.EIO, 'proc I/O error')

        try:
            ad_killer.os.readlink = fail_readlink
            self.assertIsNone(ad_killer.exact_process_path(42))
        finally:
            ad_killer.os.readlink = original_readlink

if __name__ == '__main__':
    unittest.main()
