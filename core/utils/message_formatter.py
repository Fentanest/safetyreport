def format_report_list(results, title: str):
    """Formats a list of report dictionaries into a detailed message string."""
    if not results:
        return None

    def _format_links(raw: str, label: str) -> str:
        """Helper: parse newline-separated URLs and return a formatted string."""
        if not raw or str(raw).strip() in ('', 'nan', 'None'):
            return ""
        urls = [u.strip() for u in str(raw).split('\n') if u.strip()]
        if not urls:
            return ""
        lines = [f"{label}:"]
        for i, url in enumerate(urls, 1):
            lines.append(f"  {i}. {url}")
        return "\n".join(lines) + "\n"

    response_parts = [f"{title}\n\n"]
    for i, row in enumerate(results):
        photos_str = _format_links(row.get('첨부사진', ''), '📷 첨부사진')
        files_str  = _format_links(row.get('첨부파일', ''), '📎 첨부파일')
        attachments = photos_str + files_str

        part = (
            f"--- [결과 {i+1}] ---\n"
            f"차량번호: {row.get('차량번호', 'N/A')}\n"
            f"신고번호: {row.get('신고번호', 'N/A')}\n"
            f"신고일: {row.get('신고일', 'N/A')}\n"
            f"발생일: {row.get('발생일자', 'N/A')}\n"
            f"답변일: {row.get('답변일', 'N/A')}\n"
            f"위반법규: {row.get('위반법규', 'N/A')}\n"
            f"처리상태: {row.get('처리상태', 'N/A')}\n"
            f"범칙금/과태료: {row.get('범칙금_과태료', 'N/A')}\n"
            f"처리기관: {row.get('처리기관', 'N/A')}\n"
            f"담당자: {row.get('담당자', 'N/A')}\n"
            + (attachments if attachments else "")
            + "\n"
        )
        response_parts.append(part)

    return "".join(response_parts)

async def send_message_in_chunks(bot, chat_id, text: str):
    """Sends a long message in chunks if it exceeds Telegram's limit."""
    if not text:
        return
        
    # Telegram message length limit is 4096 characters
    if len(text) > 4096:
        # Send a header message first
        header = text.split('\n\n', 1)[0]
        await bot.send_message(chat_id=chat_id, text=f"{header}\n(결과가 너무 길어 여러 개로 나누어 보냅니다.)")
        
        # Split message into chunks by finding the start of a new result entry
        chunks = []
        current_chunk = ""
        for part in text.split("--- [결과"):
            if not part:
                continue
            
            part = "--- [결과" + part
            if len(current_chunk) + len(part) > 4096:
                chunks.append(current_chunk)
                current_chunk = part
            else:
                current_chunk += part
        
        if current_chunk:
            chunks.append(current_chunk)

        for chunk in chunks:
            if chunk.strip():
                await bot.send_message(chat_id=chat_id, text=chunk)
    else:
        await bot.send_message(chat_id=chat_id, text=text)
