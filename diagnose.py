#!/usr/bin/python
"""
diagnose.py - Diagnostic check for LG webOS ad_killer compatibility.
Compatible with Python 2.7.16 and Python 3.x.
Performs read-only inspection of system files, executables, processes, and configuration.
"""

from __future__ import print_function

import errno
import json
import os
import signal
import subprocess
import sys

try:
    string_types = (str, unicode)
except NameError:
    string_types = (str,)

OVERRIDE_SOURCE = '/mnt/lg/cmn_data/sdp/sdx/detailconfig.json'
OVERRIDE_DESTINATION = '/tmp/ad_killer-detailconfig.json'
BLOCK_STUB = '/tmp/ad_killer-blocked'
BLOCK_STATE = '/tmp/ad_killer-blocked-paths'
LEGACY_WATCHDOG_PID = '/tmp/ad_killer-watchdog.pid'
DISABLE_MARKER = '/var/lib/webosbrew/ad-killer.disabled'
WEBOSBREW_DIR = '/var/lib/webosbrew'
WEBOSBREW_INIT_D = '/var/lib/webosbrew/init.d'

REQUIRED_SHELVES = set(['HOME_SH_APPS', 'HOME_SH_HOMEDASHBOARD'])
PROMOTIONAL_SHELVES = set([
    'HOME_SH_AMAZONPRIME',
    'HOME_SH_BROWSER',
    'HOME_SH_CONTENTPROMOTION',
    'HOME_SH_CONTENT_PREVIEW',
    'HOME_SH_EDITORSPICK',
    'HOME_SH_GAME',
    'HOME_SH_INLINEADBANNER',
    'HOME_SH_LGCHANNELS_GB',
    'HOME_SH_LGCHANNELS_GIP',
    'HOME_SH_LGRECOMMEND_1',
    'HOME_SH_LGRECOMMEND_2',
    'HOME_SH_LIFEONSCREEN',
    'HOME_SH_LIVETV',
    'HOME_SH_RECOMMENDED',
    'HOME_SH_SHOPPING_EU',
    'HOME_SH_SPORTSALERT',
])

ADS_TELEMETRY_EXECUTABLES = [
    '/usr/sbin/acr2',
    '/usr/sbin/admanager',
    '/usr/sbin/nudge',
    '/usr/sbin/rdxd',
    '/usr/sbin/sportsalarm',
    '/usr/sbin/uploadd',
]

PRIVACY_BLOAT_EXECUTABLES = [
    '/usr/sbin/amazon-alexa-adapter',
    '/usr/sbin/amazon-alexa-vsk',
    '/usr/sbin/iot-client',
    '/usr/sbin/lg.thinqai.adapter',
    '/usr/sbin/mqtt-client',
    '/usr/sbin/musicrecognition',
    '/usr/sbin/nlpmanager',
    '/usr/sbin/performer',
    '/usr/sbin/voiceconductor',
    '/usr/sbin/voiceinput',
    '/var/palm/jail/amazon.alexa.adapter/usr/sbin/amazon-alexa-adapter',
    '/var/palm/jail/amazon.alexa.adapter/usr/sbin/amazon-alexa-vsk',
    '/var/palm/jail/com.webos.service.voice.performer/usr/sbin/performer',
    '/var/palm/jail/lg.thinqai.adapter/usr/sbin/lg.thinqai.adapter',
]

SDX_EXECUTABLE = '/usr/sbin/sdx'
HOME_EXECUTABLE = '/usr/bin/com.webos.app.home'

ADS_SYSTEMD_UNITS = [
    'nudge.service',
    'uploadd.service',
]
PRIVACY_SYSTEMD_UNITS = [
    'musicrecognition.service',
    'voiceconductor.service',
    'voiceinput.service',
]


class DiagnosticReporter(object):
    def __init__(self):
        self.passes = 0
        self.warns = 0
        self.fails = 0

    def ok(self, category, message):
        self.passes += 1
        print('[PASS] {0}: {1}'.format(category, message))

    def warn(self, category, message):
        self.warns += 1
        print('[WARN] {0}: {1}'.format(category, message))

    def fail(self, category, message):
        self.fails += 1
        print('[FAIL] {0}: {1}'.format(category, message))

    def info(self, category, message):
        print('[INFO] {0}: {1}'.format(category, message))


