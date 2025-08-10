import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, test, expect, vi } from 'vitest';
import QueryPanel from '../components/memory/QueryPanel';

describe('QueryPanel', () => {
  test('calls onAsk on structured submit', async () => {
    const onAsk = vi.fn();
    const onQuery = vi.fn();
    render(<QueryPanel onAsk={onAsk} onQuery={onQuery} isStreaming={false} />);
    const decisionInput = screen.getByLabelText(/Decision reference/i);
    await userEvent.type(decisionInput, 'foo-bar');
    const submitBtn = screen.getByRole('button', { name: /submit/i });
    await userEvent.click(submitBtn);
    expect(onAsk).toHaveBeenCalledWith('why_decision', 'foo-bar', undefined);
    expect(onQuery).not.toHaveBeenCalled();
  });

  test('calls onQuery on natural submit', async () => {
    const onAsk = vi.fn();
    const onQuery = vi.fn();
    render(<QueryPanel onAsk={onAsk} onQuery={onQuery} isStreaming={false} />);
    // Switch to natural tab
    const naturalTab = screen.getByRole('button', { name: /Natural/i });
    await userEvent.click(naturalTab);
    const queryInput = screen.getByLabelText(/Query/i);
    await userEvent.type(queryInput, 'What happened?');
    const submitBtn = screen.getByRole('button', { name: /submit/i });
    await userEvent.click(submitBtn);
    expect(onQuery).toHaveBeenCalledWith('What happened?');
    expect(onAsk).not.toHaveBeenCalled();
  });
});