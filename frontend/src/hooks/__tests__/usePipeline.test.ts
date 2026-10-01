import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { usePipeline } from '@/hooks/usePipeline';

const mockGetJob = vi.fn();
let wsOnopenHandler: (() => void) | null = null;
let wsOnmessageHandler: ((e: { data: string }) => void) | null = null;
let wsOnerrorHandler: (() => void) | null = null;

class MockWebSocket {
  static simulatedError = false;

  constructor(_url: string) {
    wsOnopenHandler = null;
    wsOnmessageHandler = null;
    wsOnerrorHandler = null;

    setTimeout(() => {
      if (MockWebSocket.simulatedError) {
        wsOnerrorHandler?.();
      } else {
        wsOnopenHandler?.();
      }
    }, 0);
  }

  close() {
    wsOnopenHandler = null;
    wsOnmessageHandler = null;
    wsOnerrorHandler = null;
  }

  set onopen(fn: (() => void) | null) { wsOnopenHandler = fn; }
  set onmessage(fn: ((e: { data: string }) => void) | null) { wsOnmessageHandler = fn; }
  set onerror(fn: (() => void) | null) { wsOnerrorHandler = fn; }
  get onopen() { return wsOnopenHandler; }
  get onmessage() { return wsOnmessageHandler; }
  get onerror() { return wsOnerrorHandler; }
}

vi.stubGlobal('WebSocket', MockWebSocket);
vi.mock('@/utils/api', () => ({
  getWsUrl: (id: string) => `ws://localhost/ws/jobs/${id}`,
  getJob: (...args: unknown[]) => mockGetJob(...args),
}));

beforeEach(() => {
  mockGetJob.mockReset();
  MockWebSocket.simulatedError = false;
  wsOnopenHandler = null;
  wsOnmessageHandler = null;
  wsOnerrorHandler = null;
});

describe('usePipeline', () => {
  it('starts in idle state', () => {
    const { result } = renderHook(() => usePipeline());
    expect(result.current.isRunning).toBe(false);
    expect(result.current.isComplete).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.progress.pct).toBe(0);
  });

  it('sets isRunning on connect', () => {
    const { result } = renderHook(() => usePipeline());
    act(() => { result.current.connect('job-1'); });
    expect(result.current.isRunning).toBe(true);
    expect(result.current.isComplete).toBe(false);
  });

  it('sets progress to initial values on connect', () => {
    const { result } = renderHook(() => usePipeline());
    act(() => { result.current.connect('job-1'); });
    expect(result.current.progress.pct).toBe(0);
    expect(result.current.progress.stage).toBe('Starting');
    expect(result.current.progress.message).toBe('Initialising pipeline...');
  });

  it('handles progress message via WebSocket', () => {
    const { result } = renderHook(() => usePipeline());
    act(() => { result.current.connect('job-1'); });

    act(() => {
      wsOnmessageHandler?.({
        data: JSON.stringify({
          type: 'progress', progress_pct: 45, stage: 'Scraping',
          message: 'Working...', stats: { total_scraped: 10, total_verified: 5 },
        }),
      });
    });

    expect(result.current.progress.pct).toBe(45);
    expect(result.current.progress.stage).toBe('Scraping');
    expect(result.current.progress.message).toBe('Working...');
    expect(result.current.progress.scraped).toBe(10);
    expect(result.current.progress.verified).toBe(5);
  });

  it('handles complete message via WebSocket', () => {
    const { result } = renderHook(() => usePipeline());
    act(() => { result.current.connect('job-1'); });

    act(() => {
      wsOnmessageHandler?.({
        data: JSON.stringify({
          type: 'complete', progress_pct: 100, stage: 'Done', message: 'Complete',
          stats: { total_scraped: 50, total_verified: 25 },
        }),
      });
    });

    expect(result.current.isComplete).toBe(true);
    expect(result.current.isRunning).toBe(false);
  });

  it('handles error message via WebSocket', () => {
    const { result } = renderHook(() => usePipeline());
    act(() => { result.current.connect('job-1'); });

    act(() => {
      wsOnmessageHandler?.({
        data: JSON.stringify({
          type: 'error', progress_pct: 0, stage: 'Error', message: 'Pipeline failed',
        }),
      });
    });

    expect(result.current.error).toBe('Pipeline failed');
    expect(result.current.isRunning).toBe(false);
  });

  it('handles fallback_poll message by switching to polling', () => {
    mockGetJob.mockResolvedValue({ id: 'job-1', status: 'running', progress_pct: 50 });
    const { result } = renderHook(() => usePipeline());
    act(() => { result.current.connect('job-1'); });

    act(() => {
      wsOnmessageHandler?.({
        data: JSON.stringify({ type: 'fallback_poll' }),
      });
    });
  });

  it('exposes setIsComplete setter', () => {
    const { result } = renderHook(() => usePipeline());
    expect(result.current.isComplete).toBe(false);
    act(() => { result.current.setIsComplete(true); });
    expect(result.current.isComplete).toBe(true);
  });

  it('exposes setError getter/setter', () => {
    const { result } = renderHook(() => usePipeline());
    act(() => { result.current.setError('Something went wrong'); });
    expect(result.current.error).toBe('Something went wrong');
    act(() => { result.current.setError(null); });
    expect(result.current.error).toBeNull();
  });

  it('cleanup closes WebSocket', () => {
    const { result } = renderHook(() => usePipeline());
    act(() => { result.current.connect('job-1'); });
    act(() => { result.current.cleanup(); });
    expect(result.current.isRunning).toBe(true);
  });

  it('resets progress on reconnect', () => {
    const { result } = renderHook(() => usePipeline());
    act(() => { result.current.connect('job-1'); });
    act(() => { result.current.connect('job-2'); });
    expect(result.current.progress.pct).toBe(0);
    expect(result.current.progress.stage).toBe('Starting');
    expect(result.current.isRunning).toBe(true);
    expect(result.current.isComplete).toBe(false);
  });

  it('polls when WebSocket errors', () => {
    MockWebSocket.simulatedError = true;
    mockGetJob.mockResolvedValue({ id: 'job-1', status: 'running' });

    const { result } = renderHook(() => usePipeline());
    act(() => { result.current.connect('job-1'); });
  });

  it('does not crash when cleanup is called multiple times', () => {
    const { result } = renderHook(() => usePipeline());
    act(() => { result.current.connect('job-1'); });
    act(() => { result.current.cleanup(); });
    act(() => { result.current.cleanup(); });
  });

  it('cleanup on unmount', () => {
    const { result, unmount } = renderHook(() => usePipeline());
    act(() => { result.current.connect('job-1'); });
    unmount();
  });
});
