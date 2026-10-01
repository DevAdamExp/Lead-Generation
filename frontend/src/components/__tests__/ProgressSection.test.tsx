import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import ProgressSection from '@/components/ProgressSection';

describe('ProgressSection', () => {
  const defaultProps = {
    pct: 45,
    stage: 'Scraping',
    message: 'Processing page 5 of 20...',
    scraped: 42,
    verified: 18,
  };

  it('renders percentage', () => {
    render(<ProgressSection {...defaultProps} />);
    expect(screen.getByText('45%')).toBeInTheDocument();
  });

  it('renders stage name', () => {
    render(<ProgressSection {...defaultProps} />);
    expect(screen.getAllByText('Scraping').length).toBeGreaterThanOrEqual(1);
  });

  it('renders progress message', () => {
    render(<ProgressSection {...defaultProps} />);
    expect(screen.getByText('Processing page 5 of 20...')).toBeInTheDocument();
  });

  it('renders scraped count', () => {
    render(<ProgressSection {...defaultProps} />);
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('Scraped')).toBeInTheDocument();
  });

  it('renders verified count', () => {
    render(<ProgressSection {...defaultProps} />);
    expect(screen.getByText('18')).toBeInTheDocument();
    expect(screen.getByText('Verified')).toBeInTheDocument();
  });

  it('renders all 5 pipeline stage nodes', () => {
    render(<ProgressSection {...defaultProps} />);
    expect(screen.getAllByText('Scraping').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Maps')).toBeInTheDocument();
    expect(screen.getByText('Website')).toBeInTheDocument();
    expect(screen.getByText('Contacts')).toBeInTheDocument();
    expect(screen.getByText('Exporting')).toBeInTheDocument();
  });

  it('uses fallback message when message is empty', () => {
    render(<ProgressSection {...defaultProps} message="" />);
    expect(screen.getByText('Starting...')).toBeInTheDocument();
  });

  it('uses fallback stage text when stage is empty', () => {
    render(<ProgressSection {...defaultProps} stage="" />);
    expect(screen.getByText('Running Pipeline...')).toBeInTheDocument();
  });

  it('renders progress bar with correct width', () => {
    const { container } = render(<ProgressSection {...defaultProps} />);
    const fill = container.querySelector('.progress-bar-fill') as HTMLElement;
    expect(fill.style.width).toBe('45%');
  });
});
