import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import Topbar from '@/components/Topbar';

describe('Topbar', () => {
  it('renders breadcrumb title', () => {
    render(<Topbar title="Search Leads" onMenuToggle={vi.fn()} />);
    expect(screen.getByText('Search Leads')).toBeInTheDocument();
  });

  it('calls onMenuToggle when menu button clicked', () => {
    const onMenuToggle = vi.fn();
    render(<Topbar title="Search" onMenuToggle={onMenuToggle} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onMenuToggle).toHaveBeenCalled();
  });

  it('renders free badge text', () => {
    render(<Topbar title="Results" onMenuToggle={vi.fn()} />);
    expect(screen.getByText(/All Free/)).toBeInTheDocument();
  });

  it('renders menu button with aria label', () => {
    render(<Topbar title="Search" onMenuToggle={vi.fn()} />);
    expect(screen.getByLabelText('Toggle menu')).toBeInTheDocument();
  });
});
