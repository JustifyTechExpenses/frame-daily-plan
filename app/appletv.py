"""Apple TV remote → Art Mode.

An Apple TV going to sleep switches the Frame off completely (HDMI-CEC "standby"), never
to Art Mode. This module watches the Apple TV over the network (Companion protocol, via
pyatv) and makes the Frame follow it:

  Apple TV goes to sleep  ->  Frame to Art Mode
  Apple TV wakes up       ->  Frame to the TV picture

Two ways, chosen in the web UI ("cec"):

- "Control TVs and Receivers" off on the Apple TV: the app presses the Frame's power key,
  which toggles between TV picture and Art Mode, after asking the TV which mode it is in.
- On (recommended with a games console on another input): the Apple TV switches the Frame
  on and to its own input by itself; going to sleep it switches it off, and the app brings
  it back with Wake-on-LAN, straight into Art Mode.

Optionally, inside a night window the Frame is switched off completely instead, and the
daily plan's scheduler wakes it into Art Mode at the plan's start time (wake_to_art).

All of this runs in one background thread with its own asyncio loop; the web UI talks to
it through the functions at the bottom.
"""
import asyncio
import os
import socket
import threading
import time

import pyatv
from pyatv.const import PowerState, Protocol
from pyatv.interface import DeviceListener, PowerListener
from pyatv.storage.file_storage import FileStorage

import config
import jobs
import tv

STORAGE_FILE = os.path.join(config.DATA, 'pyatv.json')  # Apple TV pairing credentials
MAC_FILE = os.path.join(config.DATA, 'tv-mac.txt')     # for Wake-on-LAN, read from the TV
CLIENT_NAME = 'Frame Daily Plan'
SLEEP_DELAY = 3     # seconds the Apple TV must stay asleep before the Frame follows
WAKE_TIMEOUT = 30   # seconds to wait for a Frame woken by Wake-on-LAN
CEC_WAIT = 15       # seconds HDMI-CEC may take to switch the Frame on
CEC_OFF_WAIT = 45   # a 2017 Frame can stay on the network for a while after CEC switched it off

_loop = None
_storage = None
_pairing = None     # the pairing handler between "start" and "PIN"
_watcher = None


# ---------------------------------------------------------------------------
# The Frame side (blocking; runs in a worker thread)
# ---------------------------------------------------------------------------
def reachable(ip):
    try:
        with socket.create_connection((ip, 8001), timeout=2):
            return True
    except OSError:
        return False


def _remember_mac(ip):
    if not ip or os.path.exists(MAC_FILE):
        return
    try:
        mac = tv._tv(ip, 5).rest_device_info()['device'].get('wifiMac', '')
    except Exception:  # noqa: BLE001
        return
    if mac:
        with open(MAC_FILE, 'w') as f:
            f.write(mac)


def wake_on_lan():
    if not os.path.exists(MAC_FILE):
        jobs.log('Wake-on-LAN not possible yet: the MAC address is learned once the Frame has been on for a moment')
        return False
    with open(MAC_FILE) as f:
        mac = bytes.fromhex(f.read().strip().replace(':', '').replace('-', ''))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(b'\xff' * 6 + mac * 16, ('255.255.255.255', 9))
    return True


def art_mode(ip):
    """'on', 'off', or None if the TV does not say."""
    try:
        return tv._tv(ip, 10).art().get_artmode()
    except Exception:  # noqa: BLE001
        return None


def press_power(ip):
    remote = tv._tv(ip, 10)
    try:
        remote.send_key('KEY_POWER')  # a short press: TV picture <-> Art Mode
    finally:
        remote.close()


def power_off(ip):
    remote = tv._tv(ip, 10)
    try:
        remote.hold_key('KEY_POWER', 3)  # a long press switches the Frame off completely
    finally:
        remote.close()


def in_night(cfg, now=None):
    """Inside the "switch off completely" window (it may run past midnight)."""
    night = cfg.get('appletv', {}).get('night_off', {})
    if not night.get('enabled'):
        return False
    now = (now or jobs.now_local(cfg)).strftime('%H:%M')
    start, end = night.get('start', '23:00'), night.get('end', '06:00')
    return start <= now < end if start < end else (now >= start or now < end)


