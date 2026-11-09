import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { Settings, type ModelState } from './Settings';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const system = {
  version: '0.2.0',
  ollama_url: 'http://ollama:11434',
  ollama_available: true,
  ollama_models: ['qwen3.5:9b', 'qwen2.5:7b'],
  profiles: [{ name: 'local', model: 'qwen3.5:9b', reasoning: true, prompt_version: 'v1', default: true }],
  templates: [{ name: 'starter', version: 'starter-1' }],
  queue: { failed: 1 },
};
const local = { profile: 'local', model: 'qwen3.5:9b', reasoning: true, chosen: false };
const model: ModelState = {
  current: local,
  default: local,
  ollama_available: true,
  models: [
    { name: 'qwen3.5:9b', parameter_size: '9.7B', quantization: 'Q4_K_M', thinking: true },
    { name: 'qwen2.5:7b', parameter_size: '7.6B', quantization: 'Q4_K_M', thinking: false },
  ],
};

function api(saved?: ModelState) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/v1/system') return Response.json(system);
    if (url === '/api/v1/model') return Response.json(init?.method === 'PUT' ? saved : model);
    throw new Error(`нет ответа для ${url}`);
  });
}

test('показывает Ollama, профили и очередь, переключает тему', async () => {
  vi.stubGlobal('fetch', api());
  const onTheme = vi.fn();
  render(<Settings theme="system" onTheme={onTheme} />);

  await screen.findByText('доступна');
  expect(screen.getByText('по умолчанию')).toBeDefined();
  expect(screen.getByText('Модели Ollama: qwen3.5:9b, qwen2.5:7b.')).toBeDefined();
  expect(screen.getByText('С ошибкой').parentElement?.textContent).toBe('С ошибкой1');
  fireEvent.click(screen.getByLabelText('Тёмная'));
  expect(onTheme).toHaveBeenCalledWith('dark');
});

test('выбор модели без рассуждения снимает и блокирует галочку', async () => {
  const chosen = { profile: 'qwen2.5:7b', model: 'qwen2.5:7b', reasoning: false, chosen: true };
  const fetch = api({ ...model, current: chosen });
  vi.stubGlobal('fetch', fetch);
  render(<Settings theme="system" onTheme={() => {}} />);

  const thinking = await screen.findByRole<HTMLInputElement>('checkbox', { name: 'Режим рассуждения' });
  expect(thinking.checked).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Сохранить' }).disabled).toBe(true);

  fireEvent.change(screen.getByLabelText('Модель Ollama'), { target: { value: 'qwen2.5:7b' } });
  expect(thinking.checked).toBe(false);
  expect(thinking.disabled).toBe(true);
  expect(screen.getByText('У этой модели нет режима рассуждения.')).toBeDefined();
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

  await screen.findByText('Модель сохранена.');
  expect(fetch).toHaveBeenCalledWith('/api/v1/model', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: 'qwen2.5:7b', reasoning: false }),
  });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Как в profiles.yaml' })).toBeDefined());
});