def check_command(name):
    devnull = open(os.devnull, 'wb')
    try:
        proc = subprocess.Popen(['which', name], stdout=subprocess.PIPE, stderr=devnull)
        out, _ = proc.communicate()
        if proc.returncode == 0 and out.strip():
            return out.strip().decode('utf-8', 'replace')
    except (EnvironmentError, OSError):
        pass
    finally:
        devnull.close()

    for path_dir in ['/bin', '/usr/bin', '/sbin', '/usr/sbin']:
        candidate = os.path.join(path_dir, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def run_cmd(args):
    devnull = open(os.devnull, 'wb')
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=devnull)
        out, _ = proc.communicate()
        return proc.returncode, out.decode('utf-8', 'replace').strip()
    except (EnvironmentError, OSError):
        return -1, ''
    finally:
        devnull.close()


def exact_process_path(pid, proc_root='/proc'):
    try:
        path = os.readlink(os.path.join(proc_root, str(pid), 'exe'))
    except (EnvironmentError, AttributeError):
        return None
    suffix = ' (deleted)'
    return path[:-len(suffix)] if path.endswith(suffix) else path


def find_running_pids(proc_root='/proc'):
    mapping = {}
    if not os.path.isdir(proc_root):
        return mapping
    for entry in os.listdir(proc_root):
        if not entry.isdigit():
            continue
        pid = int(entry)
        path = exact_process_path(pid, proc_root)
        if path:
            mapping.setdefault(path, []).append(pid)
    return mapping


def check_system(diag):
    diag.info('Runtime', 'Python {0} at {1}'.format(sys.version.split()[0], sys.executable))

    if hasattr(os, 'geteuid'):
        euid = os.geteuid()
        if euid == 0:
            diag.ok('Privilege', 'Running as root (UID 0)')
        else:
            diag.warn('Privilege', 'Running as UID {0}; root is required to mount and stop services'.format(euid))
    else:
        diag.info('Privilege', 'UID check unavailable on this platform')

    # webOS release info
    for release_file in ['/etc/webos-release', '/etc/os-release']:
        if os.path.isfile(release_file):
            try:
                with open(release_file) as f:
                    lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
                diag.ok('webOS Info', '{0}: {1}'.format(release_file, '; '.join(lines[:4])))
                break
            except (IOError, OSError) as e:
                diag.warn('webOS Info', 'Could not read {0}: {1}'.format(release_file, e))
    else:
        diag.info('webOS Info', 'No /etc/webos-release or /etc/os-release found')

    # nyx-cmd hardware query
    code, out = run_cmd(['nyx-cmd', 'DeviceInfo', 'query', 'device_name'])
    if code == 0 and out:
        diag.info('Hardware', 'Device: {0}'.format(out))


def check_utilities(diag):
    mount_bin = check_command('mount')
    if mount_bin:
        diag.ok('Command', 'mount found at {0}'.format(mount_bin))
    else:
        diag.fail('Command', 'mount utility not found; cannot apply bind mounts')

    umount_bin = check_command('umount')
    if umount_bin:
        diag.ok('Command', 'umount found at {0}'.format(umount_bin))
    else:
        diag.fail('Command', 'umount utility not found; cannot revert bind mounts')

    systemctl_bin = check_command('systemctl')
    if systemctl_bin:
        diag.ok('Command', 'systemctl found at {0}'.format(systemctl_bin))
    else:
        diag.warn('Command', 'systemctl not found; service stopping will fall back or be skipped')


