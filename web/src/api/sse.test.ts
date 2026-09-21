import { describe, expect, it } from "vitest";
import { parseSseChunk, sseEvents } from "./sse";

function streamOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(c);
      controller.close();
    },
  });
}

function resp(chunks: Uint8Array[]): Response {
  return new Response(streamOf(chunks), { status: 200 });
}

const enc = (s: string): Uint8Array => new TextEncoder().encode(s);

describe("parseSseChunk", () => {
  it("несколько событий в одном чанке", () => {
    const { events, rest } = parseSseChunk('data: {"a":1}\n\ndata: {"b":2}\n\n');
    expect(events).toEqual(['{"a":1}', '{"b":2}']);
    expect(rest).toBe("");
  });

  it("разрыв события между чанками — событие копится в rest", () => {
    const first = parseSseChunk('data: {"con');
    expect(first.events).toEqual([]);
    expect(first.rest).toBe('data: {"con');
    const second = parseSseChunk(first.rest + 'tent":"x"}\n\ndata: 2\n\n');
    expect(second.events).toEqual(['{"content":"x"}', "2"]);
  });

  it("CRLF-разделители", () => {
    const { events } = parseSseChunk('data: a\r\n\r\ndata: b\r\n\r\n');
    expect(events).toEqual(["a", "b"]);
  });

  it("игнорирует event:/id:/комментарии, многострочный data", () => {
    const text = 'event: delta\nid: 7\n: ping\ndata: {"x":\ndata: 1}\n\n';
    const { events } = parseSseChunk(text);
    expect(events).toEqual(['{"x":\n1}']);
  });

  it("незавершённый блок остаётся в rest", () => {
    const { events, rest } = parseSseChunk("data: x\n\ndata: y");
    expect(events).toEqual(["x"]);
    expect(rest).toBe("data: y");
  });
});

describe("sseEvents", () => {
  it("читает события из потока с разрывами между чанками", async () => {
    const r = resp([enc('data: {"e'), enc('vent":"delta","con'), enc('tent":"Прив"}\n\ndata: {"event":"done"}\n\n')]);
    const out: string[] = [];
    for await (const ev of sseEvents(r)) out.push(ev);
    expect(out).toEqual(['{"event":"delta","content":"Прив"}', '{"event":"done"}']);
  });

  it("обрыв соединения без финального разделителя — хвост отдаётся как событие", async () => {
    const r = resp([enc("data: x\n\n"), enc("data: y")]);
    const out: string[] = [];
    for await (const ev of sseEvents(r)) out.push(ev);
    expect(out).toEqual(["x", "y"]);
  });

  it("пустой поток — ноль событий", async () => {
    const out: string[] = [];
    for await (const ev of sseEvents(resp([]))) out.push(ev);
    expect(out).toEqual([]);
  });
});
