'use client';

import { useState } from 'react';
import type { Lead } from '@/types';
import { openExport, getExportUrl } from '@/utils/api';
import { RefreshIcon, EmptyIcon } from './Icons';

interface ResultsSectionProps {
  jobId: string;
  leads: Lead[];
  total: number;
  page: number;
  pageSize: number;
  tab: string;
  hasEmail: boolean;
  minScore: number;
  loading: boolean;
  onTabChange: (tab: string) => void;
  onPageChange: (page: number) => void;
  onRefresh: () => void;
  onHasEmailChange: (v: boolean) => void;
  onMinScoreChange: (v: number) => void;
  onApplyFilters: () => void;
}

const TABS = [
  { key: 'no_website', label: 'No Website' },
  { key: 'has_gmaps', label: 'Has Maps' },
  { key: 'owner_found', label: 'Owner Found' },
  { key: 'high_value', label: 'High Value' },
  { key: 'all', label: 'All' },
];

const totalPages = (total: number, pageSize: number) => Math.max(1, Math.ceil(total / pageSize));

export default function ResultsSection({
  jobId, leads, total, page, pageSize, tab, hasEmail, minScore,
  loading, onTabChange, onPageChange, onRefresh,
  onHasEmailChange, onMinScoreChange, onApplyFilters,
}: ResultsSectionProps) {
  const [scoreExpanded, setScoreExpanded] = useState(false);
  const pages = totalPages(total, pageSize);

  return (
    <section className="results-section" id="results-section">
      <div className="results-card">
        <div className="results-header">
          <h3 className="results-title">Results</h3>
          <div className="results-actions">
            <div className="export-group">
              <button className="btn-export" onClick={() => openExport(jobId, 'xlsx')}>
                <span>Export XLSX</span>
              </button>
              <button className="btn-export" onClick={() => openExport(jobId, 'pdf')}>
                <span>Export PDF</span>
              </button>
            </div>
            <button className="btn-refresh" onClick={onRefresh} title="Refresh">
              <RefreshIcon />
            </button>
          </div>
        </div>

        <div className="results-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={`tab-btn${tab === t.key ? ' active' : ''}`}
              onClick={() => onTabChange(t.key)}
              role="tab"
              aria-selected={tab === t.key}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="results-filters">
          <label className="filter-checkbox">
            <input type="checkbox" checked={hasEmail} onChange={(e) => onHasEmailChange(e.target.checked)} />
            <span>Has Email Only</span>
          </label>
          <div className="filter-score">
            <label onClick={() => setScoreExpanded(!scoreExpanded)} style={{ cursor: 'pointer' }}>
              Min Score: {minScore}
            </label>
            {scoreExpanded && (
              <div style={{ marginTop: 6 }}>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={minScore}
                  onChange={(e) => onMinScoreChange(Number(e.target.value))}
                  className="slider"
                />
              </div>
            )}
          </div>
          <button className="btn-filter-apply" onClick={onApplyFilters}>Apply</button>
        </div>

        <div className="results-total">
          {loading ? 'Loading...' : `${total} result${total !== 1 ? 's' : ''} found`}
        </div>

        {loading ? (
          <div className="table-empty">
            <div className="spinner" />
            <p>Loading results...</p>
          </div>
        ) : leads.length === 0 ? (
          <div className="table-empty">
            <EmptyIcon />
            <h4>No results match your filters</h4>
            <p>Try adjusting the category or score threshold.</p>
          </div>
        ) : (
          <div className="table-responsive">
            <table className="results-table">
              <thead>
                <tr>
                  <th>Business</th>
                  <th>Score</th>
                  <th>Confidence</th>
                  <th>Phone</th>
                  <th>Owner Contact</th>
                  <th>Email</th>
                  <th>Website / Maps</th>
                  <th>Sources</th>
                </tr>
              </thead>
              <tbody>
                {leads.map((lead, i) => {
                  const name = lead.name || lead.business_name || 'Unknown';
                  const score = lead.lead_score ?? lead.score ?? 0;
                  const email = lead.owner_email || lead.email || '';
                  const owner = lead.owner_name || lead.owner || '';
                  const phone = lead.phone_formatted || lead.phone || '';
                  const conf = Math.round((lead.data_confidence ?? 0) * 100);
                  const ownerConf = Math.round((lead.owner_phone_confidence ?? 0) * 100);
                  const scoreClass = score >= 70 ? 'score-high' : score >= 40 ? 'score-mid' : 'score-low';
                  const sources = (lead.sources || lead.source || '').split(',').filter(Boolean);
                  let host = '';
                  try { host = lead.website ? new URL(lead.website).hostname : ''; } catch { host = lead.website || ''; }
                  return (
                    <tr key={lead.id || i}>
                      <td>
                        <div className="td-name">{name}</div>
                        <div className="td-meta">
                          {lead.business_category ? <span className="badge badge-cat">{lead.business_category}</span> : null}
                          {' '}{lead.address || lead.city || ''}
                        </div>
                      </td>
                      <td>
                        <span className={`score-badge ${scoreClass}`}>{score}</span>
                        <div className="score-bar-track">
                          <div className="score-bar-fill" style={{ width: `${score}%` }} />
                        </div>
                      </td>
                      <td>
                        <div className="score-bar-track" title={`${conf}% confidence`}>
                          <div className="score-bar-fill" style={{ width: `${conf}%` }} />
                        </div>
                        <span className="td-meta">{conf}%</span>
                      </td>
                      <td>
                        {phone ? (
                          <a href={`tel:${phone}`} className="external-link">{phone}</a>
                        ) : <span className="muted-cell">{'\u2014'}</span>}
                        {lead.phone_verified ? <span className="verify-pill ok" title="Verified">{'\u2713'}</span> : null}
                      </td>
                      <td>
                        {owner ? <div className="td-name" style={{ fontSize: 13 }}>{owner}</div> : null}
                        {lead.owner_phone ? (
                          <div className="owner-phone" title={`${lead.owner_phone_type || ''} \u00b7 ${ownerConf}% confidence`}>
                            {'\ud83d\udcf1'} <a href={`tel:${lead.owner_phone}`}>{lead.owner_phone}</a>
                            <span className="conf-chip">{ownerConf}%</span>
                          </div>
                        ) : (!owner ? <span className="muted-cell">{'\u2014'}</span> : null)}
                      </td>
                      <td>
                        {email ? (
                          <a href={`mailto:${email}`} className="email-link">{email}</a>
                        ) : <span className="muted-cell">{'\u2014'}</span>}
                        {email && lead.email_verified ? <span className="verify-pill ok" title="MX verified">{'\u2713'}</span> : null}
                      </td>
                      <td>
                        {lead.website ? (
                          <a href={lead.website} target="_blank" rel="noopener noreferrer" className="external-link">{host}</a>
                        ) : <span className="muted-cell">No website</span>}
                        {lead.has_google_maps ? (
                          <div className="td-meta">
                            {lead.google_maps_url
                              ? <a href={lead.google_maps_url} target="_blank" rel="noopener noreferrer">{'\u2b50'} {lead.google_rating ?? '\u2014'} ({lead.google_review_count ?? 0})</a>
                              : <>{'\u2b50'} {lead.google_rating ?? '\u2014'} ({lead.google_review_count ?? 0})</>}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        {sources.length
                          ? sources.map((s) => <span key={s} className={`badge badge-source src-${s}`}>{s}</span>)
                          : <span className="muted-cell">{'\u2014'}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {pages > 1 && (
          <div className="pagination">
            <button disabled={page <= 1} onClick={() => onPageChange(page - 1)} className="btn-page">
              Previous
            </button>
            <div className="page-info">
              {Array.from({ length: Math.min(pages, 5) }, (_, i) => {
                let p: number;
                if (pages <= 5) {
                  p = i + 1;
                } else if (page <= 3) {
                  p = i + 1;
                } else if (page >= pages - 2) {
                  p = pages - 4 + i;
                } else {
                  p = page - 2 + i;
                }
                return (
                  <button
                    key={p}
                    className={`btn-page${page === p ? ' active' : ''}`}
                    onClick={() => onPageChange(p)}
                  >
                    {p}
                  </button>
                );
              })}
            </div>
            <button disabled={page >= pages} onClick={() => onPageChange(page + 1)} className="btn-page">
              Next
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
