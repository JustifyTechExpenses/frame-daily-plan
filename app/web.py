"""Web UI for Frame Daily Plan: setup, status, actions and settings. Runs the scheduler too.

Optional protection: set FRAME_PLAN_PASSWORD; the browser then asks for it (any user name).
"""
import functools
import os
import sys
import threading
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also when started with python -I

from flask import Flask, Response, redirect, render_template, request, send_file, url_for
from waitress import serve

import holidays  # noqa: E402

import appletv  # noqa: E402
import config  # noqa: E402
import i18n  # noqa: E402
import plan_data  # noqa: E402
import jobs  # noqa: E402
import tv  # noqa: E402

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 40 * 1024 * 1024  # phone photos
PASSWORD = os.environ.get('FRAME_PLAN_PASSWORD', '')


def ui():
    return i18n.texts(config.load().get('ui_lang', 'de'))


def protected(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if PASSWORD and not (auth and auth.password == PASSWORD):
            return Response('Password required', 401, {'WWW-Authenticate': 'Basic realm="Frame Daily Plan"'})
        return view(*args, **kwargs)
    return wrapper


@app.get('/')
@protected
def index():
    cfg = config.load()
    lang = cfg.get('ui_lang', 'en')
    setup = {'tv': bool(cfg.get('tv_ip')), 'paired': os.path.exists(os.path.join(config.DATA, 'paired')),
             'place': cfg['weather'].get('lat') is not None, 'calendar': bool(cfg['calendars'])}
    return render_template('index.html', cfg=cfg, state=jobs.load_state(), log=list(jobs.recent)[-40:][::-1],
                           colors=config.COLORS, lang=lang, version=os.environ.get('APP_VERSION', 'dev')[:7], t=i18n.texts(lang), setup=setup,
                           setup_done=all(setup.values()), countries=sorted(holidays.list_supported_countries()),
                           has_preview=os.path.exists(jobs.PREVIEW), message=request.args.get('msg'),
                           atv_pairing=appletv.pairing_in_progress())


@app.get('/discover')
@protected
def discover():
    return {'tvs': tv.discover()}


@app.get('/geocode')
@protected
def geocode():
    return {'results': plan_data.geocode(request.args.get('q', ''), config.load().get('ui_lang', 'en'))}


@app.post('/set-tv')
@protected
def set_tv():
    cfg = config.load()
    cfg['tv_ip'] = request.form.get('ip', '').strip()
    config.save(cfg)
    jobs.log(f"TV set to {cfg['tv_ip']}")
    return redirect(url_for('index', msg=ui()['saved']) + '#setup')


@app.post('/set-place')
@protected
def set_place():
    f = request.form
    cfg = config.load()
    cfg['weather'].update({'name': f.get('label', ''), 'lat': float(f['lat']), 'lon': float(f['lon']), 'enabled': True})
    try:
        zoneinfo.ZoneInfo(f.get('timezone', ''))
        cfg['timezone'] = f['timezone']
    except Exception:  # noqa: BLE001
        pass
    code = f.get('country_code', '').upper()
    if code in holidays.list_supported_countries() and not cfg['holidays'].get('country'):
        cfg['holidays'] = {'country': code, 'subdiv': ''}
    config.save(cfg)
    jobs.log(f"Place set to {cfg['weather']['name']}")
    return redirect(url_for('index', msg=ui()['saved']) + '#setup')


@app.get('/regions/<country>')
@protected
def regions(country):
    return {'regions': sorted(holidays.list_supported_countries().get(country.upper(), []))}


@app.get('/lang/<code>')
@protected
def set_lang(code):
    if code in i18n.TEXT:
        cfg = config.load()
        cfg['ui_lang'] = code
        config.save(cfg)
    return redirect(url_for('index'))


@app.get('/health')
def health():
    return {'ok': True}


@app.get('/status')
@protected
def status():
    cfg = config.load()
    try:
        s = tv.status(cfg['tv_ip'])
        return {'ok': True, **s}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'error': str(e)}


