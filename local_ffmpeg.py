import asyncio
import json
import os
import subprocess

# Formats supportés en entrée
SUPPORTED_INPUT = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv'}

# Formats supportés en sortie par FFmpeg local
SUPPORTED_OUTPUT = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv'}


def _probe_codec(file_path: str) -> dict:
    """
    Détecte les codecs video/audio et les métadonnées du fichier source via ffprobe.
    Retourne {'video': 'h264', 'audio': 'aac', 'duration': 120, 'width': 1920, 'height': 1080}
    """
    try:
        result = subprocess.run(
            [
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                '-show_format',
                file_path
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        data = json.loads(result.stdout)
        codecs = {'video': None, 'audio': None, 'duration': 0, 'width': 0, 'height': 0}
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video' and not codecs['video']:
                codecs['video'] = stream.get('codec_name')
                codecs['width'] = int(stream.get('width', 0))
                codecs['height'] = int(stream.get('height', 0))
            if stream.get('codec_type') == 'audio' and not codecs['audio']:
                codecs['audio'] = stream.get('codec_name')
        duration = data.get('format', {}).get('duration', 0)
        codecs['duration'] = int(float(duration)) if duration else 0
        return codecs
    except Exception:
        return {'video': None, 'audio': None, 'duration': 0, 'width': 0, 'height': 0}


def _can_copy(codecs: dict, output_ext: str) -> bool:
    """
    Détermine si on peut faire un codec copy (remuxage sans ré-encodage).
    """
    video = codecs.get('video', '')
    audio = codecs.get('audio', '')

    if output_ext == '.mp4':
        video_ok = video in ('h264', 'hevc', 'h265')
        audio_ok = audio in ('aac', 'mp3', 'ac3')
        return video_ok and audio_ok

    if output_ext in ('.mkv', '.avi', '.mov', '.flv'):
        return True

    if output_ext == '.webm':
        video_ok = video in ('vp8', 'vp9')
        audio_ok = audio in ('opus', 'vorbis')
        return video_ok and audio_ok

    return False


def probe(file_path: str) -> dict:
    """Expose les métadonnées d'un fichier (durée, résolution) pour l'envoi Telegram."""
    return _probe_codec(file_path)


async def convert(input_path: str, output_path: str) -> dict:
    """
    Convertit une vidéo avec FFmpeg local.
    Applique les 4 optimisations RAM :
    1. Stream processing — asyncio subprocess
    2. Codec copy quand possible — remuxage sans ré-encodage
    3. -threads 1 — RAM réduite
    4. Pas de fichier temporaire — écriture directe

    Retourne un dict : {'path': output_path, 'duration': 120, 'width': 1920, 'height': 1080}
    """
    output_ext = os.path.splitext(output_path)[1].lower()
    codecs = _probe_codec(input_path)
    use_copy = _can_copy(codecs, output_ext)

    if use_copy:
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-c', 'copy',
            '-threads', '1',
            '-y',
            output_path
        ]
    else:
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-threads', '1',
            '-movflags', '+faststart',
            '-y',
            output_path
        ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    _, stderr = await asyncio.wait_for(process.communicate(), timeout=600)

    if process.returncode != 0:
        error = stderr.decode('utf-8', errors='replace')
        raise RuntimeError(f"FFmpeg échoué (code {process.returncode}): {error[-500:]}")

    if not os.path.exists(output_path):
        raise RuntimeError("FFmpeg n'a pas produit de fichier de sortie")

    out_meta = _probe_codec(output_path)
    return {
        'path': output_path,
        'duration': out_meta['duration'],
        'width': out_meta['width'],
        'height': out_meta['height'],
    }


async def embed_thumbnail(video_path: str, thumbnail_path: str, output_path: str) -> str:
    """
    Intègre le thumbnail directement dans le fichier vidéo comme cover art.
    Visible sur PC (VLC, Windows Media Player, etc.)
    Retourne le chemin du fichier avec thumbnail intégré.
    En cas d'échec, retourne le fichier original sans thumbnail.
    """
    ext = os.path.splitext(video_path)[1].lower()

    if ext == '.mp4':
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-i', thumbnail_path,
            '-map', '0',
            '-map', '1',
            '-c', 'copy',
            '-disposition:1', 'attached_pic',
            '-threads', '1',
            '-y',
            output_path
        ]
    elif ext == '.mkv':
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-i', thumbnail_path,
            '-map', '0',
            '-map', '1',
            '-c', 'copy',
            '-metadata:s:t', 'mimetype=image/jpeg',
            '-threads', '1',
            '-y',
            output_path
        ]
    else:
        # Format non supporté pour l'intégration du thumbnail — copie simple
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-c', 'copy',
            '-threads', '1',
            '-y',
            output_path
        ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    _, stderr = await asyncio.wait_for(process.communicate(), timeout=120)

    if process.returncode != 0 or not os.path.exists(output_path):
        # Fallback : retourne le fichier original sans thumbnail
        return video_path

    return output_path
