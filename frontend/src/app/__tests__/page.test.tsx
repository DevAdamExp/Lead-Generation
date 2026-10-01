import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Home from '@/app/page';

vi.mock('@/hooks/usePipeline', () => ({
  usePipeline: () => ({
    progress: { pct: 0, stage: 'Starting', message: 'Init', scraped: 0, verified: 0 },
    isRunning: false,
    isComplete: false,
    error: null,
    connect: vi.fn(),
    cleanup: vi.fn(),
    setError: vi.fn(),
    setIsComplete: vi.fn(),
  }),
}));

vi.mock('@/hooks/useLeads', () => ({
  useLeads: () => ({
    leads: [], total: 0, page: 1, loading: false, tab: 'all',
    hasEmail: false, minScore: 0,
    setHasEmail: vi.fn(), setMinScore: vi.fn(),
    fetchLeads: vi.fn(), changePage: vi.fn(), changeTab: vi.fn(), applyFilters: vi.fn(),
  }),
}));

describe('Home page', () => {
  it('renders HeroSection', () => {
    render(<Home />);
    expect(screen.getByText('Find & Verify')).toBeInTheDocument();
  });

  it('renders SearchCard', () => {
    render(<Home />);
    expect(screen.getByText('Start a Lead Search')).toBeInTheDocument();
  });

  it('renders sidebar', () => {
    render(<Home />);
    expect(screen.getByText('Lead')).toBeInTheDocument();
    expect(screen.getByText('Gen')).toBeInTheDocument();
  });

  it('renders search section by default', () => {
    render(<Home />);
    expect(screen.getByText('Start a Lead Search')).toBeInTheDocument();
  });
});
