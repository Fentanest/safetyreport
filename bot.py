import re
import asyncio
from functools import wraps
import sys

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

# DB and settings imports
import settings.settings as settings
from core.database import database
from core.database.engine import get_engine_with_timeout
from core.utils import logger
from core.utils import message_formatter
import warnings
import os

is_frozen = getattr(sys, 'frozen', False)

# 무시할 PTB 경고: ConversationHandler에서 per_message=False와 CallbackQueryHandler 조합 시 발생하는 경고
warnings.filterwarnings("ignore", message="If 'per_message=False', 'CallbackQueryHandler' will not be tracked for every message.*")

# State definitions for conversation
ASK_CAR_NUMBER, ASK_REPORT_NUMBER = range(2)


def authorized_update(update):
    chat, user = update.effective_chat, update.effective_user
    if chat is None or user is None:
        return False
    try:
        configured_chat = int(settings.chat_id)
    except (TypeError, ValueError):
        return False
    if chat.id != configured_chat:
        return False
    configured_users = settings.config.get('TELEGRAM', 'allowed_user_ids', fallback='')
    allowed_users = {int(value.strip()) for value in configured_users.split(',')
                     if value.strip().isdigit()}
    if chat.type == 'private':
        return user.id in (allowed_users or {configured_chat})
    return bool(allowed_users) and user.id in allowed_users


def authorized(handler):
    @wraps(handler)
    async def checked(update, context):
        if not authorized_update(update):
            if update.callback_query:
                await update.callback_query.answer('허용되지 않은 요청입니다.', show_alert=True)
            return ConversationHandler.END
        return await handler(update, context)
    return checked


