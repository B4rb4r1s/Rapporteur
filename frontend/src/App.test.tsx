import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { App } from './App';

test('показывает название приложения', () => {
  render(<App />);

  expect(screen.getByRole('heading', { level: 1, name: 'Репортёр' })).toBeDefined();
});
