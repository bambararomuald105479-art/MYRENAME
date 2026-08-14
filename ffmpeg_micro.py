import asyncio
import aiohttp
import os

FFMPEG_MICRO_BASE = "https://api.ffmpeg-micro.com"


async def _get_key(api_keys: list, index: int) -> str:
    """Retourne la clé à utiliser (rotation round-robin)"""
    return api_keys[index % len(api_keys)]


async def compress_video(
    file_path: str,
    api_keys: list,
    key_index: int = 0,
    quality: str = "medium",
    resolution: str = "1080p",
    output_format: str = "mp4"
) -> str:
    """
    Compresse une vidéo via FFmpeg Micro API.
    Retourne le chemin du fichier compressé téléchargé localement.

    Étapes :
    1. Presigned upload URL
    2. Upload du fichier
    3. Confirm upload
    4. Créer le job de transcode
    5. Polling jusqu'à completion
    6. Download du fichier compressé
    """
    api_key = await _get_key(api_keys, key_index)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    file_size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)
    content_type = "video/mp4"

    async with aiohttp.ClientSession() as session:

        # ── ÉTAPE 1 : Presigned URL ──────────────────────────────────────
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

        # ── ÉTAPE 2 : Upload du fichier ──────────────────────────────────
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

        # ── ÉTAPE 3 : Confirm upload ─────────────────────────────────────
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
            # Récupère le bucket depuis fileUrl
            file_url = confirm_data.get("result", {}).get("fileUrl", "")
            # fileUrl est de type gs://bucket/filename
            input_gs_url = file_url if file_url.startswith("gs://") else f"gs://ffmpeg-micro-uploads/{stored_filename}"

        # ── ÉTAPE 4 : Créer le job de transcode ──────────────────────────
        async with session.post(
            f"{FFMPEG_MICRO_BASE}/v1/transcodes",
            headers=headers,
            json={
                "inputs": [{"url": input_gs_url}],
                "outputFormat": output_format,
                "preset": {
                    "quality": quality,
                    "resolution": resolution
                }
            }
        ) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(f"Transcode job failed ({resp.status}): {text}")
            job_data = await resp.json()
            job_id = job_data["id"]

        # ── ÉTAPE 5 : Polling jusqu'à completion ─────────────────────────
        max_attempts = 60  # 5 minutes max (60 × 5s)
        for attempt in range(max_attempts):
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
                    raise RuntimeError(f"Transcode job failed: {status_data}")
                # sinon on continue à poller (pending / processing)

        else:
            raise RuntimeError("Timeout: la compression a pris plus de 5 minutes")

        # ── ÉTAPE 6 : Download du fichier compressé ───────────────────────
        async with session.get(
            f"{FFMPEG_MICRO_BASE}/v1/transcodes/{job_id}/download",
            headers=headers
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Download URL failed ({resp.status}): {text}")
            dl_data = await resp.json()
            download_url = dl_data["url"]

        # Télécharge le fichier compressé localement
        output_path = file_path.replace(".", "_compressed.", 1)
        async with session.get(download_url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Download failed ({resp.status})")
            with open(output_path, "wb") as f:
                f.write(await resp.read())

    return output_path