def _wait_until_off(ip, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not reachable(ip):
            return True
        time.sleep(1)
    return False


def _wake_up(ip):
    """Wake-on-LAN, then press until Art Mode. Caller holds jobs.lock."""
    if not wake_on_lan():
        return False
    deadline = time.time() + WAKE_TIMEOUT
    while time.time() < deadline and not reachable(ip):
        time.sleep(1)
    if not reachable(ip):
        jobs.log('Wake-on-LAN sent, but the Frame did not wake up')
        return False
    # Woken this way, the Frame is on the network but its screen is still dark, and the first
    # key presses can be ignored while it starts up. Dark and TV picture look the same from
    # outside, so press until it reports Art Mode, the one state it can be asked about.
    for _ in range(4):
        time.sleep(5)
        if art_mode(ip) == 'on':
            return True
        press_power(ip)
    jobs.log('Frame woken up, but it did not reach Art Mode')
    return False


def wake_to_art(cfg):
    """Switch a Frame that is completely off on, straight into Art Mode (for the morning plan)."""
    ip = cfg.get('tv_ip')
    if not ip or reachable(ip):
        return False
    with jobs.lock:
        ok = _wake_up(ip)
    if ok:
        jobs.log('Frame woken up into Art Mode for the plan')
    return ok


def frame_follow_cec(cfg, want_art):
    """The Apple TV controls the Frame over HDMI-CEC ("Control TVs and Receivers" on).

    Waking up, it switches the Frame on and to its own input by itself, which the app cannot
    do on a 2017 Frame (direct input keys are ignored over the network). Going to sleep, it
    switches the Frame off completely; the app then switches it back on into Art Mode.
    """
    ip = cfg['tv_ip']
    if want_art:
        _remember_mac(ip)  # needed to switch it back on; still reachable for a moment
        if not _wait_until_off(ip, CEC_OFF_WAIT):
            try:  # the Frame stayed on (CEC off after all?): the usual way
                frame_follow(dict(cfg, appletv=dict(cfg.get('appletv', {}), cec=False)), True)
                return
            except Exception:  # noqa: BLE001
                if reachable(ip):
                    raise
                # it only now dropped off the network: switched off by CEC after all
        if in_night(cfg):
            jobs.log('Apple TV asleep at night → Frame stays off')
            return
        with jobs.lock:
            ok = _wake_up(ip)
        jobs.log('Apple TV asleep → Frame switched off by the Apple TV, back on in Art Mode' if ok
                 else 'Apple TV asleep → Frame switched off by the Apple TV, could not bring Art Mode back')
        return
    time.sleep(CEC_WAIT)  # give HDMI-CEC time to switch the Frame on and to the Apple TV
    if not reachable(ip):
        jobs.log('Apple TV awake, but the Frame did not switch on')
        return
    with jobs.lock:
        if art_mode(ip) == 'on':  # CEC did not leave Art Mode
            press_power(ip)
            jobs.log('Apple TV awake → Frame to the TV picture')


def frame_follow(cfg, want_art):
    """Put the Frame into Art Mode (want_art) or into the TV picture."""
    ip = cfg.get('tv_ip')
    if not ip:
        return
    if cfg.get('appletv', {}).get('cec'):
        frame_follow_cec(cfg, want_art)
        return
    if want_art and in_night(cfg) and reachable(ip):
        with jobs.lock:
            _remember_mac(ip)
            power_off(ip)
        jobs.log('Apple TV asleep at night → Frame switched off completely')
        return
    if not reachable(ip):
        if want_art:
            jobs.log('Apple TV asleep, Frame is already off')
            return
        if not os.path.exists(MAC_FILE):
            jobs.log('Apple TV awake, but the Frame is off and its MAC address is not known yet')
            return
        with jobs.lock:
            if not _wake_up(ip):
                return
            press_power(ip)  # from Art Mode to the TV picture
        jobs.log('Apple TV awake → Frame switched on (Wake-on-LAN)')
        return
    with jobs.lock:
        _remember_mac(ip)
        # A 2017 Frame (Art API 1.07) answers reliably only in Art Mode; showing the TV
        # picture it answers "off" or not at all. No answer therefore means "off".
        mode = art_mode(ip) or 'off'
        if want_art:
            if mode == 'on':
                return  # already there, e.g. someone used the Samsung remote
            press_power(ip)
            jobs.log('Apple TV asleep → Frame to Art Mode')
            return
        if mode == 'on':
            press_power(ip)
            jobs.log('Apple TV awake → Frame to the TV picture')


# ---------------------------------------------------------------------------
# The Apple TV side (asyncio, in the background thread)
# ---------------------------------------------------------------------------
async def _scan(identifier=None, address=None):
    def keep(found):
        found = [c for c in found if c.get_service(Protocol.Companion)]
        if identifier:
            return [c for c in found if identifier in c.all_identifiers]
        # Macs and iPads answer on Companion too; only Apple TVs are of interest.
        return [c for c in found if 'AppleTV' in (c.device_info.raw_model or '')]

    found = []
    if address:  # asking the address directly does not work on every network, hence the fallback
        found = keep(await pyatv.scan(_loop, hosts=[address], timeout=5, storage=_storage,
                                      protocol=Protocol.Companion))
    if not found:
        found = keep(await pyatv.scan(_loop, timeout=8, storage=_storage, protocol=Protocol.Companion))
    return found


class Watcher(PowerListener, DeviceListener):
    def __init__(self):
        self.atv = None
        self.lost = asyncio.Event()
        self.pending = None
        self.handled = None   # the last state the Frame was made to follow
        self.name = ''

    def powerstate_update(self, old_state, new_state):
        if new_state not in (PowerState.On, PowerState.Off):
            return
        if self.pending:
            self.pending.cancel()
        self.pending = _loop.create_task(self._follow(new_state))

    def connection_lost(self, exception):
        jobs.log(f'Apple TV connection lost ({exception})')
        self.lost.set()

    def connection_closed(self):
        self.lost.set()

    async def _follow(self, state):
        # Waking up and falling asleep can flicker; only act on a state that stays.
        await asyncio.sleep(SLEEP_DELAY if state == PowerState.Off else 0.5)
        if not self.atv or self.atv.power.power_state != state or state == self.handled:
            return
        self.handled = state
        try:
            await asyncio.to_thread(frame_follow, config.load(), state == PowerState.Off)
        except Exception as e:  # noqa: BLE001
            jobs.log(f'Could not switch the Frame ({type(e).__name__}: {e})')

    async def run(self):
        while True:
            atv_cfg = config.load()['appletv']
            if not (atv_cfg.get('enabled') and atv_cfg.get('id')):
                await asyncio.sleep(10)
                continue
            try:
                await self._session(atv_cfg)
            except Exception as e:  # noqa: BLE001  e.g. Apple TV unplugged: try again
                jobs.log(f'Apple TV not reachable ({type(e).__name__}: {e}), retrying in a minute')
                await asyncio.sleep(60)

    async def _session(self, atv_cfg):
        found = await _scan(atv_cfg['id'], atv_cfg.get('address'))
        if not found:
            raise RuntimeError('not found on the network')
        conf = found[0]
        self.atv = await pyatv.connect(conf, _loop, storage=_storage)
        self.lost.clear()
        self.name = conf.name
        try:
            self.atv.listener = self
            self.atv.power.listener = self
            await asyncio.sleep(2)
            self.handled = self.atv.power.power_state  # don't act on the state found at start
            jobs.log(f'Watching Apple TV “{conf.name}” ({conf.address}), it is {self.handled.name.lower()}')
            while not self.lost.is_set():
                try:
                    await asyncio.wait_for(self.lost.wait(), timeout=15)
                except asyncio.TimeoutError:
                    now = config.load()['appletv']
                    if not now.get('enabled') or now.get('id') != atv_cfg['id']:
                        jobs.log('Apple TV watching stopped')
                        return
                    if not os.path.exists(MAC_FILE):  # learn it while the Frame is on, for Wake-on-LAN later
                        await asyncio.to_thread(_remember_mac, config.load().get('tv_ip', ''))
                    state = self.atv.power.power_state  # in case a pushed update was missed
                    if state in (PowerState.On, PowerState.Off) and state != self.handled:
                        self.powerstate_update(self.handled, state)
        finally:
            self.atv.close()
            self.atv = None


def _thread():
    global _loop, _storage, _watcher
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _storage = FileStorage(STORAGE_FILE, _loop)
    _loop.run_until_complete(_storage.load())
    _watcher = Watcher()
    _loop.run_until_complete(_watcher.run())


def start():
    threading.Thread(target=_thread, name='appletv', daemon=True).start()


# ---------------------------------------------------------------------------
# Called from the web UI (Flask threads)
# ---------------------------------------------------------------------------
def _call(coro, timeout=60):
    for _ in range(50):  # the background thread may still be starting
        if _loop and _storage:
            break
        time.sleep(0.1)
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout)


def discover():
    async def run():
        return [{'id': c.identifier, 'name': c.name, 'address': str(c.address)} for c in await _scan()]
    return _call(run())


def pair_start(identifier, address=''):
    """Starts pairing; the Apple TV then shows a PIN on the TV."""
    async def run():
        global _pairing
        if _pairing:
            await _pairing.close()
        found = await _scan(identifier, address)
        if not found:
            raise RuntimeError('Apple TV not found')
        _pairing = await pyatv.pair(found[0], Protocol.Companion, _loop, storage=_storage, name=CLIENT_NAME)
        await _pairing.begin()
        return found[0].name
    return _call(run())


def pair_finish(pin):
    async def run():
        global _pairing
        if not _pairing:
            raise RuntimeError('No pairing in progress')
        try:
            _pairing.pin(pin)
            await _pairing.finish()
            ok = _pairing.has_paired
        finally:
            await _pairing.close()
            _pairing = None
        if ok:
            await _storage.save()
        return ok
    return _call(run())


def pairing_in_progress():
    return _pairing is not None


def status():
    w = _watcher
    if not w or not w.atv:
        return {'connected': False}
    return {'connected': True, 'name': w.name, 'awake': w.atv.power.power_state == PowerState.On}
