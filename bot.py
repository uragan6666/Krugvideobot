import telebot
import os
import subprocess

TOKEN = os.getenv('TOKEN')

bot = telebot.TeleBot(TOKEN)

# Видаляємо старий webhook (обов'язково після створення бота!)
bot.delete_webhook(drop_pending_updates=True)

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, 'Кидай відео — зроблю кружок ✅')

@bot.message_handler(content_types=['video'])
def handle_video(message):
    try:
        # Завантажуємо відео
        file_info = bot.get_file(message.video.file_id)
        downloaded = bot.download_file(file_info.file_path)

        input_file = f'input_{message.video.file_id}.mp4'
        output_file = f'output_{message.video.file_id}.mp4'

        with open(input_file, 'wb') as f:
            f.write(downloaded)

        # Обрізаємо в квадрат і робимо 640x640
        cmd = [
            'ffmpeg', '-i', input_file,
            '-vf', "crop=min(iw\\,ih):min(iw\\,ih):(iw-min(iw\\,ih))/2:(ih-min(iw\\,ih))/2,scale=640:640",
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k',
            '-y', output_file
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise Exception("ffmpeg error: " + result.stderr)

        # Відправляємо кружок
        with open(output_file, 'rb') as video:
            bot.send_video_note(message.chat.id, video)

        # Чистимо файли
        os.remove(input_file)
        os.remove(output_file)

    except Exception as e:
        bot.reply_to(message, 'Помилка. Спробуй інше відео')

print("✅ Бот запущений і працює")
bot.infinity_polling()
