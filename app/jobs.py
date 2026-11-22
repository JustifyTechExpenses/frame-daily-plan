"""The actions: preview, show the plan, bring the artwork back, and the daily scheduler."""
import collections
import datetime
import json
import os
import threading
import time
import traceback
import zoneinfo

import config
import plan_data
import render
import tv

STATE_FILE = os.path.join(config.DATA, 'state.json')
LOG_FILE = os.path.join(config.DATA, 'frame-daily-plan.log')
PREVIEW = os.path.join(config.DATA, 'preview.jpg')
LAST_IMAGE = os.path.join(config.DATA, 'last.jpg')
ART_CACHE = os.path.join(config.DATA, 'art')
RETRY_AFTER = 2 * 60  # a sleeping TV (e.g. before anyone is in the room) is retried every 2 minutes

lock = threading.Lock()  # one TV conversation at a time (scheduler and buttons)
recent = collections.deque(maxlen=200)


def log(msg):
    line = f'{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}'
    print(line, flush=True)
    recent.append(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
        if os.path.getsize(LOG_FILE) > 1_000_000:
            os.replace(LOG_FILE, LOG_FILE + '.1')
    except OSError:
        pass


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'plan_ids': [], 'restore_id': None, 'shown_on': None, 'restore_due': False}


def save_state(state):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def now_local(cfg):
    return datetime.datetime.now(zoneinfo.ZoneInfo(cfg['timezone']))


def require_tv(cfg):
    if not cfg.get('tv_ip'):
        raise RuntimeError('No TV IP address set')


def cached_artwork(cfg, state):
    if cfg.get('background') != 'artwork' or not state.get('restore_id'):
        return None
    path = os.path.join(ART_CACHE, f"{state['restore_id']}.jpg")
    return path if os.path.exists(path) else None


