import telebot
import os
import subprocess

TOKEN = os.getenv('TOKEN')   # ← ось тут зміна
bot.delete_webhook(drop_pending_updates=True)
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, 'Кидай відео — зроблю кружок')

@bot.message_handler(content_types=['video'])
def handle_video(message):
    try:
        file_info = bot.get_file(message.video.file_id)
        downloaded = bot.download_file(file_info.file_path)

        input_file = f'input_{message.video.file_id}.mp4'
        output_file = f'output_{message.video.file_id}.mp4'

        with open(input_file, 'wb') as f:
            f.write(downloaded)

        cmd = [
            'ffmpeg', '-i', input_file,
            '-vf', "crop=min(iw\\,ih):min(iw\\,ih):(iw-min(iw\\,ih))/2:(ih-min(iw\\,ih))/2,scale=640:640",
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k',
            '-y', output_file
        ]
        subprocess.call(cmd)

        with open(output_file, 'rb') as video:
            bot.send_video_note(message.chat.id, video)

        os.remove(input_file)
        os.remove(output_file)

    except:
        bot.reply_to(message, 'Помилка. Спробуй інше відео')

bot.infinity_polling()
