"""응답 성공/실패/취소 뒤 임시 다운로드 파일을 제거한다."""
import os

from starlette.responses import FileResponse


class TemporaryFileResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
