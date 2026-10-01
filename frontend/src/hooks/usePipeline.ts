'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import type { ProgressMessage } from '@/types';
import { getWsUrl } from '@/utils/api';

export function usePipeline() {
  const [progress, setProgress] = useState({
    pct: 0,
    stage: 'Starting',
    message: 'Initialising pipeline...',
    scraped: 0,
    verified: 0,
  });
  const [isRunning, setIsRunning] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const updateProgress = useCallback((pct: number, stage: string, message: string, scraped: number, verified: number) => {
    setProgress({ pct, stage, message, scraped, verified });
  }, []);

  const cleanup = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const connect = useCallback((jobId: string) => {
    cleanup();
    setIsRunning(true);
    setIsComplete(false);
    setError(null);
    setProgress({ pct: 0, stage: 'Starting', message: 'Initialising pipeline...', scraped: 0, verified: 0 });

    let wsLive = false;
    const ws = new WebSocket(getWsUrl(jobId));
    wsRef.current = ws;

    ws.onopen = () => { wsLive = true; };

    ws.onmessage = (e) => {
      const msg: ProgressMessage = JSON.parse(e.data);

      if (msg.type === 'fallback_poll') {
        ws.close();
        startPolling(jobId);
        return;
      }

      updateProgress(
        msg.progress_pct,
        msg.stage,
        msg.message,
        msg.stats?.total_scraped || 0,
        msg.stats?.total_verified || 0,
      );

      if (msg.type === 'complete') {
        setIsRunning(false);
        setIsComplete(true);
        cleanup();
      } else if (msg.type === 'error') {
        setIsRunning(false);
        setError(msg.message);
        cleanup();
      }
    };

    ws.onerror = () => {
      if (!wsLive) startPolling(jobId);
    };

    setTimeout(() => {
      if (!wsLive) startPolling(jobId);
    }, 3000);
  }, [updateProgress, cleanup]);

  const startPolling = useCallback((jobId: string) => {
    // Bounded. This used to poll every 2s with no ceiling, so a job whose worker
    // died kept a browser timer running indefinitely against a status that could
    // never change (six such jobs were found 16 days old).
    const startedAt = Date.now();
    const MAX_POLL_MS = 8_100_000; // just past the Celery hard time limit

    const iv = setInterval(async () => {
      if (Date.now() - startedAt > MAX_POLL_MS) {
        setIsRunning(false);
        setError('Stopped watching this job — it exceeded the maximum run time. Refresh to check its final status.');
        clearInterval(iv);
        return;
      }
      try {
        const { getJob } = await import('@/utils/api');
        const job = await getJob(jobId);
        updateProgress(
          job.progress_pct,
          job.current_stage,
          job.stage_message,
          job.total_scraped,
          job.total_verified,
        );
        if (job.status === 'completed') {
          setIsRunning(false);
          setIsComplete(true);
          clearInterval(iv);
        } else if (job.status === 'failed') {
          setIsRunning(false);
          setError(job.error_message || 'Pipeline failed');
          clearInterval(iv);
        }
      } catch { /* ignore */ }
    }, 2000);
    pollRef.current = iv;
  }, [updateProgress]);

  useEffect(() => {
    return cleanup;
  }, [cleanup]);

  return { progress, isRunning, isComplete, error, connect, cleanup, setError, setIsComplete };
}