def artwork_background(cfg, a, content_id):
    """The plan sits on the artwork it replaces, so the wall keeps its colours."""
    if cfg.get('background') != 'artwork' or not content_id:
        return None
    path = os.path.join(ART_CACHE, f'{content_id}.jpg')
    try:
        data = tv.get_thumbnail(a, content_id)  # a small thumbnail is plenty for a blurred background
        os.makedirs(ART_CACHE, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
    except Exception as e:  # noqa: BLE001
        log(f'No thumbnail for {content_id} ({e}), using the default background')
    return path if os.path.exists(path) else None


def preview(cfg):
    """Render without touching the TV (uses the last known artwork as background)."""
    data = plan_data.build(cfg, now_local(cfg))
    render.render(data, PREVIEW, cached_artwork(cfg, load_state()))
    return data


def delete_plans(a, state, keep=None):
    old = [i for i in state['plan_ids'] if i != keep]
    if old:
        try:
            a.delete_list(old)
            log(f'Deleted old plans: {", ".join(old)}')
            old = []
        except Exception as e:  # noqa: BLE001  retried next time
            log(f'Could not delete old plans ({e}), will retry')
    state['plan_ids'] = old + ([keep] if keep else [])


def put_temporary(cfg, make_image, what):
    """Upload something temporary (plan, demo, message), show it, and tidy up.

    make_image(a, state) writes LAST_IMAGE. Shown inside the schedule window it is removed
    when the window ends; shown outside it, it stays until "Back to artwork".
    """
    require_tv(cfg)
    with lock:
        state = load_state()
        a = tv.connect(cfg['tv_ip'])
        current = (a.get_current() or {}).get('content_id')
        if current and current not in state['plan_ids']:
            state['restore_id'] = current  # the family's normal artwork
        notes = make_image(a, state)
        with open(LAST_IMAGE, 'rb') as f:
            cid = tv.send_image(a, f.read())
        state['plan_ids'].append(cid)
        state['restore_due'] = in_window(cfg, now_local(cfg))
        save_state(state)  # record the upload before anything else can fail
        show = a.get_artmode() == 'on'  # never interrupt a running TV programme
        a.select_image(cid, show=show)
        delete_plans(a, state, keep=cid)
        save_state(state)
        log(f'{what} {cid} ' + ('shown' if show else 'queued (appears when Art Mode comes on)')
            + (f' · notes: {"; ".join(notes)}' if notes else ''))
        return state


def show_plan(cfg):
    def make(a, state):
        data = plan_data.build(cfg, now_local(cfg))
        render.render(data, LAST_IMAGE, artwork_background(cfg, a, state.get('restore_id')))
        return data['errors']
    state = put_temporary(cfg, make, 'Plan')
    if in_window(cfg, now_local(cfg)):  # a manual plan at 06:30 must not stop the 07:00 one
        state['shown_on'] = now_local(cfg).date().isoformat()
        save_state(state)


def show_demo(cfg, lang='en', on_tv=True):
    """Made-up plan (no real data) for sharing; shown like a plan."""
    data = plan_data.demo(cfg, now_local(cfg), lang)
    if not on_tv:
        render.render(data, PREVIEW, cached_artwork(cfg, load_state()))
        return

    def make(a, state):
        render.render(data, LAST_IMAGE, artwork_background(cfg, a, state.get('restore_id')))
    put_temporary(cfg, make, f'Demo plan ({lang})')


def show_text(cfg, text, theme='dark'):
    """A message on a plain background, temporary like a plan."""
    if not text.strip():
        raise RuntimeError('No text entered')

    def make(a, state):
        render.render_text(text, LAST_IMAGE, theme)
    put_temporary(cfg, make, f'Message “{text.strip()[:40]}”')


def restore(cfg):
    require_tv(cfg)
    with lock:
        state = load_state()
        a = tv.connect(cfg['tv_ip'])
        if state.get('restore_id'):
            a.select_image(state['restore_id'], show=a.get_artmode() == 'on')
            log(f'Artwork {state["restore_id"]} restored')
        delete_plans(a, state)
        state['restore_due'] = False
        save_state(state)


def upload_artwork(cfg, src_path, mode='crop'):
    """Put the family's own picture on the Frame. It is kept (never auto-deleted)
    and becomes the artwork the plan returns to."""
    require_tv(cfg)
    fitted = os.path.join(config.DATA, 'upload-fitted.jpg')
    render.fit_artwork(src_path, fitted, mode)
    with lock:
        state = load_state()
        a = tv.connect(cfg['tv_ip'])
        with open(fitted, 'rb') as f:
            cid = tv.send_image(a, f.read())
        show = a.get_artmode() == 'on'
        a.select_image(cid, show=show)
        state['restore_id'] = cid
        state.setdefault('artworks', []).append(cid)
        save_state(state)
        os.makedirs(ART_CACHE, exist_ok=True)
        os.replace(fitted, os.path.join(ART_CACHE, f'{cid}.jpg'))  # also the plan background from now on
        log(f'Picture {cid} uploaded and ' + ('shown' if show else 'queued') + ' (kept permanently)')
        return cid


def in_window(cfg, now):
    sch = cfg['schedule']
    return (bool(sch.get('enabled')) and now.weekday() in sch.get('days', [])
            and sch['start'] <= now.strftime('%H:%M') < sch['end'])


def _due(cfg, state, now):
    if not cfg.get('tv_ip'):
        return None  # not set up yet
    if in_window(cfg, now):
        return 'show' if state.get('shown_on') != now.date().isoformat() else None
    # After the window (or on a later day if the TV was unreachable): bring the artwork back once.
    return 'restore' if state.get('restore_due') else None


def _wake_for_plan(cfg):
    """If the Apple TV feature switches the Frame off at night, switch it back on for the plan."""
    if not cfg.get('appletv', {}).get('night_off', {}).get('enabled'):
        return
    import appletv  # here, because appletv imports this module
    appletv.wake_to_art(cfg)


def scheduler():
    retry_at = 0.0
    while True:
        try:
            cfg = config.load()
            action = _due(cfg, load_state(), now_local(cfg))
            if action and time.time() >= retry_at:
                if action == 'show':
                    _wake_for_plan(cfg)
                (show_plan if action == 'show' else restore)(cfg)
        except Exception as e:  # noqa: BLE001  e.g. TV switched off: try again in a few minutes
            log(f'Error ({type(e).__name__}: {e}), retrying in {RETRY_AFTER // 60} minutes')
            traceback.print_exc()
            retry_at = time.time() + RETRY_AFTER
        time.sleep(30)


def start_scheduler():
    threading.Thread(target=scheduler, name='scheduler', daemon=True).start()
