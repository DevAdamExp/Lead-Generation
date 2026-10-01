import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useLeads } from '@/hooks/useLeads';
import { makeLead, makeLeadsResponse } from '@/test/mocks';

const mockGetLeads = vi.fn();
vi.mock('@/utils/api', () => ({
  getLeads: (...args: unknown[]) => mockGetLeads(...args),
}));

beforeEach(() => {
  mockGetLeads.mockReset();
});

describe('useLeads', () => {
  it('returns initial state when jobId is null', () => {
    const { result } = renderHook(() => useLeads(null));
    expect(result.current.leads).toEqual([]);
    expect(result.current.total).toBe(0);
    expect(result.current.page).toBe(1);
    expect(result.current.loading).toBe(false);
    expect(result.current.tab).toBe('no_website');
  });

  it('fetches leads on fetchLeads call', async () => {
    const resp = makeLeadsResponse({ items: [makeLead({ id: 'l1' })], total: 1 });
    mockGetLeads.mockResolvedValue(resp);

    const { result } = renderHook(() => useLeads('job-1'));
    await act(async () => {
      await result.current.fetchLeads(1);
    });

    expect(result.current.leads).toHaveLength(1);
    expect(result.current.leads[0].id).toBe('l1');
    expect(result.current.total).toBe(1);
    expect(mockGetLeads).toHaveBeenCalledWith('job-1', 1, 50, 0, 'no_website', false);
  });

  it('does not fetch when jobId is null', async () => {
    const { result } = renderHook(() => useLeads(null));
    await act(async () => {
      await result.current.fetchLeads(1);
    });
    expect(mockGetLeads).not.toHaveBeenCalled();
  });

  it('changePage updates page and fetches', async () => {
    const resp = makeLeadsResponse({ items: [], total: 0 });
    mockGetLeads.mockResolvedValue(resp);

    const { result } = renderHook(() => useLeads('job-1'));
    await act(async () => {
      await result.current.changePage(3);
    });

    expect(result.current.page).toBe(3);
    expect(mockGetLeads).toHaveBeenCalledWith('job-1', 3, 50, 0, 'no_website', false);
  });

  it('changeTab resets page to 1 and fetches', async () => {
    mockGetLeads.mockResolvedValue(makeLeadsResponse({ items: [], total: 0 }));

    const { result } = renderHook(() => useLeads('job-1'));
    // First fetch sets page = 1
    await act(async () => {
      await result.current.fetchLeads(1);
    });

    // Change tab
    await act(async () => {
      await result.current.changeTab('high_value');
    });

    expect(result.current.tab).toBe('high_value');
    expect(result.current.page).toBe(1);
  });

  it('applyFilters resets page to 1 and fetches with filter params', async () => {
    mockGetLeads.mockResolvedValue(makeLeadsResponse({ items: [], total: 0 }));

    const { result } = renderHook(() => useLeads('job-1'));

    await act(async () => {
      result.current.setHasEmail(true);
      result.current.setMinScore(50);
    });

    await act(async () => {
      await result.current.applyFilters();
    });

    expect(result.current.page).toBe(1);
    expect(mockGetLeads).toHaveBeenCalledWith('job-1', 1, 50, 50, 'no_website', true);
  });

  it('handles fetch error silently', async () => {
    mockGetLeads.mockRejectedValue(new Error('network error'));

    const { result } = renderHook(() => useLeads('job-1'));
    await act(async () => {
      await result.current.fetchLeads(1);
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.leads).toEqual([]);
  });

  it('sets loading state during fetch', async () => {
    let resolvePromise!: (v: unknown) => void;
    mockGetLeads.mockReturnValue(new Promise((resolve) => { resolvePromise = resolve; }));

    const { result } = renderHook(() => useLeads('job-1'));

    let fetchPromise: Promise<void>;
    act(() => {
      fetchPromise = result.current.fetchLeads(1);
    });
    expect(result.current.loading).toBe(true);

    await act(async () => {
      resolvePromise(makeLeadsResponse());
      await fetchPromise;
    });

    expect(result.current.loading).toBe(false);
  });
});
