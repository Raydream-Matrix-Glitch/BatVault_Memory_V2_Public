import { render, screen } from '@testing-library/react';
import { describe, test, expect } from 'vitest';
import GraphView from '../components/memory/GraphView';
import type { EvidenceItem } from '../types/memory';

describe('GraphView (large)', () => {
  test('falls back to list view when over threshold', () => {
    const items: EvidenceItem[] = Array.from({ length: 101 }).map((_, i) => ({
      id: `n${i}`,
      snippet: `Node ${i}`,
      based_on: i > 0 ? [`n${i-1}`] : [],
    }));
    render(<GraphView items={items} selectedId={undefined} onSelect={() => {}} />);
    expect(screen.getByText(/Graph has 101 nodes/)).toBeInTheDocument();
  });
});