@authorized
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /start is issued."""
    await update.message.reply_text("안녕하세요! 안전신문고 크롤러 봇입니다. /ㅇ 를 입력하여 메뉴를 확인하세요.")

@authorized
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the main menu."""
    keyboard = [
        [InlineKeyboardButton("1. 크롤링 시작", callback_data="start_crawl")],
        [InlineKeyboardButton("2. 차량검색", callback_data="search_car")],
        [InlineKeyboardButton("3. 신고번호 검색", callback_data="search_report_number")],
        [InlineKeyboardButton("4. 엑셀만 저장하기", callback_data="save_excel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("원하시는 작업을 선택하세요:", reply_markup=reply_markup)

@authorized
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parses the CallbackQuery and starts the corresponding action."""
    query = update.callback_query
    await query.answer()

    if query.data in ('start_crawl', 'save_excel'):
        if not context.application.bot_data.get('managed_coordinator'):
            await query.edit_message_text('서버에서 관리하는 봇으로 다시 시도하세요.')
            return ConversationHandler.END
        try:
            if query.data == 'start_crawl':
                from services import crawl_control
                await asyncio.to_thread(crawl_control.start_crawl, crawl_mode='full',
                                        header='=== [텔레그램에서 시작된 크롤링] ===',
                                        broadcast_source='telegram')
                await query.edit_message_text('크롤링을 시작했습니다. 진행 상태는 서버에서 확인하세요.')
            else:
                from services import export_service
                def save_excel():
                    return export_service.export_results(get_engine_with_timeout(15),
                                                         save_excel=True, save_sheet=False)
                saved = await asyncio.to_thread(save_excel)
                await query.edit_message_text('엑셀 저장 완료.' if saved else '저장할 신고 내역이 없습니다.')
        except Exception as exc:
            logger.LoggerFactory.get_logger().warning('봇 작업 접수 실패: %s', type(exc).__name__)
            await query.edit_message_text('작업을 시작하지 못했습니다. 서버 상태와 로그를 확인하세요.')
        return ConversationHandler.END

    elif query.data == "search_car":
        await query.edit_message_text(text="검색할 차량번호를 입력하세요. 취소하려면 /cancel 을 입력하세요.")
        return ASK_CAR_NUMBER

    elif query.data == "search_report_number":
        await query.edit_message_text(text="검색할 신고번호를 입력하세요. 취소하려면 /cancel 을 입력하세요.")
        return ASK_REPORT_NUMBER

    return ConversationHandler.END

@authorized
async def receive_car_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the car number, searches the DB, and returns the results."""
    car_number = re.sub(r'\s+', '', update.message.text)
    await update.message.reply_text(f"차량번호 '{car_number}'에 대한 신고 내역을 검색합니다...")

    try:
        engine = get_engine_with_timeout(15)
        results = await asyncio.to_thread(database.search_by_car_number, engine, car_number)

        if not results:
            await update.message.reply_text("해당 차량번호에 대한 신고 내역을 찾을 수 없습니다.")
            return ConversationHandler.END

        title = f"총 {len(results)}건의 신고 내역을 찾았습니다."
        response_message = message_formatter.format_report_list(results, title)
        
        await message_formatter.send_message_in_chunks(context.bot, update.message.chat_id, response_message)

    except Exception as e:
        logger.LoggerFactory.get_logger().error(f"Error during car number search: {e}")
        await update.message.reply_text(f"차량번호 검색 중 오류가 발생했습니다: {e}")

    return ConversationHandler.END

@authorized
async def receive_report_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the report number, searches the DB, and returns the results."""
    report_number = re.sub(r'\s+', '', update.message.text)
    await update.message.reply_text(f"신고번호 '{report_number}'에 대한 신고 내역을 검색합니다...")

    try:
        engine = get_engine_with_timeout(15)
        results = await asyncio.to_thread(database.search_by_report_number, engine, report_number)

        if not results:
            await update.message.reply_text("해당 신고번호에 대한 신고 내역을 찾을 수 없습니다.")
            return ConversationHandler.END

        title = f"총 {len(results)}건의 신고 내역을 찾았습니다."
        response_message = message_formatter.format_report_list(results, title)

        await message_formatter.send_message_in_chunks(context.bot, update.message.chat_id, response_message)

    except Exception as e:
        logger.LoggerFactory.get_logger().error(f"Error during report number search: {e}")
        await update.message.reply_text(f"신고번호 검색 중 오류가 발생했습니다: {e}")

    return ConversationHandler.END

@authorized
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text("작업을 취소했습니다.")
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """NetworkError 등 일시적 오류는 DEBUG로 기록하고 무시."""
    from telegram.error import NetworkError, TimedOut
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.LoggerFactory.get_logger().debug(f"텔레그램 일시적 네트워크 오류 (자동 재시도): {context.error}")
    else:
        logger.LoggerFactory.get_logger().error(f"텔레그램 봇 오류: {context.error}", exc_info=context.error)

def build_application(*, managed=False):
    application = Application.builder().token(settings.telegram_token).build()
    application.bot_data['managed_coordinator'] = managed
    # Create the Application and pass it your bot's token.

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("h", help_command))
    
    # 한글 명령어 'ㅇ'는 CommandHandler에서 지원하지 않으므로 MessageHandler로 처리
    application.add_handler(MessageHandler(filters.Regex(r'^/?[ㅇ]$'), help_command))

    # Add conversation handler for menu buttons and car search
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button)],
        states={
            ASK_CAR_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_car_number)],
            ASK_REPORT_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_report_number)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False, # 메세지 단위가 아닌 채팅 단위로 상태 관리 (경고 해결)
    )

    application.add_handler(conv_handler)
    application.add_error_handler(error_handler)

    return application


async def start_managed():
    application = build_application(managed=True)
    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
    except BaseException:
        await stop_managed(application)
        raise
    return application


async def stop_managed(application, timeout=10):
    import asyncio
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0, timeout)
    failures = []
    callbacks = []
    if application.updater and application.updater.running:
        callbacks.append(application.updater.stop)
    if application.running:
        callbacks.append(application.stop)
    callbacks.append(application.shutdown)
    for callback in callbacks:
        try:
            await asyncio.wait_for(callback(), timeout=max(0, deadline - loop.time()))
        except Exception as exc:
            failures.append(exc)
    if failures:
        raise failures[0]


def main() -> None:
    logger.LoggerFactory.create_logger()
    if settings.telegram_enabled:
        build_application().run_polling()

if __name__ == "__main__":
    main()
