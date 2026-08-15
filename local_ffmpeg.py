import asyncio
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
        import json
        data = json.loads(result.stdout)
        codecs = {'video': None, 'audio': None, 'duration': 0, 'width': 0, 'height': 0}
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video' and not codecs['video']:
                codecs['video'] = stream.get('codec_name')
                codecs['width'] = int(stream.get('width', 0))
                codecs['height'] = int(stream.get('height', 0))
            if stream.get('codec_type') == 'audio' and not codecs['audio']:
                codecs['audio'] = stream.get('codec_name')
        # Durée depuis format
        duration = data.get('format', {}).get('duration', 0)
        codecs['duration'] = int(float(duration)) if duration else 0
        return codecs
    except Exception:
        return {'video': None, 'audio': None, 'duration': 0, 'width': 0, 'height': 0}


def _can_copy(codecs: dict, output_ext: str) -> bool:
    """
    Détermine si on peut faire un codec copy (remuxage sans ré-encodage).
    Pour mp4 : video doit être h264/hevc, audio doit être aac/mp3
    Pour mkv : accepte presque tout
    Pour webm : video doit être vp8/vp9, audio doit être opus/vorbis
    """
    video = codecs.get('video', '')
    audio = codecs.get('audio', '')

    if output_ext == '.mp4':
        video_ok = video in ('h264', 'hevc', 'h265')
        audio_ok = audio in ('aac', 'mp3', 'ac3')
        return video_ok and audio_ok

    if output_ext in ('.mkv', '.avi', '.mov', '.flv'):
        # MKV accepte presque tous les codecs
        return True

    if output_ext == '.webm':
        video_ok = video in ('vp8', 'vp9')
        audio_ok = audio in ('opus', 'vorbis')
        return video_ok and audio_ok

    return False


async def convert(
    input_path: str,
    output_path: str,
) -> dict:
    """
    Convertit une vidéo avec FFmpeg local.
    Applique les 4 optimisations RAM :
    1. Stream processing — FFmpeg lit/écrit en chunks, pas tout en mémoire
    2. Codec copy quand possible — remuxage sans ré-encodage
    3. -threads 1 — RAM réduite
    4. Pas de fichier temporaire — écriture directe vers output_path

    Retourne un dict : {'path': output_path, 'duration': 120, 'width': 1920, 'height': 1080}
    """
    output_ext = os.path.splitext(output_path)[1].lower()
    codecs = _probe_codec(input_path)
    use_copy = _can_copy(codecs, output_ext)

    if use_copy:
        # ── Optimisation 2 : Codec copy ──────────────────────────────────
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-c', 'copy',          # copie sans ré-encodage
            '-threads', '1',       # optimisation 3 : RAM réduite
            '-y',                  # écrase si existe
            output_path            # optimisation 4 : écriture directe
        ]
    else:
        # ── Conversion réelle avec RAM minimale ──────────────────────────
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-c:v', 'libx264',
            '-preset', 'ultrafast',  # encode rapide, RAM faible
            '-crf', '23',            # qualité acceptable
            '-c:a', 'aac',
            '-b:a', '128k',
            '-threads', '1',         # optimisation 3 : RAM réduite
            '-movflags', '+faststart',
            '-y',
            output_path              # optimisation 4 : écriture directe
        ]

    # ── Optimisation 1 : Stream processing via asyncio subprocess ────────
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

    # Récupère les métadonnées du fichier converti pour Telegram
    out_meta = _probe_codec(output_path)

    return {
        'path': output_path,
        'duration': out_meta['duration'],
        'width': out_meta['width'],
        'height': out_meta['height'],
    }


def probe(file_path: str) -> dict:
    """Expose les métadonnées d'un fichier (durée, résolution) pour l'envoi Telegram."""
    return _probe_codec(file_path)
