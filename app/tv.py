"""Talking to the Frame's Art Mode.

Frames differ by model year. Old ones (e.g. 2017, Art API 1.07) refuse the library's
socket upload and send thumbnails in a way the library cannot decode; for those we do
what the SmartThings app does (one binary WebSocket frame). Newer ones use the library's
standard methods. Each call tries one way and falls back to the other.
"""
import concurrent.futures
import ipaddress
import json
import os
import socket
import time
import uuid

import requests
from samsungtvws import SamsungTVWS

import config

NAME = 'Frame Daily Plan'


def _tv(ip, timeout):
    return SamsungTVWS(host=ip, port=8002, token_file=os.path.join(config.DATA, 'tv-token.txt'),
                       name=NAME, timeout=timeout)


def connect(ip):
    # The TV only asks "allow this device?" on the remote-control channel; an unapproved
    # client on the art channel just gets disconnected. Opening it first triggers the
    # prompt for a new device (and waits long enough for someone to press "Allow").
    auth = _tv(ip, 120)
    auth.open()
    auth.close()
    return _tv(ip, 30).art()


def _send_image_binary(a, data, matte, timeout=15):
    if a.connection is None:
        a.open()
    upload_id = str(uuid.uuid4())
    inner = {'request': 'send_image', 'file_type': 'JPEG', 'matte_id': matte, 'id': upload_id}
    outer = {'method': 'ms.channel.emit', 'params': {'data': json.dumps(inner), 'to': 'host', 'event': 'art_app_request'}}
    header = json.dumps(outer, separators=(',', ':')).encode()
    a.connection.send_binary(len(header).to_bytes(2, 'big') + header + data)
    # Newer Frames (2018+) ignore this upload but keep sending other events, so the
    # library's wait_for_response would wait forever. Give up after `timeout` seconds.
    deadline = time.time() + timeout
    a.connection.settimeout(timeout)
    while time.time() < deadline:
        msg = a.get_websocket_message()
        if msg.get('request_id', msg.get('id')) != upload_id:
            continue
        if msg.get('event') == 'error':
            raise RuntimeError(f'send_image failed: {msg}')
        if msg.get('event') == 'image_added':
            return msg['content_id']
    raise TimeoutError('no image_added reply (newer Frame?)')


def send_image(a, data, matte='none'):
    try:
        return _send_image_binary(a, data, matte)
    except Exception:  # noqa: BLE001  newer firmware: the library's own upload
        a.close()
        return a.upload(data, file_type='JPEG', matte=matte, portrait_matte=matte)


def _thumbnail_binary(a, content_id, timeout=15):
    if a.connection is None:
        a.open()
    req_id = str(uuid.uuid4())
    inner = {'request': 'get_thumbnail', 'content_id': content_id, 'id': req_id,
             'conn_info': {'d2d_mode': 'socket', 'connection_id': uuid.uuid4().int % (4 << 30), 'id': req_id}}
    a.connection.send(json.dumps({'method': 'ms.channel.emit',
                                  'params': {'event': 'art_app_request', 'to': 'host', 'data': json.dumps(inner)}}))
    a.connection.settimeout(timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = a.connection.recv()
        if isinstance(raw, str):
            if '"error"' in raw and req_id in raw:
                raise RuntimeError(f'get_thumbnail {content_id} failed')
            continue
        for magic in (b'\xff\xd8\xff', b'\x89PNG'):
            pos = raw.find(magic)
            if pos >= 0:
                return raw[pos:]
    raise RuntimeError(f'no thumbnail received for {content_id}')


def get_thumbnail(a, content_id):
    try:
        return _thumbnail_binary(a, content_id)
    except Exception:  # noqa: BLE001  newer firmware: the library's own method
        a.close()
        data = a.get_thumbnail(content_id)
        if isinstance(data, dict):
            data = next(iter(data.values()))
        if isinstance(data, list):
            data = data[0]
        return bytes(data)


def status(ip):
    a = connect(ip)
    return {'art_mode': a.get_artmode(), 'current': (a.get_current() or {}).get('content_id')}


def diagnose(ip):
    """Step by step, to see where a connection fails."""
    import time
    steps = []
    tv = _tv(ip, 120)
    for name, fn in (('rest', lambda: tv.rest_device_info()['device'].get('PowerState', 'ok')),
                     ('remote', lambda: (tv.open(), tv.close(), 'ok')[2]),
                     ('art', lambda: tv.art().get_artmode())):
        t = time.time()
        try:
            steps.append((name, 'ok', str(fn())[:80], round(time.time() - t, 1)))
        except Exception as e:  # noqa: BLE001
            steps.append((name, 'FAIL', f'{type(e).__name__}: {e}'[:200], round(time.time() - t, 1)))
            break
    return steps


def _local_networks():
    """The /24 networks this machine is on (inside Docker this needs host networking)."""
    nets = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('192.0.2.1', 9))  # no packet is sent; this just picks the outgoing interface
        nets.add(ipaddress.ip_network(s.getsockname()[0] + '/24', strict=False))
        s.close()
    except OSError:
        pass
    return [n for n in nets if n.is_private]


def _probe(ip):
    try:
        info = requests.get(f'http://{ip}:8001/api/v2/', timeout=1.5).json().get('device', {})
    except Exception:  # noqa: BLE001
        return None
    if info.get('FrameTVSupport') != 'true':
        return None
    return {'ip': str(ip), 'name': info.get('name', '').replace('[TV]', '').strip() or 'Frame',
            'model': info.get('modelName', '')}


def discover():
    """Find Frames on the local network by asking every address for Samsung's device info."""
    hosts = [ip for net in _local_networks() for ip in net.hosts()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        return [r for r in pool.map(_probe, hosts) if r]
