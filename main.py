import os
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
import ffmpeg_micro
import local_ffmpeg

# ============ PYROFORK ============
pyrogram_app = Client(
    "bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN
)

# ============ GLOBAL STATE ============
user_sessions = {}

# Thumbnail persistant par utilisateur (chemin local)
user_thumbnails = {}

# Rotation des clés FFmpeg Micro
ffmpeg_key_index = 0


def next_ffmpeg_key():
    global ffmpeg_key_index
    idx = ffmpeg_key_index
    ffmpeg_key_index = (ffmpeg_key_index + 1) % len(config.FFMPEG_MICRO_API_KEYS)
    return idx


def is_authorized(user_id):
    if not config.ALLOWED_USER_IDS:
        return True
    return user_id in config.ALLOWED_USER_IDS


def _cleanup(*paths):
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


# ============ HANDLERS ============

@pyrogram_app.on_message(filters.command("start"))
async def handle_start(client, message):
    if not is_authorized(message.from_user.id):
        await message.reply_text("❌ Accès refusé!")
        return

    thumb = "✅ Défini" if user_thumbnails.get(message.from_user.id) else "❌ Aucun"
    await message.reply_text(
        "👋 Bienvenue!\n\n"
        "📝 Envoie un fichier ou une vidéo pour le renommer\n"
        "🖼️ Envoie une image pour définir le thumbnail\n"
        "🗜️ /compress — Compresser une vidéo\n"
        "🗑️ /clearthumb — Supprimer le thumbnail\n\n"
        f"Thumbnail actuel: {thumb}"
    )


@pyrogram_app.on_message(filters.command("clearthumb"))
async def handle_clearthumb(client, message):
    if not is_authorized(message.from_user.id):
        return
    user_id = message.from_user.id
    if user_id in user_thumbnails:
        _cleanup(user_thumbnails[user_id])
        del user_thumbnails[user_id]
        await message.reply_text("🗑️ Thumbnail supprimé.")
    else:
        await message.reply_text("Aucun thumbnail défini.")


@pyrogram_app.on_message(filters.command("compress"))
async def handle_compress_cmd(client, message):
    if not is_authorized(message.from_user.id):
        return
    await message.reply_text("🗜️ Mode compression activé\n\nEnvoie la vidéo à compresser.")
    user_sessions[message.from_user.id] = {'mode': 'compress_waiting_file'}


@pyrogram_app.on_message(filters.photo)
async def handle_photo(client, message):
    """Reçoit une image → sauvegarde comme thumbnail persistant"""
    if not is_authorized(message.from_user.id):
        return
    user_id = message.from_user.id

    if user_id in user_thumbnails:
        _cleanup(user_thumbnails[user_id])

    thumb_path = f"/tmp/thumb_{user_id}.jpg"
    await client.download_media(message.photo, file_name=thumb_path)
    user_thumbnails[user_id] = thumb_path

    await message.reply_text("🖼️ Thumbnail sauvegardé! Il sera utilisé pour toutes les prochaines vidéos.")


@pyrogram_app.on_message(filters.document | filters.video)
async def handle_file(client, message):
    if not is_authorized(message.from_user.id):
        return

    file = message.document or message.video
    filename = file.file_name or "file"
    user_id = message.from_user.id
    session = user_sessions.get(user_id, {})

    # ── Mode compression ──────────────────────────────────────────────────
    if session.get('mode') == 'compress_waiting_file':
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv'):
            await message.reply_text("❌ Format non supporté.")
            return

        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🟢 Haute", callback_data=f"compress_high_{user_id}"),
                InlineKeyboardButton("🟡 Moyenne", callback_data=f"compress_medium_{user_id}"),
                InlineKeyboardButton("🔴 Basse", callback_data=f"compress_low_{user_id}")
            ]
        ])

        user_sessions[user_id] = {
            'mode': 'compress_quality',
            'message': message,
            'filename': filename,
            'original_ext': ext
        }

        thumb_status = "✅" if user_thumbnails.get(user_id) else "❌"
        await message.reply_text(
            f"📹 `{filename}`\n🖼️ Thumbnail: {thumb_status}\n\nChoisis la qualité:",
            reply_markup=buttons
        )
        return

    # ── Mode renommage (comportement par défaut) ──────────────────────────
    user_sessions[user_id] = {
        'mode': 'rename',
        'message': message,
        'filename': filename,
        'original_ext': os.path.splitext(filename)[1].lower()
    }

    thumb_status = "✅" if user_thumbnails.get(user_id) else "❌ (envoie une image pour en définir un)"
    await message.reply_text(
        f"📝 `{filename}`\n🖼️ Thumbnail: {thumb_status}\n\nNouveau nom (avec ou sans extension):"
    )


