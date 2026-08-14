import os
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
import ffmpeg_micro

# ============ PYROGRAM ============
pyrogram_app = Client(
    "bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN
)

# ============ GLOBAL STATE ============
user_sessions = {}

# Rotation des clés FFmpeg Micro
ffmpeg_key_index = 0


def next_ffmpeg_key():
    """Retourne l'index de la prochaine clé FFmpeg (round-robin)"""
    global ffmpeg_key_index
    idx = ffmpeg_key_index
    ffmpeg_key_index = (ffmpeg_key_index + 1) % len(config.FFMPEG_MICRO_API_KEYS)
    return idx


def is_authorized(user_id):
    """Vérifie si l'utilisateur est autorisé"""
    if not config.ALLOWED_USER_IDS:
        return True
    return user_id in config.ALLOWED_USER_IDS


# ============ PYROGRAM HANDLERS ============

@pyrogram_app.on_message(filters.command("start"))
async def handle_start(client, message):
    """Commande /start"""
    if not is_authorized(message.from_user.id):
        await message.reply_text("❌ Accès refusé!")
        return

    await message.reply_text(
        "👋 Bienvenue!\n\n"
        "📝 Envoie un fichier ou une vidéo\n"
        "✏️ Donne-lui un nouveau nom\n"
        "📤 Choisis le format d'envoi\n\n"
        "🗜️ /compress — Compresser une vidéo"
    )


@pyrogram_app.on_message(filters.command("compress"))
async def handle_compress_cmd(client, message):
    """Commande /compress — démarre le flow de compression"""
    if not is_authorized(message.from_user.id):
        await message.reply_text("❌ Accès refusé!")
        return

    await message.reply_text(
        "🗜️ Mode compression activé\n\n"
        "Envoie la vidéo à compresser."
    )

    user_sessions[message.from_user.id] = {'mode': 'compress_waiting_file'}


@pyrogram_app.on_message(filters.document | filters.video)
async def handle_file(client, message):
    """Reçoit fichiers"""
    if not is_authorized(message.from_user.id):
        return

    file = message.document or message.video
    filename = file.file_name or "file"
    user_id = message.from_user.id
    session = user_sessions.get(user_id, {})

    # ── Mode compression ──────────────────────────────────────────────────
    if session.get('mode') == 'compress_waiting_file':
        # Vérifie que c'est bien une vidéo
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv'):
            await message.reply_text("❌ Format non supporté. Envoie un fichier vidéo (mp4, mkv, avi, mov, webm, flv).")
            return

        # Propose les options de qualité
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

        await message.reply_text(
            f"📹 Fichier: `{filename}`\n\n"
            f"Choisis la qualité de compression:",
            reply_markup=buttons
        )
        return

    # ── Mode renommage (comportement par défaut) ──────────────────────────
    await message.reply_text(
        f"📝 Fichier: `{filename}`\n\n"
        f"Nouveau nom (avec ou sans extension):"
    )

    user_sessions[user_id] = {
        'mode': 'rename',
        'message': message,
        'filename': filename,
        'original_ext': os.path.splitext(filename)[1].lower()
    }


@pyrogram_app.on_message(filters.text)
async def handle_text(client, message):
    """Reçoit texte (nouveau nom)"""
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

    # Boutons d'envoi
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 Document", callback_data=f"send_doc_{user_id}"),
            InlineKeyboardButton("🎬 Vidéo", callback_data=f"send_vid_{user_id}")
        ]
    ])

    session['new_name'] = new_name

    await message.reply_text(
        f"📝 {new_name}\n\n"
        f"Envoyer comme?",
        reply_markup=buttons
    )


@pyrogram_app.on_callback_query()
async def handle_callback(client, callback):
    """Gère tous les callbacks"""
    user_id = callback.from_user.id
    session = user_sessions.get(user_id, {})

    if not session:
        await callback.answer("Session expirée, recommence.")
        return

    # ── Callback compression : choix de qualité ───────────────────────────
    if callback.data.startswith("compress_"):
        parts = callback.data.split("_")  # compress_<quality>_<user_id>
        quality = parts[1]  # high / medium / low

        await callback.edit_message_text(
            f"⏳ Compression en cours ({quality})...\n"
            f"Ça peut prendre quelques minutes."
        )

        original_msg = session['message']
        filename = session['filename']
        ext = session['original_ext']

        # Télécharge le fichier depuis Telegram
        local_path = f"/tmp/{user_id}_{filename}"
        try:
            await client.download_media(original_msg, file_name=local_path)
        except Exception as e:
            await callback.edit_message_text(f"❌ Erreur téléchargement: {e}")
            return

        # Compresse via FFmpeg Micro
        try:
            compressed_path = await ffmpeg_micro.compress_video(
                file_path=local_path,
                api_keys=config.FFMPEG_MICRO_API_KEYS,
                key_index=next_ffmpeg_key(),
                quality=quality,
                resolution="1080p",
                output_format="mp4"
            )
        except Exception as e:
            await callback.edit_message_text(f"❌ Erreur compression: {e}")
            _cleanup(local_path)
            return

        # Renvoie le fichier compressé
        try:
            await callback.edit_message_text("📤 Envoi du fichier compressé...")
            compressed_filename = os.path.splitext(filename)[0] + "_compressed.mp4"
            await client.send_document(
                callback.message.chat.id,
                compressed_path,
                caption=f"✅ Compressé ({quality}): {compressed_filename}"
            )
            await callback.edit_message_text("✅ Compression terminée!")
        except Exception as e:
            await callback.edit_message_text(f"❌ Erreur envoi: {e}")
        finally:
            _cleanup(local_path, compressed_path)

        del user_sessions[user_id]
        return

    # ── Callback renommage : envoi doc ou vidéo ───────────────────────────
    if 'new_name' not in session:
        await callback.answer("Session expirée, recommence.")
        return

    is_video = 'vid' in callback.data

    try:
        await callback.edit_message_text("📤 Envoi...")

        original_msg = session['message']
        file_id = (original_msg.video or original_msg.document).file_id

        if is_video:
            await client.send_video(
                callback.message.chat.id,
                file_id,
                caption=session['new_name']
            )
        else:
            await client.send_document(
                callback.message.chat.id,
                file_id,
                caption=session['new_name']
            )

        await callback.edit_message_text("✅ Fait!")
        del user_sessions[user_id]

    except Exception as e:
        await callback.edit_message_text(f"❌ Erreur: {e}")


def _cleanup(*paths):
    """Supprime les fichiers temporaires"""
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


if __name__ == '__main__':
    print("✅ Bot Pyrogram en cours de démarrage...")
    print("=" * 50)
    pyrogram_app.run()
