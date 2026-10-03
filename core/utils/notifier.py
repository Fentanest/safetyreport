import asyncio
import hashlib
import json
import os
import sys
import time
import uuid

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import telegram
import settings.settings as settings
from core.utils.atomic_file import write_bytes


def plan_chunks(message, max_len=4096):
    """전송 전에 모든 조각을 확정한다. Telegram 제한은 UTF-16 code unit 기준이다."""
    chunks, current, units = [], [], 0
    for character in message:
        width = 2 if ord(character) > 0xffff else 1
        if units + width > max_len:
            chunks.append(''.join(current))
            current, units = [], 0
        current.append(character)
        units += width
    if current:
        chunks.append(''.join(current))
    return [chunk for chunk in chunks if chunk.strip()]


async def send_message(bot, text, *, deadline=None):
    if not text or not text.strip():
        return
    deadline = deadline if deadline is not None else time.monotonic() + 60
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('알림 전송 제한 시간 초과')
        try:
            return await asyncio.wait_for(bot.send_message(chat_id=settings.chat_id, text=text), remaining)
        except telegram.error.RetryAfter as exc:
            # 명시적인 flood 거절만 재시도한다. timeout/connection loss는 적용 여부 미확정이다.
            delay = exc.retry_after
            if hasattr(delay, 'total_seconds'):
                delay = delay.total_seconds()
            delay = max(0, float(delay)) + 1
            if attempt == 2 or delay >= deadline - time.monotonic():
                raise
            await asyncio.sleep(delay)


async def deliver(bot, message, *, timeout=90):
    chunks = plan_chunks(message)
    if not chunks:
        return {'state': 'skipped', 'confirmed': 0}
    run_id = uuid.uuid4().hex
    path = os.path.join(settings.datapath, 'notification_runs', run_id + '.json')
    state = {'run_id': run_id, 'state': 'prepared', 'confirmed': 0, 'total': len(chunks),
             'sha256': hashlib.sha256(message.encode('utf-8')).hexdigest()}

    def record(value):
        state['state'] = value
        write_bytes(path, json.dumps(state).encode('utf-8'))

    record('prepared')
    deadline = time.monotonic() + timeout
    for index, chunk in enumerate(chunks):
        state['sending_index'] = index
        record('sending')
        try:
            await send_message(bot, chunk, deadline=deadline)
        except Exception:
            record('unknown')
            raise
        state['confirmed'] += 1
        record('partial' if state['confirmed'] < len(chunks) else 'succeeded')
        if index + 1 < len(chunks):
            if deadline - time.monotonic() < 1.5:
                record('partial')
                raise TimeoutError('알림 전송 제한 시간 초과')
            await asyncio.sleep(1.5)
    return dict(state)


async def main():
    if not settings.telegram_enabled:
        return
    if len(sys.argv) < 2:
        if sys.stdin.isatty():
            raise SystemExit('Usage: notifier.py <message>')
        message = sys.stdin.read()
    else:
        message = sys.argv[1]
    from core.utils.runtime_mode import block_if_fixture
    block_if_fixture('telegram notify')
    async with telegram.Bot(token=settings.telegram_token) as bot:
        await deliver(bot, message)


if __name__ == '__main__':
    asyncio.run(main())
