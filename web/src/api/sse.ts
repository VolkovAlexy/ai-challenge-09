// SSE-парсер поверх fetch(POST) + ReadableStream (§5.3).
// Разбиение по \n\n (или \r\n\r\n), устойчивость к разрыву события между
// чанками, игнор полей event:/id:. [DONE] не ожидается: терминалы —
// события done/cancelled/error или закрытие потока.

/** Разбирает сырой SSE-текст на data-полезные нагрузки. Экспортирован для тестов. */
export function parseSseChunk(buffer: string): { events: string[]; rest: string } {
  const events: string[] = [];
  let start = 0;
  for (;;) {
    let idx = buffer.indexOf("\n\n", start);
    let sepLen = 2;
    const crlf = buffer.indexOf("\r\n\r\n", start);
    if (crlf !== -1 && (idx === -1 || crlf < idx)) {
      idx = crlf;
      sepLen = 4;
    }
    if (idx === -1) break;
    const block = buffer.slice(start, idx);
    start = idx + sepLen;
    const data = blockToData(block);
    if (data !== null) events.push(data);
  }
  return { events, rest: buffer.slice(start) };
}

function blockToData(block: string): string | null {
  const lines = block.split(/\r?\n/);
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith(":")) continue; // комментарий
    if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (dataLines.length === 0) return null;
  return dataLines.join("\n");
}

/** Инкрементальный генератор событий над телом ответа. */
export async function* sseEvents(response: Response): AsyncGenerator<string> {
  const body = response.body;
  if (body === null) throw new Error("у ответа нет тела (SSE)");
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  for await (const chunk of body as unknown as AsyncIterable<Uint8Array>) {
    buffer += decoder.decode(chunk, { stream: true });
    const parsed = parseSseChunk(buffer);
    buffer = parsed.rest;
    for (const ev of parsed.events) yield ev;
  }
  buffer += decoder.decode(); // flush
  const data = blockToData(buffer);
  if (data !== null) yield data;
}
