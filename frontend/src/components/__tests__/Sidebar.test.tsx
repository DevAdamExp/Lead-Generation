import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import Sidebar from '@/components/Sidebar';

describe('Sidebar', () => {
  const defaultProps = {
    activeSection: 'search',
    hasResults: false,
    onNavigate: vi.fn(),
    isOpen: true,
    onClose: vi.fn(),
  };

  it('renders brand text', () => {
    render(<Sidebar {...defaultProps} />);
    expect(screen.getByText('Lead')).toBeInTheDocument();
    expect(screen.getByText('Gen')).toBeInTheDocument();
  });

  it('renders navigation buttons', () => {
    render(<Sidebar {...defaultProps} />);
    expect(screen.getByText('Search Leads')).toBeInTheDocument();
    expect(screen.getByText('Job History')).toBeInTheDocument();
  });

  it('shows Results button when hasResults is true', () => {
    const { rerender } = render(<Sidebar {...defaultProps} hasResults={false} />);
    expect(screen.getByText('Results').closest('button')?.style.display).toBe('none');

    rerender(<Sidebar {...defaultProps} hasResults={true} />);
    expect(screen.getByText('Results').closest('button')?.style.display).toBe('flex');
  });

  it('highlights active section', () => {
    const { rerender } = render(<Sidebar {...defaultProps} activeSection="search" />);
    expect(screen.getByText('Search Leads').closest('button')).toHaveClass('active');

    rerender(<Sidebar {...defaultProps} activeSection="history" />);
    expect(screen.getByText('Search Leads').closest('button')).not.toHaveClass('active');
    expect(screen.getByText('Job History').closest('button')).toHaveClass('active');
  });

  it('calls onNavigate and onClose when nav item clicked', () => {
    const onNavigate = vi.fn();
    const onClose = vi.fn();
    render(<Sidebar {...defaultProps} onNavigate={onNavigate} onClose={onClose} />);

    fireEvent.click(screen.getByText('Search Leads'));
    expect(onNavigate).toHaveBeenCalledWith('search');
    expect(onClose).toHaveBeenCalled();
  });

  it('applies open class when isOpen is true', () => {
    const { container } = render(<Sidebar {...defaultProps} isOpen={true} />);
    expect(container.querySelector('aside')).toHaveClass('open');
  });

  it('does not apply open class when isOpen is false', () => {
    const { container } = render(<Sidebar {...defaultProps} isOpen={false} />);
    expect(container.querySelector('aside')).not.toHaveClass('open');
  });

  it('renders version text in footer', () => {
    render(<Sidebar {...defaultProps} />);
    expect(screen.getByText(/v2\.0\.0/)).toBeInTheDocument();
  });

  it('has navigation role and aria-label', () => {
    render(<Sidebar {...defaultProps} />);
    const navs = screen.getAllByRole('navigation');
    expect(navs[0]).toHaveAttribute('aria-label', 'Main navigation');
  });
});
