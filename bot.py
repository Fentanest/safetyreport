import re
import logging
import os
import subprocess
import sys

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

# DB and settings imports
from sqlalchemy import create_engine
import settings.settings as settings
import items
import logger

# State definitions for conversation
ASK_CAR_NUMBER = range(1)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /start is issued."""
    await update.message.reply_text("안녕하세요! 안전신문고 크롤러 봇입니다. /help 를 입력하여 메뉴를 확인하세요.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the main menu."""
    keyboard = [
        [InlineKeyboardButton("1. 크롤링 시작", callback_data="start_crawl")],
        [InlineKeyboardButton("2. 크롤링(min) 시작", callback_data="start_crawl_min")],
        [InlineKeyboardButton("3. 차량검색", callback_data="search_car")],
        [InlineKeyboardButton("4. 엑셀만 저장하기", callback_data="save_excel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("원하시는 작업을 선택하세요:", reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parses the CallbackQuery and starts the corresponding action."""
    query = update.callback_query
    await query.answer()

    if query.data == "start_crawl":
        await query.edit_message_text(text="전체 크롤링 프로세스를 시작합니다. 완료되면 알려드리겠습니다...")
        # Run start.py as a subprocess
        process = subprocess.Popen([sys.executable, "start.py"])
        process.wait() # Wait for the subprocess to finish
        if process.returncode == 0:
            await context.bot.send_message(chat_id=query.message.chat_id, text="크롤링 및 모든 작업이 완료되었습니다.")
        else:
            logger.LoggerFactory.get_logger().error(f"Error running start.py. Exit code: {process.returncode}")
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"크롤링 중 오류가 발생했습니다. 자세한 내용은 로그를 확인해주세요.")
        return ConversationHandler.END

    elif query.data == "start_crawl_min":
        await query.edit_message_text(text="크롤링(min) 프로세스를 시작합니다. 완료되면 알려드리겠습니다...")
        process = subprocess.Popen([sys.executable, "start.py", "--min"])
        process.wait() # Wait for the subprocess to finish
        if process.returncode == 0:
            await context.bot.send_message(chat_id=query.message.chat_id, text="크롤링(min) 및 모든 작업이 완료되었습니다.")
        else:
            logger.LoggerFactory.get_logger().error(f"Error running start.py --min. Exit code: {process.returncode}")
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"크롤링(min) 중 오류가 발생했습니다. 자세한 내용은 로그를 확인해주세요.")
        return ConversationHandler.END

    elif query.data == "save_excel":
        await query.edit_message_text(text="`debug_save.py`를 실행하여 엑셀 저장을 시작합니다...")
        # Run debug_save.py as a subprocess
        process = subprocess.Popen([sys.executable, "debug_save.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        if process.returncode == 0:
            response = stdout.decode('utf-8')
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"엑셀 저장 완료.\n\n{response}")
        else:
            error_message = stderr.decode('utf-8')
            logger.LoggerFactory.get_logger().error(f"Error running debug_save.py: {error_message}")
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"오류가 발생했습니다:\n{error_message}")
        return ConversationHandler.END

    elif query.data == "search_car":
        await query.edit_message_text(text="검색할 차량번호를 입력하세요. 취소하려면 /cancel 을 입력하세요.")
        return ASK_CAR_NUMBER

    return ConversationHandler.END

async def receive_car_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the car number, searches the DB, and returns the results."""
    # Remove all whitespace from user input
    car_number = re.sub(r'\s+', '', update.message.text)
    await update.message.reply_text(f"차량번호 '{car_number}'에 대한 신고 내역을 검색합니다...")

    try:
        engine = create_engine(
            f'sqlite:///{settings.db_path}',
            connect_args={'timeout': 15}
        )
        results = items.search_by_car_number(engine, car_number)

        if not results:
            await update.message.reply_text("해당 차량번호에 대한 신고 내역을 찾을 수 없습니다.")
            return ConversationHandler.END

        response_parts = [f"총 {len(results)}건의 신고 내역을 찾았습니다.\n\n"]
        for i, row in enumerate(results):
            part = f"""--- [결과 {i+1}] ---\n차량번호: {row.get('차량번호', 'N/A')}\n신고번호: {row.get('신고번호', 'N/A')}\n신고일: {row.get('신고일', 'N/A')}\n발생일: {row.get('발생일자', 'N/A')}\n위반법규: {row.get('위반법규', 'N/A')}\n처리상태: {row.get('처리상태', 'N/A')}\n범칙금/과태료: {row.get('범칙금_과태료', 'N/A')}\n처리기관: {row.get('처리기관', 'N/A')}\n담당자: {row.get('담당자', 'N/A')}\n\n"""
            response_parts.append(part)
        response_message = "".join(response_parts)
        
        # Telegram message length limit is 4096 characters
        if len(response_message) > 4096:
            await update.message.reply_text("결과가 너무 길어 일부만 표시합니다.")
            # Split message into chunks
            for i in range(0, len(response_message), 4096):
                await update.message.reply_text(response_message[i:i+4096])
        else:
            await update.message.reply_text(response_message)

    except Exception as e:
        logger.LoggerFactory.get_logger().error(f"Error during car number search: {e}")
        await update.message.reply_text(f"차량번호 검색 중 오류가 발생했습니다: {e}")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text("작업을 취소했습니다.")
    return ConversationHandler.END

def main() -> None:
    """Run the bot."""
    logger.LoggerFactory.create_logger()
    # Get the token from environment variable
    token = settings.telegram_token
    if not token:
        logger.LoggerFactory.get_logger().error("TELEGRAM_TOKEN 환경변수가 설정되지 않았습니다.")
        return

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(token).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # Add conversation handler for menu buttons and car search
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button)],
        states={
            ASK_CAR_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_car_number)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)

    # Run the bot until the user presses Ctrl-C
    application.run_polling()

if __name__ == "__main__":
    main()