import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { SummaryReview, type SummaryView } from './SummaryReview';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// Перед цитатой символ вне базовой плоскости: в UTF-16 он занимает две позиции, а в
// кодовых точках, которыми считает span сервер, — одну.
const slideText = '𝔼 Результаты: задержка снизилась на 23 %.';
const view: SummaryView = {
  talk_title: 'Кэширование',
  talk_status: 'processed',
  pack: {
    fragments: [{
      label: 'S2',
      kind: 'slide',
      revision_id: 'revision',
      source_file_id: 'file',
      locator: { kind: 'page', page_no: 2 },
      text: slideText,
    }],
  },
  summary: {
    version: 1,
    status: 'ready',
    profile: 'local',
    human_edit: false,
    created_at: '2026-11-06T10:00:00Z',
    sentences: [{ text: 'Задержка снизилась на 23 %.', claim_ids: ['c1'] }],
    conclusions: [],
    claims: [{
      key: 'c1',
      type: 'result',
      text: 'Задержка снизилась на 23 %.',
      status: 'verified',
      evidence: [{ label: 'S2', quote: 'задержка снизилась', fragment_revision_id: 'revision', span: [14, 32] }],
      rejected_evidence: [],
    }],
  },
};

function reply(body: SummaryView) {
  return Response.json(body);
}

test('утверждение ведёт к слайду и подсвечивает цитату по кодовым точкам', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(view)));
  render(<SummaryReview talkId="talk" title="Кэширование" onClose={() => {}} />);

  fireEvent.click(await screen.findByRole('button', { name: 'c1' }));

  const card = screen.getByRole('complementary', { name: 'Утверждение c1' });
  expect(within(card).getByText('задержка снизилась').tagName).toBe('MARK');
  expect(within(card).getByText('S2 · слайд 2')).toBeDefined();
  expect(within(card).getByRole<HTMLImageElement>('img', { name: 'Слайд 2' }).getAttribute('src'))
    .toBe('/api/v1/files/file/slides/2');
});

test('правка уходит с номером прочитанной версии, конфликт показан', async () => {
  const api = vi.fn()
    .mockResolvedValueOnce(reply(view))
    .mockResolvedValueOnce(Response.json(
      { message: 'Сводку уже изменили: обновите её и повторите правку' },
      { status: 409 },
    ));
  vi.stubGlobal('fetch', api);
  render(<SummaryReview talkId="talk" title="Кэширование" onClose={() => {}} />);

  fireEvent.click(await screen.findByRole('button', { name: 'Править' }));
  fireEvent.change(screen.getByLabelText('Предложение 1'), { target: { value: 'Новый текст.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить правку' }));

  await screen.findByText('Сводку уже изменили: обновите её и повторите правку');
  expect(api).toHaveBeenLastCalledWith('/api/v1/talks/talk/summary', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      version: 1,
      sentences: [{ text: 'Новый текст.', claim_ids: ['c1'] }],
      conclusions: [],
      claims: [],
    }),
  });
});

test('подтверждение проверки создаёт новую версию', async () => {
  const reviewed: SummaryView = {
    ...view,
    talk_status: 'reviewed',
    summary: { ...view.summary, version: 2, claims: [{ ...view.summary.claims[0], status: 'confirmed' }] },
  };
  const api = vi.fn()
    .mockResolvedValueOnce(reply(view))
    .mockResolvedValueOnce(reply(reviewed));
  vi.stubGlobal('fetch', api);
  render(<SummaryReview talkId="talk" title="Кэширование" onClose={() => {}} />);

  fireEvent.click(await screen.findByRole('button', { name: 'Подтвердить проверку' }));

  await screen.findByText('Сводка проверена.');
  expect(api).toHaveBeenLastCalledWith('/api/v1/talks/talk/summary/review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version: 1 }),
  });
  expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Подтвердить проверку' }).disabled).toBe(true);
});
