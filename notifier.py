import asyncio
import sys
import telegram
import settings.settings as settings

async def send_message(text):
    """Sends a message to the predefined chat_id."""
    if not settings.telegram_enabled:
        print("Telegram 기능이 비활성화되어 알림을 보내지 않습니다.")
        return
    
    bot = telegram.Bot(token=settings.telegram_token)
    await bot.send_message(chat_id=settings.chat_id, text=text)

if __name__ == "__main__":
    if not settings.telegram_enabled:
        # Silently exit if Telegram is disabled, as this is called by other scripts.
        sys.exit(0)

    if len(sys.argv) > 1:
        message = sys.argv[1]
        asyncio.run(send_message(message))
    else:
        print("Usage: python notifier.py <message>")
