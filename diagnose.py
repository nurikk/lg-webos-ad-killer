#!/bin/sh
""":"
if [ -x /usr/bin/python3 ]; then
    exec /usr/bin/python3 "$0" "$@"
fi
exec /usr/bin/python "$0" "$@"
":"""
"""
diagnose.py - Diagnostic check for LG webOS ad_killer compatibility.
Compatible with Python 2.7.16 and Python 3.x.
Performs non-invasive inspection by default.
"""

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

try:
    text_type = unicode
except NameError:
    text_type = str


def to_text(value):
    if isinstance(value, text_type):
        return value
    if sys.version_info[0] == 2 and isinstance(value, str):
        return value.decode('utf-8', 'replace')
    return text_type(value)


def print_message(message):
    if sys.version_info[0] == 2 and isinstance(message, text_type):
        sys.stdout.write(message.encode('utf-8') + '\n')
    else:
        sys.stdout.write(message + '\n')

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
        print_message(u'[PASS] {0}: {1}'.format(to_text(category), to_text(message)))

    def warn(self, category, message):
        self.warns += 1
        print_message(u'[WARN] {0}: {1}'.format(to_text(category), to_text(message)))

    def fail(self, category, message):
        self.fails += 1
        print_message(u'[FAIL] {0}: {1}'.format(to_text(category), to_text(message)))

    def info(self, category, message):
        print_message(u'[INFO] {0}: {1}'.format(to_text(category), to_text(message)))


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


def make_private_temp_directory(prefix='ad_killer_diag_', directory='/tmp'):
    for _ in range(1000):
        random_bytes = os.urandom(8)
        token = ''.join(
            '{0:02x}'.format(value if isinstance(value, int) else ord(value))
            for value in random_bytes
        )
        path = os.path.join(directory, prefix + token)
        try:
            os.mkdir(path, 0o700)
            return path
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise
    raise OSError(errno.EEXIST, 'could not create private temporary directory')


def exact_process_path(pid, proc_root='/proc'):
    try:
        path = os.readlink(os.path.join(proc_root, str(pid), 'exe'))
    except OSError:
        return None
    except AttributeError:
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
        diag.fail('Command', 'systemctl not found; ad_killer cannot stop service units')


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
    except Exception as error:
        diag.fail('Shelf Config', 'Failed to parse JSON in {0}: {1}'.format(OVERRIDE_SOURCE, error))
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
            for shelf in shelves:
                shelf_id = shelf.get('shelfId') if isinstance(shelf, dict) else None
                if not isinstance(shelf_id, string_types) or not shelf_id:
                    diag.fail('Shelf Config', 'Invalid shelfId: {0!r}'.format(shelf_id))
                    return
                found_shelf_ids.append(shelf_id)
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
            u'{0} custom/future shelves will be preserved: {1}'.format(
                len(unknown_shelves), u', '.join(sorted(unknown_shelves))
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


def check_bind_mount(diag):
    probe_dir = None
    src_test = None
    dst_test = None
    mounted = False
    try:
        probe_dir = make_private_temp_directory()
        src_test = os.path.join(probe_dir, 'source')
        dst_test = os.path.join(probe_dir, 'destination')
        for path, contents in ((src_test, 'src\n'), (dst_test, 'dst\n')):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as probe_file:
                probe_file.write(contents)
        code, _ = run_cmd(['mount', '--bind', src_test, dst_test])
        if code == 0:
            mounted = True
            diag.ok('Bind Mount', 'Kernel bind mounts function correctly')
        else:
            diag.warn('Bind Mount', 'mount --bind returned exit code {0}'.format(code))
    except (IOError, OSError) as error:
        diag.warn('Bind Mount', 'Bind mount probe failed: {0}'.format(error))
    finally:
        if mounted:
            code, _ = run_cmd(['umount', dst_test])
            if code != 0:
                diag.fail('Bind Mount', 'Could not unmount bind-mount probe (exit code {0})'.format(code))
        for path in (src_test, dst_test):
            if path is not None:
                try:
                    os.unlink(path)
                except OSError as error:
                    if error.errno != errno.ENOENT:
                        diag.fail('Bind Mount', 'Could not remove probe file {0}: {1}'.format(path, error))
        if probe_dir is not None:
            try:
                os.rmdir(probe_dir)
            except OSError as error:
                if error.errno != errno.ENOENT:
                    diag.fail('Bind Mount', 'Could not remove probe directory {0}: {1}'.format(probe_dir, error))


def check_filesystem_and_mounts(diag, probe_bind_mount=False):
    if probe_bind_mount:
        check_bind_mount(diag)
    else:
        diag.info('Filesystem', 'Non-invasive checks only; use --probe-bind-mount to test bind mounts')

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


def parse_args(args=None):
    if args is None:
        args = sys.argv[1:]
    if not args:
        return False
    if args == ['--probe-bind-mount']:
        return True
    if args == ['-h'] or args == ['--help']:
        print('usage: diagnose.py [--probe-bind-mount]')
        sys.exit(0)
    sys.stderr.write('diagnose.py: error: unrecognized argument\n')
    sys.exit(2)


def main(args=None):
    probe_bind_mount = parse_args(args)
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
    check_filesystem_and_mounts(diag, probe_bind_mount)
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
