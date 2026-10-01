import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import HeroSection from '@/components/HeroSection';

describe('HeroSection', () => {
  it('renders main title lines', () => {
    render(<HeroSection />);
    expect(screen.getByText('Find & Verify')).toBeInTheDocument();
    expect(screen.getByText('Business Leads')).toBeInTheDocument();
  });

  it('renders accent span text', () => {
    render(<HeroSection />);
    expect(screen.getByText('Automatically')).toBeInTheDocument();
  });

  it('renders tagline', () => {
    render(<HeroSection />);
    expect(screen.getByText(/Hotfrog \+ Google Maps Pipeline/)).toBeInTheDocument();
  });

  it('renders pipeline stages', () => {
    render(<HeroSection />);
    expect(screen.getByText('01')).toBeInTheDocument();
    expect(screen.getByText('Scrape')).toBeInTheDocument();
    expect(screen.getByText('02')).toBeInTheDocument();
    expect(screen.getByText('Verify')).toBeInTheDocument();
    expect(screen.getByText('03')).toBeInTheDocument();
    expect(screen.getByText('Enrich')).toBeInTheDocument();
    expect(screen.getByText('04')).toBeInTheDocument();
    expect(screen.getByText('Score')).toBeInTheDocument();
    expect(screen.getByText('05')).toBeInTheDocument();
    expect(screen.getByText('Export')).toBeInTheDocument();
  });

  it('renders stat badges', () => {
    render(<HeroSection />);
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('Pipeline Stages')).toBeInTheDocument();
    expect(screen.getByText('100%')).toBeInTheDocument();
    expect(screen.getByText('Free & Open')).toBeInTheDocument();
    expect(screen.getByText('30')).toBeInTheDocument();
    expect(screen.getByText('Countries')).toBeInTheDocument();
  });

  it('renders subtext', () => {
    render(<HeroSection />);
    expect(screen.getByText(/all free, no API keys required/)).toBeInTheDocument();
  });

  it('renders step descriptions', () => {
    render(<HeroSection />);
    expect(screen.getByText('Hotfrog listings')).toBeInTheDocument();
    expect(screen.getByText('Maps + website')).toBeInTheDocument();
    expect(screen.getByText('Emails + owners')).toBeInTheDocument();
    expect(screen.getByText('Quality rubric')).toBeInTheDocument();
    expect(screen.getByText('XLSX + PDF')).toBeInTheDocument();
  });

  it('has ambient glow elements', () => {
    const { container } = render(<HeroSection />);
    expect(container.querySelector('.hero-bg-glow-1')).toBeInTheDocument();
    expect(container.querySelector('.hero-bg-glow-2')).toBeInTheDocument();
  });

  it('has noise texture overlay', () => {
    const { container } = render(<HeroSection />);
    expect(container.querySelector('.hero-noise')).toBeInTheDocument();
  });

  it('has pipeline track line', () => {
    const { container } = render(<HeroSection />);
    expect(container.querySelector('.pipeline-track')).toBeInTheDocument();
  });

  it('renders pipeline step nodes with check icons', () => {
    const { container } = render(<HeroSection />);
    const dots = container.querySelectorAll('.pipeline-step-dot');
    expect(dots).toHaveLength(5);
  });

  it('renders SVG icons in stat cards', () => {
    const { container } = render(<HeroSection />);
    const statIcons = container.querySelectorAll('.hero-stat-icon svg');
    expect(statIcons).toHaveLength(3);
  });
});
