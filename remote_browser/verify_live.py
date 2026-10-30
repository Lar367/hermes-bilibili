"""Read-only transport/port verification; never sends mouse/keyboard input."""
import json
from pathlib import Path
import struct
import subprocess
import urllib.request
import zlib
from websockets.sync.client import connect


def verify():
    listeners = subprocess.check_output(['ss', '-ltnp'], text=True)
    assert not any('x11vnc' in line for line in listeners.splitlines()), 'VNC must have no TCP listener'
    for port in (6080, 9223):
        rows = [line for line in listeners.splitlines() if ':' + str(port) + ' ' in line]
        assert len(rows) == 1 and '127.0.0.1:' + str(port) in rows[0], rows
    assert urllib.request.urlopen('http://127.0.0.1:6080/vnc.html', timeout=3).status == 200
    try:
        with connect('ws://127.0.0.1:6080/websockify', origin='https://untrusted.example',
                     subprotocols=['binary'], open_timeout=4):
            raise AssertionError('Cross-site WebSocket accepted')
    except Exception as exc:
        assert '403' in str(exc), str(exc)
    with connect('ws://127.0.0.1:6080/websockify', origin='http://127.0.0.1:6080',
                 subprotocols=['binary'], open_timeout=4) as ws:
        buf = bytearray()
        def read(n):
            while len(buf) < n:
                message = ws.recv(timeout=8)
                assert isinstance(message, bytes)
                buf.extend(message)
            val = bytes(buf[:n])
            del buf[:n]
            return val
        assert read(12).startswith(b'RFB 003.')
        ws.send(b'RFB 003.008\n')
        types = read(read(1)[0])
        assert 1 in types
        ws.send(b'\x01')
        assert read(4) == b'\0\0\0\0'
        ws.send(b'\x01')
        init = read(24)
        width, height = struct.unpack('!HH', init[:4])
        read(struct.unpack('!I', init[20:24])[0])
        assert (width, height) == (1280, 800)
        # Request RGBX bytes using raw encoding, avoiding image-library dependencies.
        pixel_format = struct.pack('!BBBBHHHBBBxxx', 32, 24, 0, 1, 255, 255, 255, 0, 8, 16)
        ws.send(b'\0\0\0\0' + pixel_format)
        ws.send(struct.pack('!BBHi', 2, 0, 1, 0))
        ws.send(struct.pack('!BBHHHH', 3, 0, 0, 0, width, height))
        pixels = bytearray(width * height * 3)
        assert read(1) == b'\0'
        header = read(3)
        rects = struct.unpack('!H', header[1:])[0]
        for _ in range(rects):
            x, y, w, h, encoding = struct.unpack('!HHHHi', read(12))
            assert encoding == 0
            raw = read(w * h * 4)
            for row in range(h):
                source = raw[row*w*4:(row+1)*w*4]
                rgb = bytearray(w * 3)
                rgb[0::3], rgb[1::3], rgb[2::3] = source[0::4], source[1::4], source[2::4]
                dest = ((y + row) * width + x) * 3
                pixels[dest:dest + w*3] = rgb
        def chunk(kind, data):
            return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
        scan = b''.join(b'\0' + pixels[row*width*3:(row+1)*width*3] for row in range(height))
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(scan)) + chunk(b'IEND', b'')
        import os
        image = Path(os.environ['TMPDIR']) / 'bili-takeover-screen.png'
        image.write_bytes(png)
    runtime = Path('/run/user') / str(__import__('os').getuid()) / 'bili-browser-takeover'
    assert runtime.stat().st_mode & 0o777 == 0o700
    assert (runtime/'vnc.sock').stat().st_mode & 0o777 == 0o600
    return {'passed': True, 'http_status': 200, 'websocket_rfb': [width, height],
            'bad_origin': 403, 'vnc_tcp': False, 'loopback_only': True, 'screenshot': str(image)}


if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False))
