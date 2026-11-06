import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { Settings } from './Settings';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const system = {
  version: '0.1.0',
  mode: 'gpu',
  migration: '0004_summaries',
  profiles: [{ name: 'local', model: 'qwen3.5:9b', reasoning: true, prompt_version: 'v1', default: true }],
  templates: [{ name: 'starter', version: 'starter-1' }],
  queue: { gpu: { failed: 1, succeeded: 4 } },
};
const failed = { id: 'job', stage: 'facts', conference_id: 'c', talk_id: 't', error_code: 'llm_unavailable', created_at: '2026-11-06T10:00:00Z' };

test('показывает профили и повторяет задачу с ошибкой', async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(Response.json(system))
    .mockResolvedValueOnce(Response.json({ items: [failed], page: { total: 1 } }))
    .mockResolvedValueOnce(Response.json({ job_id: 'job', status_url: '/api/v1/jobs/job' }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ ...system, queue: { gpu: { queued: 1, succeeded: 4 } } }))
    .mockResolvedValueOnce(Response.json({ items: [], page: { total: 0 } }));
  vi.stubGlobal('fetch', fetch);
  const onTheme = vi.fn();
  render(<Settings theme="system" onTheme={onTheme} />);

  await screen.findByText('qwen3.5:9b');
  expect(screen.getByText('по умолчанию')).toBeDefined();
  fireEvent.click(screen.getByLabelText('Тёмная'));
  expect(onTheme).toHaveBeenCalledWith('dark');

  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  await screen.findByText('Задача «facts» поставлена заново.');
  expect(fetch).toHaveBeenCalledWith('/api/v1/jobs/job/retry', { method: 'POST' });
  expect(screen.getByText('Ошибок нет.')).toBeDefined();
});