@app.get('/diag')
@protected
def diag():
    return {'steps': tv.diagnose(config.load()['tv_ip'])}


@app.post('/pair')
@protected
def pair():
    # Triggers the TV's "allow this device?" prompt and waits up to two minutes for it.
    steps = tv.diagnose(config.load()['tv_ip'])
    t = ui()
    text = ' · '.join(f"{t['step_' + n]}: {'ok' if r == 'ok' else t['error'] + ' (' + d + ')'}" for n, r, d, _ in steps)
    ok = len(steps) == 3 and all(r == 'ok' for _, r, _, _ in steps)
    if ok:
        open(os.path.join(config.DATA, 'paired'), 'w').close()
    jobs.log(f'Connect: {text}')
    return redirect(url_for('index', msg=f"{t['connected'] if ok else t['not_connected']} – {text}"))


@app.get('/appletv/discover')
@protected
def appletv_discover():
    return {'devices': appletv.discover()}


@app.get('/appletv/status')
@protected
def appletv_status():
    return appletv.status()


@app.post('/appletv/pair-start')
@protected
def appletv_pair_start():
    f = request.form
    t = ui()
    try:
        name = appletv.pair_start(f['id'], f.get('address', ''))
    except Exception as e:  # noqa: BLE001
        jobs.log(f'Apple TV pairing failed: {e}')
        return redirect(url_for('index', msg=f"{t['atv_pair_failed']}: {e}") + '#appletv')
    cfg = config.load()
    cfg['appletv'].update({'id': f['id'], 'name': name, 'address': f.get('address', ''), 'enabled': False})
    config.save(cfg)
    return redirect(url_for('index', msg=t['atv_enter_pin']) + '#appletv')


@app.post('/appletv/pair-finish')
@protected
def appletv_pair_finish():
    t = ui()
    try:
        ok = appletv.pair_finish(request.form.get('pin', '').strip())
    except Exception as e:  # noqa: BLE001
        ok, err = False, str(e)
    else:
        err = ''
    if not ok:
        jobs.log(f'Apple TV pairing failed {err}')
        if 'not connected' in err:  # the Apple TV closes the PIN screen after about 30 seconds
            return redirect(url_for('index', msg=t['atv_too_slow']) + '#appletv')
        return redirect(url_for('index', msg=t['atv_pair_failed'] + (f': {err}' if err else '')) + '#appletv')
    cfg = config.load()
    cfg['appletv']['enabled'] = True
    config.save(cfg)
    jobs.log(f"Apple TV “{cfg['appletv']['name']}” paired")
    return redirect(url_for('index', msg=t['atv_paired']) + '#appletv')


@app.post('/appletv/enabled')
@protected
def appletv_enabled():
    cfg = config.load()
    f = request.form
    cfg['appletv']['enabled'] = 'enabled' in f and bool(cfg['appletv'].get('id'))
    cfg['appletv']['cec'] = 'cec' in f
    night = cfg['appletv'].setdefault('night_off', {})
    night.update({'enabled': 'night_enabled' in f, 'start': f.get('night_start') or night.get('start', '23:00'),
                  'end': f.get('night_end') or night.get('end', '06:00')})
    if night['start'] == night['end']:
        return redirect(url_for('index', msg=ui()['atv_night_same']) + '#appletv')
    config.save(cfg)
    jobs.log('Apple TV → Art Mode ' + ('on' if cfg['appletv']['enabled'] else 'off')
             + (f", off completely {night['start']}–{night['end']}" if night['enabled'] else ''))
    return redirect(url_for('index', msg=ui()['saved']) + '#appletv')


@app.get('/preview.jpg')
@protected
def preview_image():
    return send_file(jobs.PREVIEW, mimetype='image/jpeg', max_age=0)


def run_action(key, fn):
    t = ui()
    try:
        fn()
        return redirect(url_for('index', msg=f"{t[key]}: {t['done']}"))
    except Exception as e:  # noqa: BLE001
        jobs.log(f"{i18n.TEXT['en'][key]} failed: {e}")
        return redirect(url_for('index', msg=f"{t[key]} {t['failed']}: {e}"))


