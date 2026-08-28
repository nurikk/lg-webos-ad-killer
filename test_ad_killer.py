import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).with_name('ad_killer')
loader = importlib.machinery.SourceFileLoader('ad_killer', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
if spec is None:
    raise RuntimeError('could not load ad_killer')
ad_killer = importlib.util.module_from_spec(spec)
loader.exec_module(ad_killer)


class AdKillerTests(unittest.TestCase):
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
        with tempfile.TemporaryDirectory() as directory:
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
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.json'
            destination = Path(directory) / 'destination.json'
            source.write_text(json.dumps(data))

            with self.assertRaisesRegex(ValueError, 'required shelves missing'):
                ad_killer.clean_shelves(source, destination)

    def test_matching_pids_uses_exact_executable_paths(self):
        with tempfile.TemporaryDirectory() as directory:
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


if __name__ == '__main__':
    unittest.main()
