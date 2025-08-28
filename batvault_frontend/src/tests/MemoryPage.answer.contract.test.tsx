import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, test, expect, vi } from 'vitest';

// Mock the useMemoryAPI hook to return a controlled response with new contract fields
vi.mock('../hooks/useMemoryAPI', () => {
  return {
    useMemoryAPI: () => ({
      tokens: [],
      isStreaming: false,
      error: null,
      finalData: {
        meta: {
          request_id: 'req-42',
          fallback_used: true,
          fallback_reason: 'deterministic execution',
        },
        evidence: {
          anchor: {
            id: 'dec-1',
            tags: ['strategy'],
            decision_maker: 'Jane Doe',
            timestamp: '2021-05-01',
          },
          events: [
            { id: 'evt-2', snippet: 'Event 2' },
            { id: 'evt-3', snippet: 'Event 3' },
            { id: 'evt-4', snippet: 'Event 4' },
          ],
          transitions: {
            succeeding: [
              { id: 'evt-99', snippet: 'Future event' },
            ],
          },
          allowed_ids: ['dec-1','evt-2','evt-3','evt-4','evt-99'],
        },
        answer: {
          short_answer: 'Short answer text.',
          supporting_ids: ['dec-1','evt-2','evt-3','evt-4'],
        },
        bundle_url: 'https://example.com/bundle.json',
      },
      ask: vi.fn(),
      query: vi.fn(),
      cancel: vi.fn(),
    }),
  };
});

import MemoryPage from '../components/memory/MemoryPage';

describe('MemoryPage answer contract integration', () => {
  test('renders citations, chips, badge, next line and triggers bundle download', async () => {
    // Spy on window.open for presigned bundle URL
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<MemoryPage />);

    // Short answer heading and text
    expect(await screen.findByText(/Short answer/)).toBeInTheDocument();
    expect(screen.getByText('Short answer text.')).toBeInTheDocument();

    // Citations: first three of four supporting IDs
    const cid1 = screen.getByRole('button', { name: 'dec-1' });
    const cid2 = screen.getByRole('button', { name: 'evt-2' });
    const cid3 = screen.getByRole('button', { name: 'evt-3' });
    expect(cid1).toBeInTheDocument();
    expect(cid2).toBeInTheDocument();
    expect(cid3).toBeInTheDocument();
    // The fourth ID should not appear
    expect(screen.queryByRole('button', { name: 'evt-4' })).toBeNull();

    // Maker and date chips
    expect(screen.getByText('Jane Doe')).toBeInTheDocument();
    expect(screen.getByText('2021-05-01')).toBeInTheDocument();

    // Badge reflecting fallback_used=true
    expect(screen.getByText('Deterministic')).toBeInTheDocument();

    // Next line shows the succeeding transition id
    expect(screen.getByText(/Next:/)).toBeInTheDocument();
    expect(screen.getByText('evt-99')).toBeInTheDocument();

    // Trigger download evidence button
    const dlBtn = screen.getByRole('button', { name: /Download evidence/i });
    await userEvent.click(dlBtn);
    expect(openSpy).toHaveBeenCalledWith('https://example.com/bundle.json', '_blank');
    openSpy.mockRestore();
  });
});