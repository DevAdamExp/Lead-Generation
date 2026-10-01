'use client';

import { useState, useCallback } from 'react';
import type { Lead } from '@/types';
import { getLeads } from '@/utils/api';

export function useLeads(jobId: string | null) {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState('no_website');
  const [hasEmail, setHasEmail] = useState(false);
  const [minScore, setMinScore] = useState(0);

  const fetchLeads = useCallback(async (p?: number) => {
    if (!jobId) return;
    setLoading(true);
    try {
      const currentPage = p ?? page;
      const cat = tab === 'all' ? '' : tab;
      const data = await getLeads(jobId, currentPage, 50, minScore, cat, hasEmail);
      setLeads(data.items);
      setTotal(data.total);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [jobId, page, tab, hasEmail, minScore]);

  const changePage = useCallback((n: number) => {
    setPage(n);
    fetchLeads(n);
  }, [fetchLeads]);

  const changeTab = useCallback((t: string) => {
    setTab(t);
    setPage(1);
    setTimeout(() => fetchLeads(1), 0);
  }, [fetchLeads]);

  const applyFilters = useCallback(() => {
    setPage(1);
    fetchLeads(1);
  }, [fetchLeads]);

  return {
    leads, total, page, loading, tab, hasEmail, minScore,
    setHasEmail, setMinScore, fetchLeads, changePage, changeTab, applyFilters,
  };
}
