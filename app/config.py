"""Settings, stored as JSON in the data volume and edited through the web UI."""
import copy
import json
import os

DATA = os.environ.get('FRAME_PLAN_DATA', '/data')
CONFIG_FILE = os.path.join(DATA, 'config.json')

DEFAULTS = {
    'tv_ip': '',
    'timezone': os.environ.get('TZ') or 'UTC',
    'ui_lang': 'en',
    'plan_lang': 'en',
    # role "events" shows entries as appointments, "todos" puts every entry on the to-do list
    'calendars': [],
    'weather': {'enabled': True, 'name': '', 'lat': None, 'lon': None},
    'holidays': {'country': '', 'subdiv': ''},
    'schedule': {'enabled': True, 'start': '07:00', 'end': '10:00', 'days': [0, 1, 2, 3, 4, 5, 6]},
    'dim_past_events': True,
    'background': 'artwork',  # "artwork": the picture the plan replaces, "default": bundled background
    # Apple TV asleep -> Frame to Art Mode (see appletv.py); credentials live in pyatv.json
    # night_off: inside this window the Frame is switched off completely instead of Art Mode
    # cec: the Apple TV still controls the Frame over HDMI-CEC ("Control TVs and Receivers" on)
    'appletv': {'enabled': False, 'id': '', 'name': '', 'address': '', 'cec': False,
                'night_off': {'enabled': False, 'start': '23:00', 'end': '06:00'}},
}

COLORS = ['green', 'blue', 'orange', 'red', 'grey']


def load():
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            stored = json.load(f)
        for key, value in stored.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg


def save(cfg):
    os.makedirs(DATA, exist_ok=True)
    tmp = CONFIG_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(cfg, f, indent=1, ensure_ascii=False)
    os.chmod(tmp, 0o600)  # calendar links are secrets
    os.replace(tmp, CONFIG_FILE)
