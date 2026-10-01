import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { KnowledgeDTO } from '@/api/types';
import { useKnowledgeStore } from './knowledge';

const dto = (overrides: Partial<KnowledgeDTO> = {}): KnowledgeDTO => ({
  enabled: true,
  ready: true,
  indexing: false,
  indexed: 0,
  total: 0,
  size: 10,
  chunk_strategy: 'paragraph',
  chunk_size: 512,
  chunk_overlap: 64,
  top_k: 4,
  retrieve_top_k: 20,
  relevance_enabled: false,
  relevance_threshold: 0.6,
  embedding_model: 'ollama:nomic-embed-text-v2-moe:latest',
  error: false,
  ...overrides,
});

function mockFetch(handlers: Map<string, (body?: unknown) => Response>): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string | URL, init?: RequestInit) => {
      const key = `${init?.method ?? 'GET'} ${String(url).replace(/^\/api/, '')}`;
      const h = handlers.get(key);
      if (h === undefined) throw new Error(`нет заглушки для ${key}`);
      return h(init?.body === undefined ? undefined : JSON.parse(String(init.body)));
    }),
  );
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.unstubAllGlobals();
});

describe('knowledge store', () => {
  it('load тянет состояние RAG', async () => {
    mockFetch(new Map([['GET /knowledge', () => new Response(JSON.stringify(dto()), { status: 200 })]]));
    const store = useKnowledgeStore();
    await store.load();
    expect(store.loaded).toBe(true);
    expect(store.knowledge).toEqual(dto());
    expect(store.loadError).toBeNull();
  });

  it('update шлёт POST и заменяет состояние ответом', async () => {
    const off = dto({ enabled: false });
    mockFetch(
      new Map([
        ['GET /knowledge', () => new Response(JSON.stringify(dto()), { status: 200 })],
        ['POST /knowledge', () => new Response(JSON.stringify(off), { status: 200 })],
      ]),
    );
    const store = useKnowledgeStore();
    await store.load();
    await store.update({ enabled: false });
    expect(store.knowledge?.enabled).toBe(false);
  });

  it('ошибка записывается в loadError', async () => {
    mockFetch(
      new Map([
        ['GET /knowledge', () => new Response(JSON.stringify({ detail: 'boom' }), { status: 500 })],
      ]),
    );
    const store = useKnowledgeStore();
    await store.load();
    expect(store.loadError).toBe('boom');
  });
});
