'use client';

import { useState, useCallback } from 'react';
import type { Job } from '@/types';
import { createJob } from '@/utils/api';
import { usePipeline } from '@/hooks/usePipeline';
import { useLeads } from '@/hooks/useLeads';
import type { SearchFormData } from '@/types';

import Topbar from '@/components/Topbar';
import Layout from '@/components/Layout';
import HeroSection from '@/components/HeroSection';
import SearchCard from '@/components/SearchCard';
import ProgressSection from '@/components/ProgressSection';
import ResultsSection from '@/components/ResultsSection';
import JobHistory from '@/components/JobHistory';
import Toast from '@/components/Toast';

export default function Home() {
  const [activeSection, setActiveSection] = useState('search');
  const [currentJob, setCurrentJob] = useState<Job | null>(null);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'info' } | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const { progress, isRunning, isComplete, error, connect, cleanup, setError, setIsComplete } = usePipeline();
  const leadsHook = useLeads(currentJob?.id ?? null);

  const handleStart = useCallback(async (data: SearchFormData) => {
    cleanup();
    setIsComplete(false);
    setError(null);

    try {
      const job = await createJob(data);
      setCurrentJob(job);
      setActiveSection('results');
      connect(job.id);
      setToast({ message: 'Lead search started!', type: 'success' });
      setRefreshKey((k) => k + 1);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to create job';
      setToast({ message: msg, type: 'error' });
    }
  }, [cleanup, connect, setError, setIsComplete]);

  const handleJobSelect = useCallback((job: Job) => {
    setCurrentJob(job);
    setActiveSection('results');
    leadsHook.fetchLeads();
  }, [leadsHook]);

  const handleNavigate = useCallback((section: string) => {
    setActiveSection(section);
    if (section === 'history') {
      setRefreshKey((k) => k + 1);
    }
  }, []);

  const sections: Record<string, React.ReactNode> = {
    search: (
      <>
        <HeroSection />
        <SearchCard onStart={handleStart} isRunning={isRunning} />
      </>
    ),
    history: (
      <JobHistory onSelect={handleJobSelect} refreshKey={refreshKey} />
    ),
    results: (
      <>
        {isRunning && (
          <ProgressSection
            pct={progress.pct}
            stage={progress.stage}
            message={progress.message}
            scraped={progress.scraped}
            verified={progress.verified}
          />
        )}
        {currentJob && (isComplete || !isRunning) && (
          <ResultsSection
            jobId={currentJob.id}
            leads={leadsHook.leads}
            total={leadsHook.total}
            page={leadsHook.page}
            pageSize={50}
            tab={leadsHook.tab}
            hasEmail={leadsHook.hasEmail}
            minScore={leadsHook.minScore}
            loading={leadsHook.loading}
            onTabChange={leadsHook.changeTab}
            onPageChange={leadsHook.changePage}
            onRefresh={leadsHook.fetchLeads}
            onHasEmailChange={leadsHook.setHasEmail}
            onMinScoreChange={leadsHook.setMinScore}
            onApplyFilters={leadsHook.applyFilters}
          />
        )}
      </>
    ),
  };

  return (
    <Layout
      hasResults={!!currentJob && isComplete}
      activeSection={activeSection}
      onNavigate={handleNavigate}
    >
      <Topbar
        title={activeSection === 'search' ? 'Lead Search' : activeSection === 'history' ? 'Job History' : 'Results'}
        onMenuToggle={() => {
          const sidebar = document.querySelector('.sidebar');
          sidebar?.classList.toggle('open');
        }}
      />
      <main className="content">
        {sections[activeSection] || sections.search}
      </main>
      <Toast
        message={toast?.message ?? null}
        type={toast?.type ?? 'info'}
        visible={!!toast}
        onClose={() => setToast(null)}
      />
    </Layout>
  );
}
