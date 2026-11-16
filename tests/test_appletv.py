import asyncio

from pyatv.const import PowerState

import appletv
import config
import web

CFG = {'tv_ip': '192.168.1.50'}


def fake_frame(monkeypatch, mode, up=True):
    pressed = []
    monkeypatch.setattr(appletv, 'reachable', lambda ip: up)
    monkeypatch.setattr(appletv, '_remember_mac', lambda ip: None)
    monkeypatch.setattr(appletv, 'art_mode', lambda ip: mode)
    monkeypatch.setattr(appletv, 'press_power', lambda ip: pressed.append(ip))
    return pressed


def test_sleep_sends_frame_to_art_mode(monkeypatch):
    pressed = fake_frame(monkeypatch, 'off')
    appletv.frame_follow(CFG, want_art=True)
    assert pressed == ['192.168.1.50']


def test_no_key_when_frame_is_already_there(monkeypatch):
    pressed = fake_frame(monkeypatch, 'on')
    appletv.frame_follow(CFG, want_art=True)          # already in Art Mode
    fake_frame(monkeypatch, 'off')
    appletv.frame_follow(CFG, want_art=False)         # already showing the TV picture
    assert pressed == []


def test_wake_leaves_art_mode(monkeypatch):
    pressed = fake_frame(monkeypatch, 'on')
    appletv.frame_follow(CFG, want_art=False)
    assert pressed == ['192.168.1.50']


def test_no_art_answer_means_tv_picture(monkeypatch):
    pressed = fake_frame(monkeypatch, None)            # 2017 Frame showing the TV picture
    appletv.frame_follow(CFG, want_art=False)
    assert pressed == []
    appletv.frame_follow(CFG, want_art=True)
    assert pressed == ['192.168.1.50']


def test_frame_off_is_left_alone_when_apple_tv_sleeps(monkeypatch):
    pressed = fake_frame(monkeypatch, None, up=False)
    appletv.frame_follow(CFG, want_art=True)
    assert pressed == []


def test_flicker_does_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(appletv, 'frame_follow', lambda cfg, want_art: calls.append(want_art))
    monkeypatch.setattr(appletv, 'SLEEP_DELAY', 0.1)

    class Power:
        power_state = PowerState.On

    class Atv:
        power = Power()

    async def run():
        appletv._loop = asyncio.get_running_loop()
        w = appletv.Watcher()
        w.atv, w.handled = Atv(), PowerState.On
        Power.power_state = PowerState.Off            # asleep only for a moment
        w.powerstate_update(PowerState.On, PowerState.Off)
        Power.power_state = PowerState.On
        w.powerstate_update(PowerState.Off, PowerState.On)
        await asyncio.sleep(0.8)
        assert calls == []
        Power.power_state = PowerState.Off            # asleep for good
        w.powerstate_update(PowerState.On, PowerState.Off)
        await asyncio.sleep(0.3)
        assert calls == [True]
    asyncio.run(run())


def test_card_and_toggle():
    c = web.app.test_client()
    c.get('/lang/en')
    html = c.get('/').get_data(as_text=True)
    assert 'Sleep After' in html and 'Search for Apple TV' in html
    c.post('/appletv/enabled', data={'enabled': 'on'})
    assert config.load()['appletv']['enabled'] is False  # nothing paired yet
    cfg = config.load()
    cfg['appletv'].update({'id': 'AA:BB', 'name': 'Wohnzimmer'})
    config.save(cfg)
    c.post('/appletv/enabled', data={'enabled': 'on'})
    assert config.load()['appletv']['enabled'] is True
    html = c.get('/').get_data(as_text=True)
    assert 'Art Mode when the Apple TV sleeps' in html and 'Control TVs and Receivers' in html


def night_cfg(start='23:00', end='06:00'):
    return {'tv_ip': '192.168.1.50', 'timezone': 'Europe/Berlin',
            'appletv': {'night_off': {'enabled': True, 'start': start, 'end': end}}}


def test_night_window_runs_past_midnight():
    import datetime
    at = lambda h, m=0: datetime.datetime(2026, 11, 16, h, m)
    cfg = night_cfg()
    assert appletv.in_night(cfg, at(23, 30)) and appletv.in_night(cfg, at(2)) and appletv.in_night(cfg, at(5, 59))
    assert not appletv.in_night(cfg, at(6)) and not appletv.in_night(cfg, at(22, 59))
    assert appletv.in_night(night_cfg('01:00', '05:00'), at(3)) and not appletv.in_night(night_cfg('01:00', '05:00'), at(6))
    cfg['appletv']['night_off']['enabled'] = False
    assert not appletv.in_night(cfg, at(2))


def test_sleep_at_night_switches_off(monkeypatch):
    pressed = fake_frame(monkeypatch, 'off')
    off = []
    monkeypatch.setattr(appletv, 'power_off', lambda ip: off.append(ip))
    monkeypatch.setattr(appletv, 'in_night', lambda cfg, now=None: True)
    appletv.frame_follow(night_cfg(), want_art=True)
    assert off == ['192.168.1.50'] and pressed == []
    appletv.frame_follow(night_cfg(), want_art=False)  # waking up at night: back to the TV picture as usual
    assert off == ['192.168.1.50']


