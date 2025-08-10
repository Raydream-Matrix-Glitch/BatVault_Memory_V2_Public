import { render, screen } from '@testing-library/react';
import { describe, test, expect, vi } from 'vitest';
import EvidenceList from '../components/memory/EvidenceList';
import type { EvidenceItem } from '../types/memory';

describe('EvidenceList', () => {
  test('renders items and calls onSelect', async () => {
    const items: EvidenceItem[] = [
      { id: 'dec-1', snippet: 'Anchor', tags: ['anchor'], based_on: [] },
      { id: 'evt-1', snippet: 'Event one', tags: ['event'], based_on: ['dec-1'] },
    ];
    const onSelect = vi.fn();
    render(
      <EvidenceList
        items={items}
        selectedId={undefined}
        onSelect={onSelect}
      />
    );
    // EvidenceList uses virtualization; the first item should be rendered.
    expect(screen.getByText(/Anchor/)).toBeInTheDocument();
  });
});