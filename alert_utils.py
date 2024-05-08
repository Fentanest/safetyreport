import datetime
import asyncio
import telegram
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import settings.settings as settings

token = settings.telegram_token
id = settings.chat_id


class TelegramBotHandler:
    @classmethod
    async def help(cls, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = "안전신문고 크롤링이 완료되면 메세지로 알려드립니다."
        await update.message.reply_text(msg)

def main():
    try:
        application = ApplicationBuilder().token(token).build()
        application.add_handler(CommandHandler('help', TelegramBotHandler.help))
        application.run_polling()
    except KeyboardInterrupt:
        return True

async def result(letter):
    bot = telegram.Bot(token)
    msg = f'''{str(datetime.datetime.now())[:19]}\n
                {letter}'''
    async with bot:
        await bot.send_message(text=msg, chat_id=id)

# if __name__ == '__main__':
    # main()  
