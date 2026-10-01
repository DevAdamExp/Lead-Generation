'use client';

import { LogoIcon, SearchIcon, HistoryIcon, ResultIcon } from './Icons';

interface SidebarProps {
  activeSection: string;
  hasResults: boolean;
  onNavigate: (section: string) => void;
  isOpen: boolean;
  onClose: () => void;
}

export default function Sidebar({ activeSection, hasResults, onNavigate, isOpen, onClose }: SidebarProps) {
  const handleNav = (section: string) => {
    onNavigate(section);
    onClose();
  };

  return (
    <aside className={`sidebar${isOpen ? ' open' : ''}`} role="navigation" aria-label="Main navigation">
      <div className="sidebar-brand">
        <div className="sidebar-brand-icon">
          <LogoIcon />
        </div>
        <span className="sidebar-brand-text">Lead<span className="sidebar-brand-accent">Gen</span></span>
      </div>

      <nav className="sidebar-nav">
        <button
          className={`nav-item${activeSection === 'search' ? ' active' : ''}`}
          onClick={() => handleNav('search')}
        >
          <SearchIcon />
          Search Leads
        </button>
        <button
          className={`nav-item${activeSection === 'history' ? ' active' : ''}`}
          onClick={() => handleNav('history')}
        >
          <HistoryIcon />
          Job History
        </button>
        <button
          className={`nav-item${activeSection === 'results' ? ' active' : ''}`}
          onClick={() => handleNav('results')}
          style={{ display: hasResults ? 'flex' : 'none' }}
        >
          <ResultIcon />
          Results
        </button>
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-footer-text">Lead Intelligence Platform<br />v2.0.0</div>
      </div>
    </aside>
  );
}
