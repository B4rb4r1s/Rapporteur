import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App, parseRoute, routeHash } from './App';

const conference = {
  id: 'conference',
  title: 'Конференция',
  start_date: '2026-10-20',
  end_date: null,
  place: 'г. Москва',
  organizer: null,
  processing_started: false,
  talks: 1,
  summaries: 1,
  reviewed: 0,
};
const progress = {
  processing_started: false,
  files: 1,
  talks: [{ id: 'first', section: 'Секция', order: 1, title: 'Первый', status: 'processed', job: 'done', step: null, error: null, summary_number: 1, summary_status: 'ready' }],
};

beforeEach(() => {
  window.location.hash = '';
  document.documentElement.removeAttribute('data-theme');
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Ответы API по адресу; последний вызов с телом виден в mock.calls. */
function api(routes: Record<string, () => Response>) {
  return vi.fn(async (url: string) => {
    const path = url.split('?')[0];
    const handler = routes[path];
    if (!handler) throw new Error(`нет ответа для ${url}`);
    return handler();
  });
}

test('адрес страницы задаёт раздел и доклад', () => {
  expect(parseRoute('')).toEqual({ page: 'home' });
  expect(parseRoute('#/settings')).toEqual({ page: 'settings' });
  expect(parseRoute('#/c/abc/report')).toEqual({ page: 'conference', id: 'abc', tab: 'report' });
  expect(parseRoute('#/c/abc')).toEqual({ page: 'conference', id: 'abc', tab: 'materials' });
  expect(parseRoute('#/c/abc/talk/t1')).toEqual({ page: 'talk', id: 'abc', talkId: 't1' });
  expect(routeHash({ page: 'talk', id: 'abc', talkId: 't1' })).toBe('#/c/abc/talk/t1');
});

test('главная показывает конференции и удаляет выбранную после подтверждения', async () => {
  const fetch = api({
    '/api/v1/conferences': () => Response.json([conference]),
    '/api/v1/conferences/conference': () => new Response(null, { status: 204 }),
  });
  vi.stubGlobal('fetch', fetch);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(<App />);

  expect(screen.getByRole('heading', { level: 1, name: 'Репортёр' })).toBeDefined();
  const card = await screen.findByRole('article', { name: 'Конференция' });
  expect(within(card).getByText('Проверено')).toBeDefined();
  expect(within(card).getByText('обработка не запущена')).toBeDefined();
  fireEvent.click(within(card).getByRole('button', { name: 'Удалить' }));

  await screen.findByText('Конференция «Конференция» удалена.');
  expect(screen.queryByRole('article', { name: 'Конференция' })).toBeNull();
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/conferences/conference', { method: 'DELETE' });
});

test('переключатель темы меняет тему и запоминает выбор', async () => {
  vi.stubGlobal('fetch', api({
    '/api/v1/conferences': () => Response.json([]),
  }));
  render(<App />);
  await screen.findByText('Конференций пока нет.');

  fireEvent.click(screen.getByRole('button', { name: 'Включить тёмную тему' }));
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(window.localStorage.getItem('rapporteur.theme')).toBe('dark');
  fireEvent.click(screen.getByRole('button', { name: 'Включить светлую тему' }));
  expect(document.documentElement.dataset.theme).toBe('light');
});

test('сохраняет привязку и после запуска переходит к ходу обработки', async () => {
  const review = {
    processing_started: false,
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
  const saved = { ...review, files: [{ ...review.files[0], binding: { confirmed: true, talk_id: 'second' } }] };
  let materials = 0;
  const fetch = api({
    '/api/v1/conferences/conference': () => Response.json(conference),
    '/api/v1/conferences/conference/materials': () => Response.json(materials++ ? saved : review),
    '/api/v1/conferences/conference/materials/bindings': () => Response.json(saved),
    '/api/v1/conferences/conference/processing': () => Response.json(
      { ...progress, processing_started: true }, { status: 202 },
    ),
    '/api/v1/conferences/conference/progress': () => Response.json({ ...progress, processing_started: true }),
  });
  vi.stubGlobal('fetch', fetch);
  window.location.hash = '#/c/conference/materials';
  render(<App />);

  const select = await screen.findByRole<HTMLSelectElement>('combobox', { name: 'Доклад для Доклад.pdf' });
  expect(select.value).toBe('first');
  expect(screen.getByText('Проверьте и подтвердите привязки: пока они не подтверждены, шаги сводки ждут.')).toBeDefined();
  fireEvent.change(select, { target: { value: 'second' } });
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить привязки' }));
  await screen.findByText('Привязки сохранены.');
  expect(fetch).toHaveBeenCalledWith('/api/v1/conferences/conference/materials/bindings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bindings: [{ source_file_id: 'file', talk_id: 'second' }] }),
  });

  fireEvent.click(screen.getByRole('button', { name: 'Запустить обработку' }));
  await waitFor(() => expect(window.location.hash).toBe('#/c/conference/processing'));
  await screen.findByRole('heading', { name: 'Обработка' });
  expect(fetch).toHaveBeenCalledWith('/api/v1/conferences/conference/processing', { method: 'POST' });
});
