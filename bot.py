import os
import subprocess
import tempfile
import traceback

import telebot


TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise RuntimeError("❌ TOKEN не знайдено. Додай TOKEN у змінні середовища.")

bot = telebot.TeleBot(TOKEN)

# Важливо, якщо бот раніше працював через webhook
bot.delete_webhook(drop_pending_updates=True)


@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        "Кидай відео — зроблю кружок ✅\n\nБажано до 60 секунд."
    )


@bot.message_handler(content_types=["video"])
def handle_video(message):
    try:
        bot.send_message(message.chat.id, "Обробляю відео... ⏳")

        file_info = bot.get_file(message.video.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = os.path.join(temp_dir, "input.mp4")
            output_file = os.path.join(temp_dir, "output.mp4")

            with open(input_file, "wb") as f:
                f.write(downloaded_file)

            cmd = [
                "ffmpeg",
                "-y",
                "-i", input_file,

                # максимум 60 секунд для кружка
                "-t", "60",

                # беремо відео, аудіо якщо є
                "-map", "0:v:0",
                "-map", "0:a?",

                # квадратний crop з центру + 640x640
                "-vf",
                "crop=min(iw\\,ih):min(iw\\,ih):(iw-min(iw\\,ih))/2:(ih-min(iw\\,ih))/2,"
                "scale=640:640,setsar=1,format=yuv420p",

                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",

                "-c:a", "aac",
                "-b:a", "128k",

                "-movflags", "+faststart",

                output_file
            ]

            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            if result.returncode != 0:
                print("FFMPEG ERROR:")
                print(result.stderr)
                raise RuntimeError("ffmpeg не зміг обробити відео")

            if not os.path.exists(output_file):
                raise RuntimeError("output.mp4 не створився")

            with open(output_file, "rb") as video:
                bot.send_video_note(
                    message.chat.id,
                    video,
                    length=640,
                    duration=60
                )

    except Exception as e:
        print("BOT ERROR:")
        print(traceback.format_exc())
        bot.reply_to(message, f"Помилка: {e}")


print("✅ Бот запущений і працює")
bot.infinity_polling(skip_pending=True)
