"""Optional public-CDN frames; local ffmpeg only, no authenticated media."""
import json
from contextlib import nullcontext
import math
import re
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit
import uuid
from .client import APIError, NoRedirect, _bvid, _number, validate_url, set_read_timeout


class MediaError(APIError):
    pass


MAX_BYTES = 20_000_000
MAX_DOWNLOAD_SECONDS = 30
MAX_DURATION = 600
MAX_FRAME_COUNT = 20
MAX_FRAME_BYTES = 50_000_000


def prune_frames(output, *, preserve=None):
    """Bound owned collage files only; never follow or remove symlinks."""
    files = []
    for path in Path(output).iterdir():
        if not re.fullmatch(r'BV[0-9A-Za-z]{10}-[a-f0-9]{32}\.png', path.name):
            continue
        if path.is_symlink() or not path.is_file():
            raise MediaError('Owned frame path must be a regular file')
        info = path.stat()
        files.append((path, info.st_mtime_ns, info.st_size))
    total = sum(size for _, _, size in files)
    count = len(files)
    for path, _, size in sorted(files, key=lambda item: (item[1], item[0].name)):
        if count <= MAX_FRAME_COUNT and total <= MAX_FRAME_BYTES:
            break
        if path == preserve:
            continue
        if path.is_symlink():
            raise MediaError('Symlink frame path forbidden')
        path.unlink()
        count -= 1
        total -= size
    if count > MAX_FRAME_COUNT or total > MAX_FRAME_BYTES:
        raise MediaError('Frame retention ceiling exceeded')


def download_public(url, target, *, max_bytes=MAX_BYTES, timeout=MAX_DOWNLOAD_SECONDS, opener=None, clock=time.monotonic,
                    dispatch=None, on_response=None):
    validate_media_url(url)
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES or type(timeout) not in (int, float) or not 0 < timeout <= MAX_DOWNLOAD_SECONDS:
        raise MediaError('Invalid download bounds')
    target = Path(target)
    if target.exists() or target.is_symlink():
        raise MediaError('Download target must be a new regular file')
    start = clock()
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers={'User-Agent':'hermes-bilibili/1.0', 'Accept':'video/*'}, method='GET')
    created = False
    try:
        with dispatch() if dispatch is not None else nullcontext():
            remaining = timeout - (clock() - start)
            if remaining <= 0:
                raise MediaError('Media download deadline exceeded before open')
            try:
                raw = opener.open(request, timeout=min(20, remaining))
            except urllib.error.HTTPError as exc:
                raw = exc
            with raw:
                if on_response is not None:
                    on_response(raw)
                if raw.status != 200:
                    raise MediaError('Public media download rejected')
                length = raw.headers.get('Content-Length')
                if length is not None and (not str(length).isdigit() or int(length) > max_bytes):
                    raise MediaError('Media exceeds byte ceiling')
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                created = True
                with os.fdopen(fd, 'wb') as output:
                    size = 0
                    while True:
                        remaining = timeout - (clock() - start)
                        if remaining <= 0:
                            raise MediaError('Media download deadline exceeded')
                        set_read_timeout(raw, remaining)
                        block = getattr(raw, 'read1', raw.read)(min(65536, max_bytes - size + 1))
                        if clock() - start >= timeout:
                            raise MediaError('Media download deadline exceeded')
                        if not block:
                            break
                        size += len(block)
                        if size > max_bytes:
                            raise MediaError('Media exceeds byte ceiling')
                        output.write(block)
                    if size == 0:
                        raise MediaError('Media download was empty')
        return str(target)
    except Exception as exc:
        if created:
            target.unlink(missing_ok=True)
        if isinstance(exc, MediaError):
            raise
        raise MediaError('Public media download failed') from None


def extract_frames(client, bvid, output_dir):
    """Return one PNG collage in Store.path.parent; never return source URL.

    Caller passes the plugin data directory or its descendant. External binaries
    ffprobe and ffmpeg are optional; unavailable/unsupported media fail honestly.
    """
    bvid = _bvid(bvid)
    try:
        root = Path(os.path.abspath(Path(client.store.path).parent))
        output = Path(os.path.abspath(output_dir))
        if not output.is_relative_to(root):
            raise MediaError('Frame output must remain under plugin data')
        for part in (output, *output.parents):
            if part.is_symlink():
                raise MediaError('Symlink media paths are forbidden')
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            raise MediaError('Frame extraction requires optional ffmpeg and ffprobe')
        video = client.video(bvid)
        duration = _number(video['duration'])
        if not 0 < duration <= MAX_DURATION:
            raise MediaError('Video duration exceeds frame extraction ceiling')
        data = client._signed_request('/x/player/wbi/playurl', {'bvid':bvid, 'cid':video['cid'], 'qn':16, 'fnval':0, 'fourk':0})
        parts = data.get('durl') or []
        if len(parts) != 1:
            raise MediaError('Only a single bounded public media file is supported')
        url = parts[0]['url']
        validate_media_url(url)
        size = parts[0].get('size')
        if size is not None and (type(size) is not int or not 0 < size <= MAX_BYTES):
            raise MediaError('Media exceeds byte ceiling')
        output.mkdir(parents=True, mode=0o700, exist_ok=True)
        prune_frames(output)
        result = output / (bvid + '-' + uuid.uuid4().hex + '.png')
        success = False
        try:
            with tempfile.TemporaryDirectory(prefix='.frames-', dir=output) as work:
                source = Path(work) / 'source.mp4'
                client.download_public(url, source)
                probe = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file', '-f', 'mov',
                                        '-show_entries', 'format=duration:stream=codec_type,width,height',
                                        '-of', 'json', str(source)], capture_output=True, check=True, timeout=15, shell=False)
                info = json.loads(probe.stdout)
                actual = float(info['format']['duration'])
                streams = [s for s in info.get('streams', []) if s.get('codec_type') == 'video']
                if (not math.isfinite(actual) or not 0 < actual <= MAX_DURATION or not streams
                        or any(type(s.get(k)) is not int or not 0 < s[k] <= 4096 for s in streams for k in ('width', 'height'))):
                    raise MediaError('Decoded media exceeds duration or dimension ceiling')
                interval = max(actual / 9, 0.1)
                filters = f'fps=1/{interval:.6f},scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2,tile=3x3'
                subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file', '-threads', '1', '-f', 'mov',
                                '-i', str(source), '-t', str(MAX_DURATION), '-an', '-sn', '-dn',
                                '-vf', filters, '-frames:v', '1', '-threads', '1', '-n', str(result)],
                               capture_output=True, check=True, timeout=40, shell=False)
                if not result.is_file() or not 8 < result.stat().st_size <= 5_000_000:
                    raise MediaError('No bounded frame collage was produced')
                os.chmod(result, 0o600)
                prune_frames(output, preserve=result)
                success = True
                return {'bvid':bvid, 'path':str(result), 'duration':actual, 'layout':'3x3', 'untrusted':True}
        finally:
            if not success:
                result.unlink(missing_ok=True)
    except MediaError:
        raise
    except Exception:
        raise MediaError('Frame extraction unavailable for this video') from None


def validate_media_url(url):
    try:
        host = urlsplit(url).hostname
        # Explicit CDN namespaces, not arbitrary bilibili.com or arbitrary URLs.
        if not host or not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:bilivideo\.com|bilivideo\.cn)', host):
            raise MediaError('Media URL is not an approved public CDN')
        return validate_url(url, {host})
    except (APIError, ValueError, TypeError, AttributeError):
        raise MediaError('Media URL rejected by public CDN policy') from None
