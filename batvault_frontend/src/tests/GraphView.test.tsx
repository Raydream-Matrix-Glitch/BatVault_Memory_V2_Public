import { render, screen } from '@testing-library/react';
import { describe, test, expect } from 'vitest';
import GraphView from '../components/memory/GraphView';
import type { EvidenceItem } from '../types/memory';

describe('GraphView', () => {
  test('renders graph for small datasets', () => {
    const items: EvidenceItem[] = [
      { id: 'a', snippet: 'A', based_on: [] },
      { id: 'b', snippet: 'B', based_on: ['a'] },
    ];
    render(<GraphView items={items} selectedId={undefined} onSelect={() => {}} />);
    // Should not show fallback message
    expect(screen.queryByText(/Graph has/)).not.toBeInTheDocument();
  });
});