def check_shelves_config(diag):
    if not os.path.exists(OVERRIDE_SOURCE):
        diag.fail('Shelf Config', '{0} does not exist'.format(OVERRIDE_SOURCE))
        return

    if not os.path.isfile(OVERRIDE_SOURCE):
        diag.fail('Shelf Config', '{0} is not a regular file'.format(OVERRIDE_SOURCE))
        return

    try:
        with open(OVERRIDE_SOURCE, 'r') as f:
            data = json.load(f)
    except Exception as e:
        diag.fail('Shelf Config', 'Failed to parse JSON in {0}: {1}'.format(OVERRIDE_SOURCE, e))
        return

    if not isinstance(data, dict) or not isinstance(data.get('smartConfig'), list):
        diag.fail('Shelf Config', 'Invalid smartConfig structure in {0}'.format(OVERRIDE_SOURCE))
        return

    total_shelves = 0
    found_shelf_ids = []
    for sc in data['smartConfig']:
        if not isinstance(sc, dict):
            continue
        ai_home = sc.get('ai_home')
        if not isinstance(ai_home, dict):
            continue
        shelves = ai_home.get('ai_home_info')
        if isinstance(shelves, list):
            for s in shelves:
                if isinstance(s, dict):
                    sid = s.get('shelfId')
                    if sid and isinstance(sid, string_types):
                        found_shelf_ids.append(str(sid))
                        total_shelves += 1

    shelf_set = set(found_shelf_ids)
    missing_required = REQUIRED_SHELVES - shelf_set
    if missing_required:
        diag.fail(
            'Shelf Config',
            'Missing required shelves: {0} (found {1} shelves: {2})'.format(
                sorted(missing_required), total_shelves, found_shelf_ids
            )
        )
    else:
        diag.ok(
            'Shelf Config',
            'Required shelves present ({0})'.format(', '.join(sorted(REQUIRED_SHELVES)))
        )

    promos_to_remove = PROMOTIONAL_SHELVES & shelf_set
    if promos_to_remove:
        diag.ok(
            'Shelf Config',
            '{0} promotional shelves detected for removal: {1}'.format(
                len(promos_to_remove), ', '.join(sorted(promos_to_remove))
            )
        )
    else:
        diag.info('Shelf Config', 'No known promotional shelves present in config')

    unknown_shelves = shelf_set - PROMOTIONAL_SHELVES - REQUIRED_SHELVES
    if unknown_shelves:
        diag.info(
            'Shelf Config',
            '{0} custom/future shelves will be preserved: {1}'.format(
                len(unknown_shelves), ', '.join(sorted(unknown_shelves))
            )
        )


def check_executables_and_processes(diag):
    running = find_running_pids()

    # Telemetry
    present_ads = []
    running_ads = []
    for exe in ADS_TELEMETRY_EXECUTABLES:
        exists = os.path.isfile(exe)
        pids = running.get(exe, [])
        if exists:
            present_ads.append(exe)
        if pids:
            running_ads.append('{0} (PID {1})'.format(os.path.basename(exe), ','.join(map(str, pids))))

    diag.info(
        'Ads/Telemetry',
        '{0}/{1} executables exist on disk ({2})'.format(
            len(present_ads), len(ADS_TELEMETRY_EXECUTABLES),
            ', '.join([os.path.basename(e) for e in present_ads]) or 'none'
        )
    )
    if running_ads:
        diag.ok('Ads/Telemetry', 'Running processes detected: {0}'.format(', '.join(running_ads)))
    else:
        diag.info('Ads/Telemetry', 'No ads/telemetry processes currently running')

    # Privacy
    present_privacy = []
    running_privacy = []
    for exe in PRIVACY_BLOAT_EXECUTABLES:
        exists = os.path.isfile(exe)
        pids = running.get(exe, [])
        if exists:
            present_privacy.append(exe)
        if pids:
            running_privacy.append('{0} (PID {1})'.format(os.path.basename(exe), ','.join(map(str, pids))))

    diag.info(
        'Privacy/Bloat',
        '{0}/{1} executables exist on disk ({2})'.format(
            len(present_privacy), len(PRIVACY_BLOAT_EXECUTABLES),
            ', '.join([os.path.basename(e) for e in present_privacy]) or 'none'
        )
    )
    if running_privacy:
        diag.ok('Privacy/Bloat', 'Running processes detected: {0}'.format(', '.join(running_privacy)))
    else:
        diag.info('Privacy/Bloat', 'No privacy/bloat processes currently running')

    # Core
    sdx_pids = running.get(SDX_EXECUTABLE, [])
    if os.path.isfile(SDX_EXECUTABLE):
        diag.ok(
            'Core Service',
            'sdx executable found at {0} (running: PID {1})'.format(
                SDX_EXECUTABLE, ','.join(map(str, sdx_pids)) or 'no'
            )
        )
    else:
        diag.warn('Core Service', 'sdx not found at {0}'.format(SDX_EXECUTABLE))

    home_pids = running.get(HOME_EXECUTABLE, [])
    if os.path.isfile(HOME_EXECUTABLE):
        diag.info(
            'Core Service',
            'Home app executable found at {0} (running: PID {1})'.format(
                HOME_EXECUTABLE, ','.join(map(str, home_pids)) or 'no'
            )
        )


