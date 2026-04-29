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
from telebot.apihelper import ApiTelegramException


# ============================================================
# Telegram video-to-circle bot
# Docker-ready / production-oriented version
# ============================================================

TOKEN = os.getenv("TOKEN")
DEBUG_ERRORS = os.getenv("DEBUG_ERRORS", "0") == "1"

# Safer than 60. Telegram video notes can be picky near the 60s limit.
MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_SECONDS", "59"))
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


def require_binary(name: str) -> str:
    path = shutil.which(name)

    if not path:
        raise RuntimeError(
            f"{name} не знайдено в системі. "
            "Перевір Dockerfile і зроби повний rebuild, не restart."
        )

    logger.info("%s PATH: %s", name.upper(), path)
    return path


def log_ffmpeg_version() -> None:
    result = subprocess.run(
        ["ffmpeg", "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    first_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
    logger.info("FFMPEG VERSION: %s", first_line)


require_env()
require_binary("ffmpeg")
require_binary("ffprobe")
log_ffmpeg_version()

bot = telebot.TeleBot(TOKEN, parse_mode=None)


# ----------------------------
# Helpers
# ----------------------------
def get_media_file_id(message: Message) -> tuple[str, str]:
    """
    Supports:
    - normal Telegram video
    - video sent as document/file
    """

    if message.video:
        return message.video.file_id, "input_video.mp4"

    if message.document:
        mime_type = message.document.mime_type or ""

        if not mime_type.startswith("video/"):
            raise RuntimeError("Це не відеофайл. Надішли саме відео.")

        return message.document.file_id, "input_document_video.mp4"

    raise RuntimeError("Надішли відео файлом або звичайним відео.")


def download_telegram_file(file_id: str, destination: str) -> None:
    logger.info("Getting Telegram file info...")
    file_info = bot.get_file(file_id)

    logger.info("Downloading Telegram file: %s", file_info.file_path)
    downloaded_file = bot.download_file(file_info.file_path)

    with open(destination, "wb") as f:
        f.write(downloaded_file)

    size = os.path.getsize(destination) if os.path.exists(destination) else 0

    if size == 0:
        raise RuntimeError("Відео не завантажилося або файл порожній.")

    logger.info("Input file saved: %s bytes", size)


def get_video_duration_seconds(path: str) -> int | None:
    """
    Reads real output duration using ffprobe.
    Returns None if ffprobe cannot read duration.
    """

    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        logger.warning("ffprobe failed: %s", result.stderr)
        return None

    try:
        duration_float = float(result.stdout.strip())
        duration_int = max(1, int(round(duration_float)))
        logger.info("Output duration: %s seconds", duration_int)
        return min(duration_int, MAX_DURATION_SECONDS)
    except Exception:
        logger.warning("Could not parse ffprobe duration: %r", result.stdout)
        return None


def run_ffmpeg(input_file: str, output_file: str) -> None:
    """
    Converts input video into Telegram video note friendly MP4:
    - square center crop
    - 640x640
    - max 59 seconds by default
    - H.264
    - yuv420p
    - AAC audio if source has audio
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

        "-t", str(MAX_DURATION_SECONDS),

        "-map", "0:v:0",
        "-map", "0:a?",

        "-vf", crop_filter,

        "-r", "30",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-profile:v", "main",
        "-level", "3.1",

        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",

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
        raise RuntimeError("ffmpeg не зміг обробити це відео.")

    if not os.path.exists(output_file):
        raise RuntimeError("ffmpeg завершився без помилки, але output файл не створився.")

    output_size = os.path.getsize(output_file)

    if output_size == 0:
        raise RuntimeError("ffmpeg створив порожній output файл.")

    logger.info("Output file size: %s bytes", output_size)


def send_circle(chat_id: int, output_file: str) -> None:
    """
    IMPORTANT:
    pyTelegramBotAPI uses the second positional argument as video note data.
    Do NOT use keyword video_note=... here.
    """

    duration = get_video_duration_seconds(output_file)

    with open(output_file, "rb") as video:
        if duration:
            bot.send_video_note(
                chat_id,
                video,
                duration=duration,
                length=OUTPUT_SIZE,
                timeout=120,
            )
        else:
            bot.send_video_note(
                chat_id,
                video,
                length=OUTPUT_SIZE,
                timeout=120,
            )


def user_error_text(error: Exception) -> str:
    base = str(error)

    if DEBUG_ERRORS:
        return f"Помилка: {base}"

    if "ffmpeg" in base and "не знайдено" in base:
        return "Помилка сервера: ffmpeg не встановлений. Перевір Dockerfile і зроби rebuild."

    if "ffmpeg не зміг" in base:
        return "Не зміг обробити це відео. Спробуй коротше або інший MP4."

    if "TOKEN" in base:
        return "Помилка сервера: TOKEN не налаштований."

    if isinstance(error, ApiTelegramException):
        return "Telegram не прийняв готовий кружок. Спробуй коротше відео або інший MP4."

    if "send_video_note" in base:
        return "Помилка відправки кружка. Перевір версію pyTelegramBotAPI або онови requirements."

    return "Помилка. Спробуй інше відео або коротший файл."


# ----------------------------
# Handlers
# ----------------------------
@bot.message_handler(commands=["start"])
def start(message: Message) -> None:
    bot.send_message(
        message.chat.id,
        "Кидай відео — зроблю кружок ✅\n\n"
        "Краще MP4 до 60 секунд. Якщо довше — візьму перші 59 секунд."
    )


@bot.message_handler(commands=["health"])
def health(message: Message) -> None:
    text = (
        "✅ Бот живий\n"
        f"ffmpeg: {shutil.which('ffmpeg') or 'НЕ ЗНАЙДЕНО'}\n"
        f"ffprobe: {shutil.which('ffprobe') or 'НЕ ЗНАЙДЕНО'}\n"
        f"max duration: {MAX_DURATION_SECONDS}s\n"
        f"output size: {OUTPUT_SIZE}x{OUTPUT_SIZE}"
    )

    bot.send_message(message.chat.id, text)


@bot.message_handler(content_types=["video", "document"])
def handle_video(message: Message) -> None:
    try:
        bot.send_message(message.chat.id, "Обробляю відео... ⏳")

        file_id, input_name = get_media_file_id(message)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = str(Path(temp_dir) / input_name)
            output_file = str(Path(temp_dir) / "circle.mp4")

            download_telegram_file(file_id, input_file)
            run_ffmpeg(input_file, output_file)
            send_circle(message.chat.id, output_file)

            logger.info("Video note sent successfully")

    except Exception as e:
        logger.error("BOT ERROR:\n%s", traceback.format_exc())

        try:
            bot.reply_to(message, user_error_text(e))
        except Exception:
            logger.error("Could not send error message to user:\n%s", traceback.format_exc())


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
