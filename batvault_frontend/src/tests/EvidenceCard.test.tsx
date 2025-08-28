import { render, screen, fireEvent } from '@testing-library/react';
import { describe, test, expect, vi } from 'vitest';
import EvidenceCard from '../components/memory/EvidenceCard';
import type { EvidenceItem } from '../types/memory';

describe('EvidenceCard', () => {
  test('renders snippet, tags, based_on and orphan indicator', () => {
    const item: EvidenceItem = {
      id: 'evt-1',
      snippet: 'Quarterly report shows losses',
      tags: ['finance', 'risk'],
      based_on: ['dec-0'],
      orphan: false,
    };
    const onSelect = vi.fn();
    render(<EvidenceCard item={item} onSelect={onSelect} />);

    expect(screen.getByText(/evt-1/)).toBeInTheDocument();
    expect(screen.getByText(/Quarterly report/)).toBeInTheDocument();
    expect(screen.getByText('finance')).toBeInTheDocument();
    expect(screen.getByText('risk')).toBeInTheDocument();
    expect(screen.getByText(/Based on:/)).toBeInTheDocument();
    expect(screen.queryByText(/Orphan evidence/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText(/evt-1/));
    expect(onSelect).toHaveBeenCalledWith('evt-1');
  });

  test('shows orphan indicator when no links', () => {
    const item: EvidenceItem = { id: 'evt-2', snippet: 'Isolated note', orphan: true };
    render(<EvidenceCard item={item} onSelect={() => {}} />);
    expect(screen.getByText(/Orphan evidence/)).toBeInTheDocument();
  });
});
