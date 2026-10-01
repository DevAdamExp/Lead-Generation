'use client';

export default function HeroSection() {
  return (
    <section className="hero">
      <div className="hero-bg-glow hero-bg-glow-1" aria-hidden />
      <div className="hero-bg-glow hero-bg-glow-2" aria-hidden />
      <div className="hero-noise" aria-hidden />

      <div className="hero-content">
        <div className="hero-tag">
          <span className="hero-tag-pulse" />
          <span className="hero-tag-text">Hotfrog + Google Maps Pipeline</span>
        </div>

        <h1 className="hero-title">
          <span className="hero-title-line hero-title-line-1">Find &amp; Verify</span>
          <span className="hero-title-line hero-title-line-2">
            Business Leads <span className="hero-title-accent">Automatically</span>
          </span>
        </h1>

        <p className="hero-sub">
          Scrape Hotfrog listings, verify Google Maps ratings, extract emails,
          and score quality — all free, no API keys required.
        </p>

        <div className="hero-stats">
          <div className="hero-stat">
            <span className="hero-stat-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
              </svg>
            </span>
            <span className="hero-stat-value">5</span>
            <span className="hero-stat-label">Pipeline Stages</span>
          </div>
          <div className="hero-stat-divider" />
          <div className="hero-stat">
            <span className="hero-stat-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              </svg>
            </span>
            <span className="hero-stat-value">100%</span>
            <span className="hero-stat-label">Free &amp; Open</span>
          </div>
          <div className="hero-stat-divider" />
          <div className="hero-stat">
            <span className="hero-stat-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" /><line x1="2" y1="12" x2="22" y2="12" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
              </svg>
            </span>
            <span className="hero-stat-value">30</span>
            <span className="hero-stat-label">Countries</span>
          </div>
        </div>

        <div className="hero-pipeline">
          <div className="pipeline-track" aria-hidden />
          {[
            { num: '01', label: 'Scrape', desc: 'Hotfrog listings' },
            { num: '02', label: 'Verify', desc: 'Maps + website' },
            { num: '03', label: 'Enrich', desc: 'Emails + owners' },
            { num: '04', label: 'Score', desc: 'Quality rubric' },
            { num: '05', label: 'Export', desc: 'XLSX + PDF' },
          ].map((step, i) => (
            <div key={step.num} className="pipeline-step" style={{ '--i': i } as React.CSSProperties}>
              <div className="pipeline-step-node">
                <div className="pipeline-step-ring" />
                <div className="pipeline-step-dot">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                </div>
              </div>
              <div className="pipeline-step-info">
                <span className="pipeline-step-number">{step.num}</span>
                <span className="pipeline-step-label">{step.label}</span>
                <span className="pipeline-step-desc">{step.desc}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