def test_morning_wake_only_with_night_off(monkeypatch):
    import jobs
    woken = []
    monkeypatch.setattr(appletv, 'wake_to_art', lambda cfg: woken.append(cfg['tv_ip']))
    jobs._wake_for_plan({'tv_ip': 'a', 'appletv': {'night_off': {'enabled': False}}})
    jobs._wake_for_plan({'tv_ip': 'b', 'appletv': {'night_off': {'enabled': True}}})
    assert woken == ['b']


def test_night_settings_saved():
    c = web.app.test_client()
    cfg = config.load()
    cfg['appletv'].update({'id': 'AA:BB', 'name': 'Wohnzimmer'})
    config.save(cfg)
    c.post('/appletv/enabled', data={'enabled': 'on', 'night_enabled': 'on', 'night_start': '22:30', 'night_end': '06:30'})
    night = config.load()['appletv']['night_off']
    assert night == {'enabled': True, 'start': '22:30', 'end': '06:30'}
    c.post('/appletv/enabled', data={'enabled': 'on', 'night_enabled': 'on', 'night_start': '05:00', 'night_end': '05:00'})
    assert config.load()['appletv']['night_off']['start'] == '22:30'  # same start and end is refused


CEC = dict(CFG, timezone='Europe/Berlin', appletv={'cec': True})


def cec_frame(monkeypatch, switched_off, mode='off'):
    pressed, woken = fake_frame(monkeypatch, mode), []
    monkeypatch.setattr(appletv, '_wait_until_off', lambda ip, s: switched_off)
    monkeypatch.setattr(appletv, '_wake_up', lambda ip: woken.append(ip) or True)
    monkeypatch.setattr(appletv.time, 'sleep', lambda s: None)
    return pressed, woken


def test_cec_sleep_brings_art_mode_back(monkeypatch):
    pressed, woken = cec_frame(monkeypatch, switched_off=True)
    appletv.frame_follow(CEC, want_art=True)            # the Apple TV switched the Frame off
    assert woken == ['192.168.1.50'] and pressed == []


def test_cec_sleep_at_night_leaves_it_off(monkeypatch):
    pressed, woken = cec_frame(monkeypatch, switched_off=True)
    monkeypatch.setattr(appletv, 'in_night', lambda cfg, now=None: True)
    appletv.frame_follow(CEC, want_art=True)
    assert woken == [] and pressed == []


def test_cec_frame_stayed_on_uses_power_key(monkeypatch):
    pressed, woken = cec_frame(monkeypatch, switched_off=False)
    appletv.frame_follow(CEC, want_art=True)
    assert woken == [] and pressed == ['192.168.1.50']


def test_cec_wake_leaves_input_to_cec(monkeypatch):
    pressed, _ = cec_frame(monkeypatch, switched_off=False, mode='off')
    appletv.frame_follow(CEC, want_art=False)           # CEC already showed the Apple TV
    assert pressed == []
    pressed, _ = cec_frame(monkeypatch, switched_off=False, mode='on')
    appletv.frame_follow(CEC, want_art=False)           # CEC did not leave Art Mode
    assert pressed == ['192.168.1.50']


def test_cec_setting_saved():
    c = web.app.test_client()
    cfg = config.load()
    cfg['appletv'].update({'id': 'AA:BB', 'name': 'Wohnzimmer'})
    config.save(cfg)
    c.post('/appletv/enabled', data={'enabled': 'on', 'cec': 'on'})
    assert config.load()['appletv']['cec'] is True
    c.post('/appletv/enabled', data={'enabled': 'on'})
    assert config.load()['appletv']['cec'] is False


def test_art_channel_skips_client_connect_notice(monkeypatch):
    import json
    import tv
    from samsungtvws import art
    from samsungtvws.connection import SamsungTVWSConnection

    class Sock:
        msgs = [json.dumps({'event': 'ms.channel.clientConnect', 'data': {'attributes': {'token': '1'}}}),
                json.dumps({'event': 'ms.channel.ready'})]
        def recv(self): return self.msgs.pop(0)
        def close(self): pass

    monkeypatch.setattr(SamsungTVWSConnection, 'open', lambda self: setattr(self, 'connection', Sock()))
    a = art.SamsungTVArt('192.168.1.50', port=8002)
    assert a.open() is a.connection and tv.NAME


def test_mac_learned_while_frame_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(appletv, 'MAC_FILE', str(tmp_path / 'tv-mac.txt'))
    assert appletv.wake_on_lan() is False                 # nothing learned yet, and the log says so

    class Rest:
        def rest_device_info(self): return {'device': {'wifiMac': 'aa:bb:cc:dd:ee:ff'}}
    monkeypatch.setattr(appletv.tv, '_tv', lambda ip, t: Rest())
    appletv._remember_mac('192.168.1.50')
    assert (tmp_path / 'tv-mac.txt').read_text() == 'aa:bb:cc:dd:ee:ff'


def test_version_in_footer(monkeypatch):
    monkeypatch.setenv('APP_VERSION', 'f7ab5ca75afc')
    assert 'Version f7ab5ca<' in web.app.test_client().get('/').get_data(as_text=True)


def test_cec_frame_drops_off_while_pressing(monkeypatch):
    pressed, woken = cec_frame(monkeypatch, switched_off=False)
    up = iter([True, False])                            # still there, then gone
    monkeypatch.setattr(appletv, 'reachable', lambda ip: next(up, False))
    def boom(ip): raise RuntimeError('TV unreachable')
    monkeypatch.setattr(appletv, 'press_power', boom)
    appletv.frame_follow(CEC, want_art=True)
    assert woken == ['192.168.1.50']
