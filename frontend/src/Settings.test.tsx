import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { Settings } from './Settings';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const system = {
  version: '0.2.0',
  ollama_url: 'http://ollama:11434',
  ollama_available: true,
  ollama_models: ['qwen3.5:9b'],
  profiles: [{ name: 'local', model: 'qwen3.5:9b', reasoning: true, prompt_version: 'v1', default: true }],
  templates: [{ name: 'starter', version: 'starter-1' }],
  queue: { failed: 1 },
};

test('показывает Ollama, профили и очередь, переключает тему', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(system)));
  const onTheme = vi.fn();
  render(<Settings theme="system" onTheme={onTheme} />);

  await screen.findByText('доступна');
  expect(screen.getByText('по умолчанию')).toBeDefined();
  expect(screen.getByText('Модели Ollama: qwen3.5:9b.')).toBeDefined();
  expect(screen.getByText('С ошибкой').parentElement?.textContent).toBe('С ошибкой1');
  fireEvent.click(screen.getByLabelText('Тёмная'));
  expect(onTheme).toHaveBeenCalledWith('dark');
});
