def format_report_list(results, title: str):
    """Formats a list of report dictionaries into a detailed message string."""
    if not results:
        return None

    response_parts = [f"{title}\n\n"]
    for i, row in enumerate(results):
        part = f"""--- [결과 {i+1}] ---\n차량번호: {row.get('차량번호', 'N/A')}\n신고번호: {row.get('신고번호', 'N/A')}\n신고일: {row.get('신고일', 'N/A')}\n발생일: {row.get('발생일자', 'N/A')}\n답변일: {row.get('답변일', 'N/A')}\n위반법규: {row.get('위반법규', 'N/A')}\n처리상태: {row.get('처리상태', 'N/A')}\n범칙금/과태료: {row.get('범칙금_과태료', 'N/A')}\n처리기관: {row.get('처리기관', 'N/A')}\n담당자: {row.get('담당자', 'N/A')}\n\n"""
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
