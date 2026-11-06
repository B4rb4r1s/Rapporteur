import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { ReportPanel } from './ReportPanel';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const conference = {
  id: 'conference', title: 'Конференция', start_date: null, end_date: null, place: null, organizer: null,
};
const progress = {
  processing_started: true,
  stages: {},
  talks: [{ id: 'talk', section: 'Секция', order: 1, title: 'Доклад', status: 'processed', stages: {}, error_code: null, summary_number: 1, summary_status: 'ready' }],
};
const report = { id: 'report', selection: { talk_ids: ['talk'] } };

test('предупреждает о датах и собирает справку с подтверждением непроверенных', async () => {
  const api = vi.fn()
    .mockResolvedValueOnce(Response.json(conference, { headers: { ETag: '"1"' } }))
    .mockResolvedValueOnce(Response.json(progress))
    .mockResolvedValueOnce(Response.json({ items: [report], page: { total: 1 } }))
    .mockResolvedValueOnce(Response.json({ items: [], page: { total: 0 } }))
    .mockResolvedValueOnce(Response.json(
      { job_id: 'job', status_url: '/api/v1/jobs/job' }, { status: 202 },
    ));
  vi.stubGlobal('fetch', api);
  render(<ReportPanel conferenceId="conference" />);

  await screen.findByText('Без дат в справке не будет периода проведения, сборка не пройдёт.');
  await screen.findByText('Собранных версий пока нет.');
  fireEvent.click(screen.getByLabelText('Собрать, даже если не все сводки проверены'));
  fireEvent.click(screen.getByRole('button', { name: 'Собрать справку' }));

  await screen.findByText('Справка собирается…');
  expect(api).toHaveBeenLastCalledWith('/api/v1/reports/report/build', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ unreviewed_confirmed: true }),
  });
});
