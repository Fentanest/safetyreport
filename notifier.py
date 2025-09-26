import asyncio
import sys
import telegram
import settings.settings as settings

async def send_message(text):
    """Sends a message to the predefined chat_id."""
    if not settings.telegram_token or not settings.chat_id:
        print("TELEGRAM_TOKEN or CHAT_ID is not set in settings.")
        return
    
    bot = telegram.Bot(token=settings.telegram_token)
    await bot.send_message(chat_id=settings.chat_id, text=text)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        message = sys.argv[1]
        asyncio.run(send_message(message))
    else:
        print("Usage: python notifier.py <message>")