@pyrogram_app.on_message(filters.text)
async def handle_text(client, message):
    if message.text.startswith('/'):
        return

    user_id = message.from_user.id
    session = user_sessions.get(user_id, {})

    if not session or session.get('mode') != 'rename':
        return

    new_name = message.text.strip()
    if not new_name:
        return

    # Ajoute l'extension si absente
    if '.' not in new_name:
        new_name = new_name + session['original_ext']

    session['new_name'] = new_name

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 Envoyer", callback_data=f"send_doc_{user_id}")
        ]
    ])

    await message.reply_text(
        f"📝 Nouveau nom: `{new_name}`\n\nConfirmer?",
        reply_markup=buttons
    )


@pyrogram_app.on_callback_query()
async def handle_callback(client, callback):
    user_id = callback.from_user.id
    session = user_sessions.get(user_id, {})

    if not session:
        await callback.answer("Session expirée, recommence.")
        return

    # ── Callback compression ──────────────────────────────────────────────
    if callback.data.startswith("compress_"):
        parts = callback.data.split("_")
        quality = parts[1]

        original_msg = session['message']
        filename = session['filename']
        local_path = f"/tmp/{user_id}_{filename}"
        thumbnail_path = user_thumbnails.get(user_id)

        await callback.edit_message_text("⏳ Téléchargement depuis Telegram...")
        try:
            await client.download_media(original_msg, file_name=local_path)
        except Exception as e:
            await callback.edit_message_text(f"❌ Erreur téléchargement: {e}")
            return

        await callback.edit_message_text(f"⏳ Compression ({quality}) via FFmpeg Micro...")
        try:
            download_url = await ffmpeg_micro.compress_video(
                file_path=local_path,
                api_keys=config.FFMPEG_MICRO_API_KEYS,
                key_index=next_ffmpeg_key(),
                quality=quality,
                resolution="1080p",
                output_format="mp4",
                thumbnail_path=thumbnail_path
            )
        except Exception as e:
            await callback.edit_message_text(f"❌ Erreur compression: {e}")
            _cleanup(local_path)
            return
        finally:
            _cleanup(local_path)

        # Télécharge localement pour ajouter le thumbnail via Pyrogram
        await callback.edit_message_text("⏳ Finalisation...")
        compressed_name = os.path.splitext(filename)[0] + "_compressed.mp4"
        final_path = f"/tmp/{user_id}_{compressed_name}"
        try:
            async with aiohttp.ClientSession() as dl_session:
                async with dl_session.get(download_url) as resp:
                    with open(final_path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            f.write(chunk)

            await callback.edit_message_text("📤 Envoi vers Telegram...")
            await client.send_document(
                callback.message.chat.id,
                final_path,
                file_name=compressed_name,
                caption=f"✅ Compressé ({quality}): `{compressed_name}`",
                thumb=thumbnail_path
            )
            await callback.edit_message_text("✅ Compression terminée!")
        except Exception as e:
            await callback.edit_message_text(f"❌ Erreur envoi: {e}")
        finally:
            _cleanup(final_path)

        del user_sessions[user_id]
        return

    # ── Callback renommage ────────────────────────────────────────────────
    if 'new_name' not in session:
        await callback.answer("Session expirée, recommence.")
        return

    is_video = 'vid' in callback.data
    new_name = session['new_name']
    original_msg = session['message']
    original_ext = session['original_ext']
    new_ext = os.path.splitext(new_name)[1].lower()
    thumbnail_path = user_thumbnails.get(user_id)

    # ── Même extension : renommage simple, pas de FFmpeg ─────────────────
    if new_ext == original_ext:
        await callback.edit_message_text("⏳ Téléchargement depuis Telegram...")
        local_path = f"/tmp/{user_id}_{new_name}"
        try:
            await client.download_media(original_msg, file_name=local_path)
        except Exception as e:
            await callback.edit_message_text(f"❌ Erreur téléchargement: {e}")
            return

        final_path = local_path
        try:
            # Intègre le thumbnail dans le fichier si disponible
            if thumbnail_path:
                await callback.edit_message_text("⏳ Intégration du thumbnail...")
                thumb_output = f"/tmp/{user_id}_thumb_{new_name}"
                final_path = await local_ffmpeg.embed_thumbnail(local_path, thumbnail_path, thumb_output)

            meta = local_ffmpeg.probe(final_path)
            await callback.edit_message_text("📤 Envoi vers Telegram...")

            await client.send_document(
                callback.message.chat.id,
                final_path,
                file_name=new_name,
                caption=new_name,
                thumb=thumbnail_path,
                duration=meta['duration'],
                width=meta['width'],
                height=meta['height']
            )
            await callback.edit_message_text("✅ Fait!")
            del user_sessions[user_id]
        except Exception as e:
            await callback.edit_message_text(f"❌ Erreur envoi: {e}")
        finally:
            _cleanup(local_path)
            if final_path != local_path:
                _cleanup(final_path)
        return

    # ── Extension différente : conversion via FFmpeg local ────────────────
    original_filename = session['filename']
    local_path = f"/tmp/{user_id}_{original_filename}"
    output_path = f"/tmp/{user_id}_{new_name}"

    await callback.edit_message_text("⏳ Téléchargement depuis Telegram...")
    try:
        await client.download_media(original_msg, file_name=local_path)
    except Exception as e:
        await callback.edit_message_text(f"❌ Erreur téléchargement: {e}")
        return

    await callback.edit_message_text("⏳ Conversion via FFmpeg local...")
    try:
        result = await local_ffmpeg.convert(
            input_path=local_path,
            output_path=output_path,
        )
    except Exception as e:
        await callback.edit_message_text(f"❌ Erreur conversion: {e}")
        _cleanup(local_path)
        return
    finally:
        _cleanup(local_path)

    # Envoie avec thumbnail intégré dans le fichier
    final_path = result['path']
    try:
        if thumbnail_path:
            await callback.edit_message_text("⏳ Intégration du thumbnail...")
            thumb_output = f"/tmp/{user_id}_thumb_{new_name}"
            final_path = await local_ffmpeg.embed_thumbnail(result['path'], thumbnail_path, thumb_output)

        meta = local_ffmpeg.probe(final_path)
        await callback.edit_message_text("📤 Envoi vers Telegram...")

        await client.send_document(
            callback.message.chat.id,
            final_path,
            file_name=new_name,
            caption=new_name,
            thumb=thumbnail_path,
            duration=meta['duration'],
            width=meta['width'],
            height=meta['height']
        )
        await callback.edit_message_text("✅ Fait!")
        del user_sessions[user_id]
    except Exception as e:
        await callback.edit_message_text(f"❌ Erreur envoi: {e}")
    finally:
        _cleanup(output_path)
        if final_path != output_path:
            _cleanup(final_path)


if __name__ == '__main__':
    print("✅ Bot démarrage (pyrofork)...")
    print("=" * 50)
    pyrogram_app.run()
