import asyncio
import aiohttp
import os

FFMPEG_MICRO_BASE = "https://api.ffmpeg-micro.com"


async def _get_key(api_keys: list, index: int) -> str:
    """Retourne la clé à utiliser (rotation round-robin)"""
    return api_keys[index % len(api_keys)]


async def _upload_file(session: aiohttp.ClientSession, headers: dict, file_path: str) -> str:
    """
    Upload un fichier local vers FFmpeg Micro.
    Retourne l'URL gs:// du fichier uploadé.
    """
    file_size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)

    # Détermine le content type
    ext = os.path.splitext(filename)[1].lower()
    content_type_map = {
        '.mp4': 'video/mp4',
        '.mkv': 'video/x-matroska',
        '.avi': 'video/x-msvideo',
        '.mov': 'video/quicktime',
        '.webm': 'video/webm',
        '.flv': 'video/x-flv',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
    }
    content_type = content_type_map.get(ext, 'application/octet-stream')

    # Étape 1 : Presigned URL
    async with session.post(
        f"{FFMPEG_MICRO_BASE}/v1/upload/presigned-url",
        headers=headers,
        json={
            "filename": filename,
            "contentType": content_type,
            "fileSize": file_size
        }
    ) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Presigned URL failed ({resp.status}): {text}")
        data = await resp.json()
        upload_url = data["result"]["uploadUrl"]
        stored_filename = data["result"]["filename"]

    # Étape 2 : Upload
    with open(file_path, "rb") as f:
        file_data = f.read()

    async with session.put(
        upload_url,
        headers={"Content-Type": content_type},
        data=file_data
    ) as resp:
        if resp.status not in (200, 204):
            text = await resp.text()
            raise RuntimeError(f"Upload failed ({resp.status}): {text}")

    # Étape 3 : Confirm
    async with session.post(
        f"{FFMPEG_MICRO_BASE}/v1/upload/confirm",
        headers=headers,
        json={
            "filename": stored_filename,
            "fileSize": file_size
        }
    ) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Confirm upload failed ({resp.status}): {text}")
        confirm_data = await resp.json()
        file_url = confirm_data.get("result", {}).get("fileUrl", "")
        if not file_url.startswith("gs://"):
            file_url = f"gs://ffmpeg-micro-uploads/{stored_filename}"

    return file_url


async def _poll_job(session: aiohttp.ClientSession, headers: dict, job_id: str, timeout_minutes: int = 10) -> str:
    """
    Poll un job jusqu'à completion.
    Retourne l'URL de téléchargement signée (Google Storage).
    """
    max_attempts = timeout_minutes * 12  # toutes les 5s
    for _ in range(max_attempts):
        await asyncio.sleep(5)
        async with session.get(
            f"{FFMPEG_MICRO_BASE}/v1/transcodes/{job_id}",
            headers=headers
        ) as resp:
            if resp.status != 200:
                continue
            status_data = await resp.json()
            status = status_data.get("status", "")

            if status == "completed":
                break
            elif status == "failed":
                raise RuntimeError(f"Job échoué: {status_data}")
    else:
        raise RuntimeError(f"Timeout: le job a pris plus de {timeout_minutes} minutes")

    # Récupère l'URL de téléchargement signée
    async with session.get(
        f"{FFMPEG_MICRO_BASE}/v1/transcodes/{job_id}/download",
        headers=headers
    ) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Download URL failed ({resp.status}): {text}")
        dl_data = await resp.json()
        return dl_data["url"]  # URL Google Storage signée


async def process_video(
    file_path: str,
    api_keys: list,
    key_index: int = 0,
    output_format: str = "mp4",
    quality: str = "medium",
    resolution: str = "1080p",
    thumbnail_path: str = None,
) -> str:
    """
    Traite une vidéo via FFmpeg Micro (renommage + conversion + thumbnail optionnel).
    Retourne une URL Google Storage signée — Telegram télécharge directement depuis cette URL.

    Le fichier ne transite JAMAIS par Railway après le traitement.
    """
    api_key = await _get_key(api_keys, key_index)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:

        # Upload la vidéo source
        input_gs_url = await _upload_file(session, headers, file_path)

        # Upload le thumbnail si fourni
        thumbnail_gs_url = None
        if thumbnail_path and os.path.exists(thumbnail_path):
            thumbnail_gs_url = await _upload_file(session, headers, thumbnail_path)

        # Construit les options FFmpeg
        if thumbnail_gs_url:
            # Mode avancé : applique le thumbnail comme cover art
            options = [
                {"option": "-c:v", "argument": "copy"},
                {"option": "-c:a", "argument": "copy"},
            ]
            transcode_body = {
                "inputs": [
                    {"url": input_gs_url},
                    {"url": thumbnail_gs_url}
                ],
                "outputFormat": output_format,
                "options": options
            }
        else:
            # Mode simple : conversion avec preset qualité
            transcode_body = {
                "inputs": [{"url": input_gs_url}],
                "outputFormat": output_format,
                "preset": {
                    "quality": quality,
                    "resolution": resolution
                }
            }

        # Crée le job de transcode
        async with session.post(
            f"{FFMPEG_MICRO_BASE}/v1/transcodes",
            headers=headers,
            json=transcode_body
        ) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(f"Transcode job failed ({resp.status}): {text}")
            job_data = await resp.json()
            job_id = job_data["id"]

        # Poll et retourne l'URL directe
        download_url = await _poll_job(session, headers, job_id, timeout_minutes=10)

    return download_url


async def compress_video(
    file_path: str,
    api_keys: list,
    key_index: int = 0,
    quality: str = "medium",
    resolution: str = "1080p",
    output_format: str = "mp4",
    thumbnail_path: str = None,
) -> str:
    """
    Compresse une vidéo. Retourne l'URL directe Google Storage.
    """
    return await process_video(
        file_path=file_path,
        api_keys=api_keys,
        key_index=key_index,
        output_format=output_format,
        quality=quality,
        resolution=resolution,
        thumbnail_path=thumbnail_path,
    )
