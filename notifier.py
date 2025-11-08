import asyncio
import sys
import telegram
import settings.settings as settings

async def send_message(bot, text):
    """Sends a single message to the predefined chat_id."""
    if not text or not text.strip():
        return
    await bot.send_message(chat_id=settings.chat_id, text=text)

async def main():
    """Main function to parse args and send message(s)."""
    if not settings.telegram_enabled:
        sys.exit(0)

    if len(sys.argv) < 2:
        print("Usage: python notifier.py <message>")
        sys.exit(1)

    message = sys.argv[1]
    bot = telegram.Bot(token=settings.telegram_token)

    max_len = 4096
    if len(message) > max_len:
        # Try smart chunking first
        if "--- [" in message:
            try:
                header = message.split('\n\n', 1)[0]
                await send_message(bot, f"{header}\n(내용이 너무 길어 여러 개로 나누어 보냅니다.)")
                
                content = message.split('\n\n', 1)[1]
                chunks = []
                current_chunk = ""
                
                for part in content.split("--- ["):
                    if not part.strip():
                        continue
                    
                    part_to_add = "--- [" + part
                    if len(current_chunk) + len(part_to_add) > max_len:
                        if current_chunk:
                            chunks.append(current_chunk)
                        current_chunk = part_to_add
                    else:
                        current_chunk += part_to_add
                
                if current_chunk:
                    chunks.append(current_chunk)

                for chunk in chunks:
                    await send_message(bot, chunk)
                return
            except Exception:
                # Fallback to simple chunking if smart chunking fails
                pass

        # Fallback: simple character-based chunking
        for i in range(0, len(message), max_len):
            chunk = message[i:i+max_len]
            await send_message(bot, chunk)
    else:
        await send_message(bot, message)

if __name__ == "__main__":
    asyncio.run(main())