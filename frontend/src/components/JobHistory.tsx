'use client';

import { useEffect, useState } from 'react';
import type { Job } from '@/types';
import { getJobs } from '@/utils/api';
import { HistoryIcon, RefreshIcon } from './Icons';

interface JobHistoryProps {
  onSelect: (job: Job) => void;
  refreshKey: number;
}

export default function JobHistory({ onSelect, refreshKey }: JobHistoryProps) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchJobs = async () => {
    setLoading(true);
    try {
      const data = await getJobs();
      const sorted = data.sort((a, b) => new Date(b.created_at || '').getTime() - new Date(a.created_at || '').getTime());
      setJobs(sorted);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchJobs(); }, [refreshKey]);

  const statusClass = (status: string) => {
    switch (status) {
      case 'completed': return 'status-completed';
      case 'running': return 'status-running';
      case 'failed': return 'status-failed';
      case 'queued': return 'status-queued';
      default: return '';
    }
  };

  return (
    <section className="history-section">
      <div className="history-card">
        <div className="history-header">
          <h3 className="history-title">Job History</h3>
          <button className="btn-refresh" onClick={fetchJobs} title="Refresh jobs">
            <RefreshIcon />
          </button>
        </div>

        {loading ? (
          <div className="table-empty">
            <div className="spinner" />
            <p>Loading history...</p>
          </div>
        ) : jobs.length === 0 ? (
          <div className="table-empty">
            <HistoryIcon width={48} height={48} />
            <h4>No jobs yet</h4>
            <p>Start a lead search to see your job history here.</p>
          </div>
        ) : (
          <div className="history-list">
            {jobs.map((job) => (
              <div
                key={job.id}
                className="history-item clickable"
                onClick={() => onSelect(job)}
              >
                <div className="history-item-main">
                  <div className="history-item-title">
                    {job.niche} in {job.location}
                  </div>
                  <div className="history-item-meta">
                    {job.created_at && new Date(job.created_at).toLocaleString()}
                    {' \u00b7 '}
                    {job.total_leads} leads
                    {job.total_verified > 0 && ` \u00b7 ${job.total_verified} verified`}
                  </div>
                </div>
                <div className={`history-item-status ${statusClass(job.status)}`}>
                  {job.status}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
