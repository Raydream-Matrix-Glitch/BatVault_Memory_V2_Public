import { renderHook, act } from '@testing-library/react';
import { describe, test, expect, vi } from 'vitest';

const startStreamMock = vi.fn();

vi.mock('../hooks/useSSE', () => {
  return {
    useSSE: () => ({
      tokens: [],
      isStreaming: false,
      error: null,
      finalData: null,
      startStream: startStreamMock,
      cancel: vi.fn(),
    }),
  };
});

import { useMemoryAPI } from '../hooks/useMemoryAPI';

describe('useMemoryAPI', () => {
  test('calls /v2/ask and /v2/query endpoints', async () => {
    const { result } = renderHook(() => useMemoryAPI());
    await act(async () => {
      await result.current.ask('why_decision', { decision_ref: 'foo' });
    });
    expect(startStreamMock).toHaveBeenCalledWith('/v2/ask', { intent: 'why_decision', decision_ref: 'foo' }, expect.any(String));
    await act(async () => {
      await result.current.query('Why?');
    });
    expect(startStreamMock).toHaveBeenCalledWith('/v2/query', { text: 'Why?' }, expect.any(String));
  });
});
