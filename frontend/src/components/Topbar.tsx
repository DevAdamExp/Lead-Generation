'use client';

import { MenuIcon } from './Icons';

interface TopbarProps {
  title: string;
  onMenuToggle: () => void;
}

export default function Topbar({ title, onMenuToggle }: TopbarProps) {
  return (
    <header className="topbar">
      <div className="topbar-left">
        <button className="menu-toggle" onClick={onMenuToggle} aria-label="Toggle menu">
          <MenuIcon />
        </button>
        <h1 className="topbar-title">{title}</h1>
      </div>
      <div className="topbar-right">
        <div className="topbar-badge">
          <span className="topbar-badge-dot" />
          <span>All Free &middot; No API Keys</span>
        </div>
      </div>
    </header>
  );
}