def check_systemd_units(diag):
    if not check_command('systemctl'):
        return

    for unit in ADS_SYSTEMD_UNITS + PRIVACY_SYSTEMD_UNITS:
        code, state = run_cmd(['systemctl', 'is-active', unit])
        if code == 0:
            diag.ok('Service Unit', '{0} is active'.format(unit))
        else:
            diag.info('Service Unit', '{0} status: {1}'.format(unit, state or 'inactive/not found'))


def check_filesystem_and_mounts(diag):
    # Test /tmp writable
    test_file = '/tmp/ad_killer_diag_test_{0}'.format(os.getpid())
    try:
        with open(test_file, 'w') as f:
            f.write('test\n')
        os.unlink(test_file)
        diag.ok('Filesystem', '/tmp is writable')
    except (IOError, OSError) as e:
        diag.fail('Filesystem', 'Cannot write to /tmp: {0}'.format(e))

    # Test /var/lib/webosbrew
    if os.path.isdir(WEBOSBREW_DIR):
        writable = os.access(WEBOSBREW_DIR, os.W_OK)
        if writable:
            diag.ok('webosbrew', '{0} exists and is writable'.format(WEBOSBREW_DIR))
        else:
            diag.warn('webosbrew', '{0} exists but is not writable (root required)'.format(WEBOSBREW_DIR))
    else:
        diag.warn('webosbrew', '{0} directory not found (Homebrew Channel installed?)'.format(WEBOSBREW_DIR))

    if os.path.isdir(WEBOSBREW_INIT_D):
        diag.ok('webosbrew', '{0} directory exists for boot execution'.format(WEBOSBREW_INIT_D))
    else:
        diag.info('webosbrew', '{0} directory does not exist yet'.format(WEBOSBREW_INIT_D))

    # Test bind mount if root
    if hasattr(os, 'geteuid') and os.geteuid() == 0 and check_command('mount') and check_command('umount'):
        src_test = '/tmp/ad_killer_bm_src_{0}'.format(os.getpid())
        dst_test = '/tmp/ad_killer_bm_dst_{0}'.format(os.getpid())
        try:
            with open(src_test, 'w') as f:
                f.write('src\n')
            with open(dst_test, 'w') as f:
                f.write('dst\n')
            ret, _ = run_cmd(['mount', '--bind', src_test, dst_test])
            if ret == 0:
                diag.ok('Bind Mount', 'Kernel bind mounts function correctly')
                run_cmd(['umount', dst_test])
            else:
                diag.warn('Bind Mount', 'mount --bind returned exit code {0}'.format(ret))
        except Exception as e:
            diag.warn('Bind Mount', 'Bind mount test raised: {0}'.format(e))
        finally:
            try:
                run_cmd(['umount', dst_test])
            except Exception:
                pass
            for p in [src_test, dst_test]:
                try:
                    os.unlink(p)
                except (IOError, OSError):
                    pass


def main():
    print('=' * 60)
    print('  LG webOS ad_killer Compatibility Diagnostics')
    print('=' * 60)

    diag = DiagnosticReporter()
    check_system(diag)
    print('-' * 60)
    check_utilities(diag)
    print('-' * 60)
    check_shelves_config(diag)
    print('-' * 60)
    check_executables_and_processes(diag)
    print('-' * 60)
    check_systemd_units(diag)
    print('-' * 60)
    check_filesystem_and_mounts(diag)
    print('=' * 60)
    print('Summary: {0} passed, {1} warnings, {2} failures'.format(
        diag.passes, diag.warns, diag.fails
    ))

    if diag.fails > 0:
        print('VERDICT: Incompatible or missing prerequisites detected.')
        print('Review [FAIL] items above before running ad_killer.')
        sys.exit(1)
    elif diag.warns > 0:
        print('VERDICT: Compatible with warnings.')
        print('Review [WARN] items above (e.g. root privilege).')
        sys.exit(0)
    else:
        print('VERDICT: Fully compatible.')
        print('Target TV has all expected webOS components for ad_killer.')
        sys.exit(0)


if __name__ == '__main__':
    main()
