import os
import sys
import shutil
import subprocess
import tempfile
import traceback
import logging
from pathlib import Path

import telebot
from telebot.types import Message


# ============================================================
# Telegram video-to-circle bot
# Production-ready version for Docker/Railway/Render-like hosts
# ============================================================

TOKEN = os.getenv("TOKEN")
DEBUG_ERRORS = os.getenv("DEBUG_ERRORS", "0") == "1"

MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_SECONDS", "60"))
OUTPUT_SIZE = int(os.getenv("OUTPUT_SIZE", "640"))


# ----------------------------
# Logging
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("video-note-bot")


# ----------------------------
# Startup checks
# ----------------------------
def require_env() -> None:
    if not TOKEN:
        raise RuntimeError(
            "TOKEN не знайдено. Додай змінну середовища TOKEN у налаштуваннях сервера."
        )


def require_ffmpeg() -> str:
    ffmpeg_path = shutil.which("ffmpeg")

    if not ffmpeg_path:
        raise RuntimeError(
            "ffmpeg не знайдено в системі. "
            "Для Docker використовуй Dockerfile з командою: "
            "apt-get update && apt-get install -y ffmpeg"
        )

    logger.info("FFMPEG PATH: %s", ffmpeg_path)

    version_result = subprocess.run(
        ["ffmpeg", "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    first_line = version_result.stdout.splitlines()[0] if version_result.stdout else "unknown"
    logger.info("FFMPEG VERSION: %s", first_line)

    return ffmpeg_path


require_env()
require_ffmpeg()

bot = telebot.TeleBot(TOKEN, parse_mode=None)


# ----------------------------
# Helpers
# ----------------------------
def run_ffmpeg(input_file: str, output_file: str) -> None:
    """
    Converts any supported video into Telegram video note format:
    - square crop from center
    - 640x640
    - max 60 seconds
    - H.264 video
    - AAC audio if source has audio
    - yuv420p for compatibility
    """

    crop_filter = (
        "crop=min(iw\\,ih):min(iw\\,ih):"
        "(iw-min(iw\\,ih))/2:(ih-min(iw\\,ih))/2,"
        f"scale={OUTPUT_SIZE}:{OUTPUT_SIZE},"
        "setsar=1,"
        "format=yuv420p"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",

        "-i", input_file,

        # Telegram video notes should be short.
        "-t", str(MAX_DURATION_SECONDS),

        # Main video stream and optional audio stream.
        "-map", "0:v:0",
        "-map", "0:a?",

        "-vf", crop_filter,

        # Video settings.
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",

        # Audio settings. If no audio exists, optional mapping prevents crash.
        "-c:a", "aac",
        "-b:a", "128k",

        # Better MP4 compatibility.
        "-movflags", "+faststart",

        output_file,
    ]

    logger.info("Running ffmpeg command: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        logger.error("FFMPEG STDOUT:\n%s", result.stdout)
        logger.error("FFMPEG STDERR:\n%s", result.stderr)
        raise RuntimeError("ffmpeg не зміг обробити це відео")

    if not os.path.exists(output_file):
        raise RuntimeError("ffmpeg завершився без помилки, але output.mp4 не створився")

    if os.path.getsize(output_file) == 0:
        raise RuntimeError("ffmpeg створив порожній output.mp4")


def get_video_file_id(message: Message) -> tuple[str, str]:
    """
    Supports:
    - normal Telegram video
    - video sent as document/file
    """

    if message.video:
        return message.video.file_id, "video.mp4"

    if message.document:
        mime_type = message.document.mime_type or ""

        if not mime_type.startswith("video/"):
            raise RuntimeError("Це не відеофайл. Надішли відео, не фото і не архів.")

        return message.document.file_id, "document_video.mp4"

    raise RuntimeError("Надішли відео файлом або звичайним відео")


def download_telegram_file(file_id: str, destination: str) -> None:
    logger.info("Getting Telegram file info...")
    file_info = bot.get_file(file_id)

    logger.info("Downloading Telegram file: %s", file_info.file_path)
    downloaded_file = bot.download_file(file_info.file_path)

    with open(destination, "wb") as f:
        f.write(downloaded_file)

    if not os.path.exists(destination) or os.path.getsize(destination) == 0:
        raise RuntimeError("Відео не завантажилося або файл порожній")

    logger.info("Input file saved: %s bytes", os.path.getsize(destination))


def user_error_text(error: Exception) -> str:
    base = str(error)

    if DEBUG_ERRORS:
        return f"Помилка: {base}"

    if "ffmpeg не знайдено" in base:
        return "Помилка сервера: ffmpeg не встановлений. Перевір Dockerfile і зроби rebuild."

    if "ffmpeg не зміг" in base:
        return "Не зміг обробити це відео. Спробуй коротше або інший формат MP4."

    if "TOKEN" in base:
        return "Помилка сервера: TOKEN не налаштований."

    return "Помилка. Спробуй інше відео або коротший файл."


# ----------------------------
# Handlers
# ----------------------------
@bot.message_handler(commands=["start"])
def start(message: Message) -> None:
    bot.send_message(
        message.chat.id,
        "Кидай відео — зроблю кружок ✅\n\n"
        "Краще надсилати MP4 до 60 секунд. Якщо відео довше — візьму перші 60 секунд."
    )


@bot.message_handler(commands=["health"])
def health(message: Message) -> None:
    ffmpeg_path = shutil.which("ffmpeg")

    text = (
        "✅ Бот живий\n"
        f"ffmpeg: {ffmpeg_path or 'НЕ ЗНАЙДЕНО'}\n"
        f"max duration: {MAX_DURATION_SECONDS}s\n"
        f"output size: {OUTPUT_SIZE}x{OUTPUT_SIZE}"
    )

    bot.send_message(message.chat.id, text)


@bot.message_handler(content_types=["video", "document"])
def handle_video(message: Message) -> None:
    try:
        bot.send_message(message.chat.id, "Обробляю відео... ⏳")

        file_id, original_name = get_video_file_id(message)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = str(Path(temp_dir) / original_name)
            output_file = str(Path(temp_dir) / "circle.mp4")

            download_telegram_file(file_id, input_file)
            run_ffmpeg(input_file, output_file)

            logger.info("Output file size: %s bytes", os.path.getsize(output_file))

            with open(output_file, "rb") as video:
                bot.send_video_note(
                    chat_id=message.chat.id,
                    video_note=video,
                    duration=MAX_DURATION_SECONDS,
                    length=OUTPUT_SIZE,
                )

            logger.info("Video note sent successfully")

    except Exception as e:
        logger.error("BOT ERROR:\n%s", traceback.format_exc())
        bot.reply_to(message, user_error_text(e))


@bot.message_handler(func=lambda message: True)
def fallback(message: Message) -> None:
    bot.send_message(message.chat.id, "Надішли відео — я зроблю з нього кружок ✅")


# ----------------------------
# Main
# ----------------------------
if __name__ == "__main__":
    logger.info("Deleting webhook and dropping pending updates...")
    bot.delete_webhook(drop_pending_updates=True)

    logger.info("✅ Bot started")
    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
        logger_level=logging.INFO,
    )
