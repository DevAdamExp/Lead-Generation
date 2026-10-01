'use client';

import { useState } from 'react';
import Sidebar from './Sidebar';

interface LayoutProps {
  children: React.ReactNode;
  hasResults: boolean;
  activeSection: string;
  onNavigate: (section: string) => void;
}

export default function Layout({ children, hasResults, activeSection, onNavigate }: LayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="app-layout">
      <Sidebar
        activeSection={activeSection}
        hasResults={hasResults}
        onNavigate={onNavigate}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />
      {sidebarOpen && <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} />}
      <div className="main-content">
        {children && (
          <div className="children-wrap" onClick={() => setSidebarOpen(false)}>
            {children}
          </div>
        )}
      </div>
    </div>
  );
}
