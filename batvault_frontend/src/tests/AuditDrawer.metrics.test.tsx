import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, test, expect } from 'vitest';
import AuditDrawer from '../components/memory/AuditDrawer';
import type { EvidenceBundle, MetaInfo, WhyDecisionAnswer } from '../types/memory';

describe('AuditDrawer Metrics', () => {
  test('shows bundling metrics and prompt budget info', async () => {
    const meta: Partial<MetaInfo> = {
      latency_ms: 1234,
      retries: 1,
      routing_confidence: 0.91,
      function_calls: ['search_similar'],
      fallback_used: false,
      cache_hit: true,
      request_id: 'req-1',
      plan_fingerprint: 'plan-fp-1',
      prompt_fingerprint: 'prompt-fp-1',
      snapshot_etag: 'etag-1',
      total_neighbors_found: 12,
      selector_truncation: true,
      final_evidence_count: 8,
      dropped_evidence_ids: ['evt-x', 'evt-y'],
      bundle_size_bytes: 7800,
      max_prompt_bytes: 8192,
      fallback_reason: 'test reason',
    } as MetaInfo;

    const evidence: EvidenceBundle = {
      anchor: { id: 'dec-1', tags: ['strategy'] },
      events: [
        { id: 'evt-a', snippet: 'A' },
        { id: 'evt-b', snippet: 'B' },
      ],
      transitions: {},
      allowed_ids: ['dec-1','evt-a','evt-b'],
    };

    const answer: WhyDecisionAnswer = {
      short_answer: 'Because reasons',
      supporting_ids: ['dec-1'],
    };

    render(<AuditDrawer open={true} onClose={() => {}} meta={meta as MetaInfo} evidence={evidence} answer={answer} />);

    // Open Metrics tab by clicking its label
    await userEvent.click(screen.getByText('Metrics'));

    // Metrics tab renders values
    expect(screen.getByText(/Total neighbors found/)).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText(/Final evidence count/)).toBeInTheDocument();
    expect(screen.getByText('8')).toBeInTheDocument();
    expect(screen.getByText(/Selector truncation/)).toBeInTheDocument();
    expect(screen.getByText(/yes/)).toBeInTheDocument();
    expect(screen.getByText(/Bundle size/)).toBeInTheDocument();
    expect(screen.getByText('7800')).toBeInTheDocument();
    expect(screen.getByText(/Budget/)).toBeInTheDocument();
    expect(screen.getByText('8192')).toBeInTheDocument();
    // Fallback used and reason should be shown
    expect(screen.getByText(/Fallback used/)).toBeInTheDocument();
    expect(screen.getByText(/no/)).toBeInTheDocument();
    expect(screen.getByText(/Fallback reason/)).toBeInTheDocument();
    expect(screen.getByText(/test reason/)).toBeInTheDocument();
  });
});
