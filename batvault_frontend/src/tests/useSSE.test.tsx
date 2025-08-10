import { renderHook } from '@testing-library/react';
import { describe, test, expect } from 'vitest';
import { useSSE } from '../hooks/useSSE';

describe('useSSE hook', () => {
  test('initial state is idle', () => {
    const { result } = renderHook(() => useSSE());
    expect(result.current.tokens).toEqual([]);
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.finalData).toBe(null);
  });
});