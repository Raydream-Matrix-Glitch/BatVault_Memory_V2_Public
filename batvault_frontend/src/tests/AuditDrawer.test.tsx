import { render, screen } from '@testing-library/react';
import { describe, test, expect } from 'vitest';
import AuditDrawer from '../components/memory/AuditDrawer';
import type { MetaInfo, EvidenceBundle, WhyDecisionAnswer } from '../types/memory';

describe('AuditDrawer', () => {
  test('renders when open with default tabs', () => {
    const meta: MetaInfo = {
      policy_id: 'p',
      prompt_id: 'prompt',
      retries: 0,
      latency_ms: 0,
    };
    const evidence: EvidenceBundle = {
      anchor: { id: 'a' },
      events: [],
      allowed_ids: ['a'],
    };
    const answer: WhyDecisionAnswer = { short_answer: '', supporting_ids: [] };
    render(
      <AuditDrawer open={true} onClose={() => {}} meta={meta} evidence={evidence} answer={answer} />
    );
    expect(screen.getByText(/Audit/)).toBeInTheDocument();
    expect(screen.getByText(/Trace/)).toBeInTheDocument();
    expect(screen.getByText(/Prompt/)).toBeInTheDocument();
    expect(screen.getByText(/Evidence/)).toBeInTheDocument();
    expect(screen.getByText(/Metrics/)).toBeInTheDocument();
    expect(screen.getByText(/Fingerprints/)).toBeInTheDocument();
  });
});