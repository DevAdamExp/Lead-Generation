import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import ResultsSection from '@/components/ResultsSection';
import { makeLead } from '@/test/mocks';

vi.mock('@/utils/api', () => ({
  openExport: vi.fn(),
  getExportUrl: vi.fn(() => 'http://localhost:8000/export'),
}));

const defaultProps = {
  jobId: 'job-1',
  leads: [makeLead()],
  total: 1,
  page: 1,
  pageSize: 50,
  tab: 'all',
  hasEmail: false,
  minScore: 0,
  loading: false,
  onTabChange: vi.fn(),
  onPageChange: vi.fn(),
  onRefresh: vi.fn(),
  onHasEmailChange: vi.fn(),
  onMinScoreChange: vi.fn(),
  onApplyFilters: vi.fn(),
};

describe('ResultsSection', () => {
  it('renders results title', () => {
    render(<ResultsSection {...defaultProps} />);
    expect(screen.getByText('Results')).toBeInTheDocument();
  });

  it('renders all tab buttons', () => {
    render(<ResultsSection {...defaultProps} />);
    expect(screen.getByText('No Website')).toBeInTheDocument();
    expect(screen.getByText('Has Maps')).toBeInTheDocument();
    expect(screen.getByText('Owner Found')).toBeInTheDocument();
    expect(screen.getByText('High Value')).toBeInTheDocument();
    expect(screen.getByText('All')).toBeInTheDocument();
  });

  it('highlights active tab', () => {
    render(<ResultsSection {...defaultProps} tab="has_gmaps" />);
    expect(screen.getByText('Has Maps')).toHaveAttribute('aria-selected', 'true');
  });

  it('calls onTabChange when tab clicked', () => {
    const onTabChange = vi.fn();
    render(<ResultsSection {...defaultProps} onTabChange={onTabChange} />);
    fireEvent.click(screen.getByText('No Website'));
    expect(onTabChange).toHaveBeenCalledWith('no_website');
  });

  it('shows total results count', () => {
    render(<ResultsSection {...defaultProps} total={42} />);
    expect(screen.getByText('42 results found')).toBeInTheDocument();
  });

  it('shows singular result count', () => {
    render(<ResultsSection {...defaultProps} total={1} />);
    expect(screen.getByText('1 result found')).toBeInTheDocument();
  });

  it('renders leads in table', () => {
    render(<ResultsSection {...defaultProps} />);
    expect(screen.getByText('Acme Plumbing')).toBeInTheDocument();
    expect(screen.getByText('john@acmeplumbing.com')).toBeInTheDocument();
    expect(screen.getByText('85')).toBeInTheDocument();
    expect(screen.getByText('John Doe')).toBeInTheDocument();
  });

  it('renders table headers', () => {
    render(<ResultsSection {...defaultProps} />);
    expect(screen.getByText('Business')).toBeInTheDocument();
    expect(screen.getByText('Score')).toBeInTheDocument();
    expect(screen.getByText('Confidence')).toBeInTheDocument();
    expect(screen.getByText('Phone')).toBeInTheDocument();
    expect(screen.getByText('Owner Contact')).toBeInTheDocument();
    expect(screen.getByText('Email')).toBeInTheDocument();
    expect(screen.getByText('Website / Maps')).toBeInTheDocument();
    expect(screen.getByText('Sources')).toBeInTheDocument();
  });

  it('shows empty state when no leads', () => {
    render(<ResultsSection {...defaultProps} leads={[]} total={0} />);
    expect(screen.getByText('No results match your filters')).toBeInTheDocument();
  });

  it('shows loading spinner when loading', () => {
    render(<ResultsSection {...defaultProps} loading={true} />);
    expect(screen.getByText('Loading...')).toBeInTheDocument();
    expect(screen.getByText('Loading results...')).toBeInTheDocument();
  });

  it('renders website as link with hostname', () => {
    render(<ResultsSection {...defaultProps} />);
    const link = screen.getByText('acmeplumbing.com');
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', 'https://acmeplumbing.com');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('shows em dash when no website', () => {
    const { container } = render(<ResultsSection {...defaultProps} leads={[makeLead({ website: '' })]} />);
    const mutedCells = container.querySelectorAll('.muted-cell');
    expect(mutedCells.length).toBeGreaterThanOrEqual(1);
  });

  it('shows em dash when no email', () => {
    const { container } = render(<ResultsSection {...defaultProps} leads={[makeLead({ email: '', owner_email: '' })]} />);
    const mutedCells = container.querySelectorAll('.muted-cell');
    expect(mutedCells.length).toBeGreaterThanOrEqual(1);
  });

  it('renders pagination when multiple pages', () => {
    const leads = Array.from({ length: 60 }, (_, i) => makeLead({ id: `l${i}`, business_name: `Biz ${i}` }));
    render(<ResultsSection {...defaultProps} leads={leads} total={60} page={1} />);
    expect(screen.getByText('Previous')).toBeInTheDocument();
    expect(screen.getByText('Next')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('does not show pagination when single page', () => {
    render(<ResultsSection {...defaultProps} total={1} leads={[makeLead()]} />);
    expect(screen.queryByText('Previous')).not.toBeInTheDocument();
    expect(screen.queryByText('Next')).not.toBeInTheDocument();
  });

  it('disables Previous on first page', () => {
    const leads = Array.from({ length: 60 }, (_, i) => makeLead({ id: `l${i}` }));
    render(<ResultsSection {...defaultProps} leads={leads} total={60} page={1} />);
    expect(screen.getByText('Previous')).toBeDisabled();
  });

  it('disables Next on last page', () => {
    const leads = Array.from({ length: 60 }, (_, i) => makeLead({ id: `l${i}` }));
    render(<ResultsSection {...defaultProps} leads={leads} total={60} page={2} pageSize={50} />);
    expect(screen.getByText('Next')).toBeDisabled();
  });

  it('calls onPageChange when page button clicked', () => {
    const onPageChange = vi.fn();
    const leads = Array.from({ length: 60 }, (_, i) => makeLead({ id: `l${i}` }));
    render(<ResultsSection {...defaultProps} leads={leads} total={60} onPageChange={onPageChange} />);
    fireEvent.click(screen.getByText('2'));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it('has email filter checkbox', () => {
    const onHasEmailChange = vi.fn();
    render(<ResultsSection {...defaultProps} onHasEmailChange={onHasEmailChange} />);
    const checkbox = screen.getByText('Has Email Only').previousElementSibling as HTMLInputElement;
    expect(checkbox.type).toBe('checkbox');
    fireEvent.click(checkbox);
    expect(onHasEmailChange).toHaveBeenCalledWith(true);
  });

  it('shows min score and apply button', () => {
    render(<ResultsSection {...defaultProps} />);
    expect(screen.getByText('Apply')).toBeInTheDocument();
  });

  it('calls onApplyFilters when apply clicked', () => {
    const onApplyFilters = vi.fn();
    render(<ResultsSection {...defaultProps} onApplyFilters={onApplyFilters} />);
    fireEvent.click(screen.getByText('Apply'));
    expect(onApplyFilters).toHaveBeenCalled();
  });
});
