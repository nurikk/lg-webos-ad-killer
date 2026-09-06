import errno
import json
import os
import shutil
import tempfile
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


if __name__ == '__main__':
    unittest.main()
