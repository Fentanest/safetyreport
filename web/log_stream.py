"""관리자/기기 인증은 라우트가 수행하고, 로그 읽기는 64KiB로 제한한다."""
import asyncio
import codecs
import os
from fastapi import WebSocketDisconnect
from core.utils import ws_auth


async def stream_log(websocket, log_file, watch, waiting_message):
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')

    try:
        if not os.path.exists(log_file):
            await websocket.send_text(waiting_message)
            while not os.path.exists(log_file):
                await asyncio.sleep(1)
                if await watch.lost():
                    await websocket.close(code=ws_auth.CLOSE_GATE)
                    return

        if os.path.exists(log_file):
            with open(log_file, "rb") as file_obj:
                info = os.fstat(file_obj.fileno())
                identity = (info.st_dev, info.st_ino)
                modified = info.st_mtime_ns
                size = info.st_size
                file_obj.seek(max(0, size - 64 * 1024))
                raw = file_obj.read(64 * 1024)
                if size > 64 * 1024:
                    while raw and (raw[0] & 0xc0) == 0x80: raw = raw[1:]
                data = decoder.decode(raw)
                if data:
                    await websocket.send_text(data)
                last_size = file_obj.tell()
        else:
            last_size = 0
            identity = None
            modified = None

        while True:
            await asyncio.sleep(0.5)
            if await watch.lost():
                await websocket.close(code=ws_auth.CLOSE_GATE)
                return
            if not os.path.exists(log_file):
                continue

            info = os.stat(log_file)
            current_size = info.st_size
            current_identity = (info.st_dev, info.st_ino)
            if current_identity != identity or current_size < last_size or (
                    current_size == last_size and info.st_mtime_ns != modified):
                last_size = 0
                decoder.reset()
            identity, modified = current_identity, info.st_mtime_ns
            if current_size > last_size:
                with open(log_file, "rb") as file_obj:
                    file_obj.seek(last_size)
                    new_data = decoder.decode(file_obj.read(64 * 1024))
                    if new_data:
                        await websocket.send_text(new_data)
                    last_size = file_obj.tell()
            elif current_size < last_size:
                last_size = 0
                decoder.reset()
    except WebSocketDisconnect:
        pass
    except (OSError, RuntimeError):
        return