@app.post('/preview')
@protected
def make_preview():
    return run_action('a_preview', lambda: jobs.preview(config.load()))


@app.post('/show')
@protected
def show_now():
    return run_action('a_show', lambda: jobs.show_plan(config.load()))


@app.post('/restore')
@protected
def restore_now():
    return run_action('a_restore', lambda: jobs.restore(config.load()))


@app.post('/upload')
@protected
def upload():
    f = request.files.get('image')
    if not f or not f.filename:
        return redirect(url_for('index', msg=ui()['pick_image']))
    src = os.path.join(config.DATA, 'upload-original')
    f.save(src)
    mode = request.form.get('mode', 'crop')
    try:
        return run_action('a_upload', lambda: jobs.upload_artwork(config.load(), src, mode))
    finally:
        try:
            os.remove(src)
        except OSError:
            pass


@app.post('/demo')
@protected
def demo():
    lang = request.form.get('lang', 'de')
    on_tv = request.form.get('target') == 'tv'
    return run_action('a_demo_tv' if on_tv else 'a_demo_preview', lambda: jobs.show_demo(config.load(), lang, on_tv))


@app.post('/text')
@protected
def text():
    return run_action('a_text', lambda: jobs.show_text(config.load(), request.form.get('text', ''),
                                                               request.form.get('theme', 'dark')))


@app.post('/restart')
@protected
def restart():
    # The container restarts itself (restart: unless-stopped); app/ is mounted, so code
    # copied into the folder takes effect without rebuilding.
    jobs.log('Restart requested')
    threading.Timer(1.0, os._exit, [0]).start()
    return redirect(url_for('index', msg=ui()['restarting']))


@app.post('/settings')
@protected
def save_settings():
    f = request.form
    cfg = config.load()
    cfg['tv_ip'] = f.get('tv_ip', '').strip()
    tz = f.get('timezone', '').strip()
    try:
        zoneinfo.ZoneInfo(tz)
        cfg['timezone'] = tz
    except Exception:  # noqa: BLE001  keep the old one if the name is not a real time zone
        pass
    cfg['plan_lang'] = f.get('plan_lang', 'en')
    cfg['schedule'] = {
        'enabled': 'schedule_enabled' in f,
        'start': f.get('start', '07:00'),
        'end': f.get('end', '10:00'),
        'days': [i for i in range(7) if f'day{i}' in f],
    }
    cfg['weather'] = {
        'enabled': 'weather_enabled' in f,
        'name': f.get('weather_name', '').strip(),
        'lat': float(f['lat']) if f.get('lat') else None,
        'lon': float(f['lon']) if f.get('lon') else None,
    }
    cfg['holidays'] = {'country': f.get('hol_country', '').strip().upper(), 'subdiv': f.get('hol_subdiv', '').strip().upper()}
    cfg['dim_past_events'] = 'dim_past_events' in f
    cfg['background'] = f.get('background', 'artwork')
    calendars = []
    for i in range(int(f.get('cal_count', 0)) + 1):  # the extra row adds a new calendar
        url = f.get(f'cal_url{i}', '').strip()
        if url and f'cal_delete{i}' not in f:
            calendars.append({'name': f.get(f'cal_name{i}', '').strip() or 'Calendar', 'url': url,
                              'color': f.get(f'cal_color{i}', 'green'), 'role': f.get(f'cal_role{i}', 'events')})
    cfg['calendars'] = calendars
    if cfg['schedule']['start'] >= cfg['schedule']['end']:
        return redirect(url_for('index', msg=ui()['end_after_start']))
    config.save(cfg)
    jobs.log('Settings saved')
    return redirect(url_for('index', msg=ui()['saved']))


def main():
    os.makedirs(config.DATA, exist_ok=True)
    jobs.log('Frame Daily Plan started')
    jobs.start_scheduler()
    appletv.start()
    serve(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8765')), threads=4)


if __name__ == '__main__':
    main()
