import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, test, expect, vi } from 'vitest';

vi.mock('../hooks/useMemoryAPI', () => {
  return {
    useMemoryAPI: () => ({
      tokens: [],
      isStreaming: false,
      error: null,
      finalData: {
        meta: {
          policy_id: 'p',
          prompt_id: 'pr',
          retries: 0,
          latency_ms: 10,
          function_calls: ['search_similar'],
          routing_confidence: 0.9,
          prompt_fingerprint: 'fp123',
          snapshot_etag: 'etag123',
        },
        evidence: {
          anchor: { id: 'dec-1', tags: ['strategy'] },
          events: [
            { id: 'evt-1', snippet: 'Event one', tags: ['finance'], based_on: ['dec-1'] },
            { id: 'evt-2', snippet: 'Event two', tags: ['risk'] },
          ],
          allowed_ids: ['dec-1','evt-1','evt-2'],
        },
        answer: { short_answer: 'Because losses mounted', supporting_ids: ['dec-1'] },
      },
      ask: vi.fn(),
      query: vi.fn(),
      cancel: vi.fn(),
    }),
  };
});

import MemoryPage from '../components/memory/MemoryPage';

describe('MemoryPage integration', () => {
  test('renders evidence, tag cloud and opens audit drawer', async () => {
    render(<MemoryPage />);

    expect(screen.getByText(/BatVault Memory Interface/)).toBeInTheDocument();
    expect(await screen.findByText(/Event one/)).toBeInTheDocument();
    expect(screen.getByText(/Event two/)).toBeInTheDocument();

    expect(screen.getByText(/Filter by tag/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'finance' })).toBeInTheDocument();

    const auditBtn = screen.getByRole('button', { name: /Audit/i });
    await userEvent.click(auditBtn);
    expect(screen.getByText(/Audit/)).toBeInTheDocument();
    expect(screen.getByText(/Trace/)).toBeInTheDocument();
    expect(screen.getByText(/Prompt/)).toBeInTheDocument();
  });
});
