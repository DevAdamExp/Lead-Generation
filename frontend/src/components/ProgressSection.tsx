'use client';

interface ProgressSectionProps {
  pct: number;
  stage: string;
  message: string;
  scraped: number;
  verified: number;
}

const STAGES = ['Scraping', 'Maps', 'Website', 'Contacts', 'Exporting'];

export default function ProgressSection({ pct, stage, message, scraped, verified }: ProgressSectionProps) {
  const activeIdx = STAGES.findIndex((s) => stage?.startsWith(s));

  return (
    <section className="progress-section">
      <div className="progress-card">
        <div className="progress-header">
          <div className="progress-title-group">
            <div className="spinner" />
            <h3 className="progress-title">{stage || 'Running Pipeline...'}</h3>
          </div>
          <div className="progress-percent">{pct}%</div>
        </div>

        <div className="stage-pipeline">
          {STAGES.map((s, i) => (
            <span key={s} style={{ display: 'contents' }}>
              {i > 0 && (
                <span className={`stage-connector${i <= activeIdx ? ' done' : ''}`} />
              )}
              <span className={`stage-node${i === activeIdx ? ' active' : ''}${i < activeIdx ? ' done' : ''}`}>
                <span className="stage-dot" />
                <span className="stage-label">{s}</span>
              </span>
            </span>
          ))}
        </div>

        <div className="progress-bar-track">
          <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
        </div>

        <div className="progress-message">{message || 'Starting...'}</div>

        <div className="live-stats">
          <div className="stat-card">
            <div className="stat-value">{scraped}</div>
            <div className="stat-label">Scraped</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{verified}</div>
            <div className="stat-label">Verified</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{stage || '\u2014'}</div>
            <div className="stat-label">Stage</div>
          </div>
        </div>
      </div>
    </section>
  );
}
