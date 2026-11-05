import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { App } from './App';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test('показывает название приложения', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({
    items: [{ id: 'conference', title: 'Конференция' }], page: { total: 1 },
  })));
  render(<App />);

  expect(screen.getByRole('heading', { level: 1, name: 'Репортёр' })).toBeDefined();
  await screen.findByRole('option', { name: 'Конференция' });
});

test('сохраняет ручную привязку с версией последнего просмотра', async () => {
  const review = {
    sections: [{ id: 'section', title: 'Секция' }],
    files: [{
      source: { id: 'file', kind: 'presentation', encoding: null, duration_ms: null },
      relative_path: 'Доклад.pdf',
      title_candidates: null,
      warning: null,
      binding: { confirmed: false, talk_id: null },
      suggested_talk_id: 'first',
      confidence: 0.90,
      alternate_of: null,
    }],
    talks: [
      { id: 'first', title: 'Первый', section: 'Секция', order: 1, status: 'draft' },
      { id: 'second', title: 'Второй', section: 'Секция', order: 2, status: 'draft' },
    ],
  };
  const api = vi.fn()
    .mockResolvedValueOnce(Response.json({
      items: [{ id: 'conference', title: 'Конференция' }], page: { total: 1 },
    }))
    .mockResolvedValueOnce(Response.json(review, { headers: { ETag: '"4"' } }))
    .mockResolvedValueOnce(Response.json({
      ...review,
      files: [{ ...review.files[0], binding: { confirmed: true, talk_id: 'second' } }],
    }, { headers: { ETag: '"5"' } }));
  vi.stubGlobal('fetch', api);
  render(<App />);

  await screen.findByRole('option', { name: 'Конференция' });
  fireEvent.change(screen.getByLabelText('Конференция'), { target: { value: 'conference' } });
  const select = await screen.findByRole<HTMLSelectElement>('combobox', { name: 'Доклад для Доклад.pdf' });
  expect(select.value).toBe('first');
  fireEvent.change(select, { target: { value: 'second' } });
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить привязки' }));
  await screen.findByText('Привязки сохранены.');

  expect(api).toHaveBeenLastCalledWith('/api/v1/conferences/conference/materials/bindings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'If-Match': '"4"' },
    body: JSON.stringify({ bindings: [{ source_file_id: 'file', talk_id: 'second' }] }),
  });
  expect(select.value).toBe('second');
});
