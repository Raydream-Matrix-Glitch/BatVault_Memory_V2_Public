import { render, screen, fireEvent } from '@testing-library/react';
import { describe, test, expect, vi } from 'vitest';
import TagCloud from '../components/memory/TagCloud';

describe('TagCloud', () => {
  test('selects and toggles tags', () => {
    const onSelect = vi.fn();
    const tags = { finance: 3, risk: 1, strategy: 2 };
    render(<TagCloud tags={tags} onSelect={onSelect} />);

    const finance = screen.getByRole('button', { name: 'finance' });
    fireEvent.click(finance);
    expect(onSelect).toHaveBeenCalledWith('finance');

    onSelect.mockClear();
    render(<TagCloud tags={tags} selected="finance" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: 'finance' }));
    expect(onSelect).toHaveBeenCalledWith(undefined);
  });
});
