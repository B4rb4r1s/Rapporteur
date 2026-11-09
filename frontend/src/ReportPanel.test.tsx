import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { ReportPanel } from './ReportPanel';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const conference = {
  id: 'conference',
  title: 'Конференция',
  start_date: null,
  end_date: null,
  place: null,
  organizer: null,
  processing_started: true,
  talks: 1,
  summaries: 1,
  reviewed: 0,
};
const progress = {
  processing_started: true,
  files: 1,
  talks: [{ id: 'talk', section: 'Секция', order: 1, title: 'Доклад', status: 'processed', job: 'done', step: null, error: null, summary_number: 1, summary_status: 'ready' }],
};
const version = {
  id: 'version',
  number: 1,
  created_at: '2026-11-09T10:00:00Z',
  files: [{ format: 'docx' }, { format: 'md' }, { format: 'pdf' }],
  variables: { talks_count: 1 },
  warnings: ['Сводка не проверена человеком: «Доклад»'],
};

test('предупреждает о датах и собирает справку с подтверждением непроверенных', async () => {
  const api = vi.fn()
    .mockResolvedValueOnce(Response.json(conference))
    .mockResolvedValueOnce(Response.json(progress))
    .mockResolvedValueOnce(Response.json([]))
    .mockResolvedValueOnce(Response.json(version, { status: 201 }));
  vi.stubGlobal('fetch', api);
  render(<ReportPanel conferenceId="conference" />);

  await screen.findByText('Без дат в справке не будет периода проведения, сборка не пройдёт.');
  await screen.findByText('Собранных версий пока нет.');
  fireEvent.click(screen.getByLabelText('Собрать, даже если не все сводки проверены'));
  fireEvent.click(screen.getByRole('button', { name: 'Собрать справку' }));

  await screen.findByText('Собрана версия 1.');
  expect(screen.getByText('Сводка не проверена человеком: «Доклад»')).toBeDefined();
  expect(screen.getByRole('link', { name: 'PDF' }).getAttribute('href'))
    .toBe('/api/v1/report-versions/version/files/pdf');
  expect(api).toHaveBeenLastCalledWith('/api/v1/conferences/conference/reports', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ unreviewed_confirmed: true }),
  });
});
