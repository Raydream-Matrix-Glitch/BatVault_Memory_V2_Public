import { render, screen } from '@testing-library/react';
import { describe, test, expect, vi } from 'vitest';
// Mock useSchema to provide deterministic data
vi.mock('../hooks/useSchema', () => {
  return {
    useSchema: () => ({
      fields: { name: ['full_name', 'first_name'], age: ['age', 'years'] },
      relations: [
        { from: 'person', to: 'name', relation: 'has' },
      ],
      loading: false,
      error: null,
    }),
  };
});

import SchemaExplorer from '../components/memory/SchemaExplorer';

describe('SchemaExplorer', () => {
  test('renders schema fields and relations', () => {
    render(<SchemaExplorer />);
    expect(screen.getByText(/Schema Explorer/)).toBeInTheDocument();
    expect(screen.getByText(/name/)).toBeInTheDocument();
    expect(screen.getByText(/full_name/)).toBeInTheDocument();
    expect(screen.getByText(/Relations/)).toBeInTheDocument();
    expect(screen.getByText(/person/)).toBeInTheDocument();
  });
});