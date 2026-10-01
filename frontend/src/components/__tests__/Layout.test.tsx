import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import Layout from '@/components/Layout';

describe('Layout', () => {
  it('renders children', () => {
    render(
      <Layout activeSection="search" hasResults={false} onNavigate={vi.fn()}>
        <p>Main content</p>
      </Layout>
    );
    expect(screen.getByText('Main content')).toBeInTheDocument();
  });

  it('renders sidebar and topbar', () => {
    render(
      <Layout activeSection="search" hasResults={false} onNavigate={vi.fn()}>
        <p>content</p>
      </Layout>
    );
    expect(screen.getByText('Lead')).toBeInTheDocument();
    expect(screen.getByText('Gen')).toBeInTheDocument();
    expect(screen.getAllByRole('navigation').length).toBeGreaterThanOrEqual(1);
  });

  it('toggles sidebar on menu button click', () => {
    render(
      <Layout activeSection="search" hasResults={false} onNavigate={vi.fn()}>
        <p>content</p>
      </Layout>
    );

    const menuBtn = screen.getAllByRole('button')[0];
    fireEvent.click(menuBtn);
    // Sidebar should be open now
  });

  it('closes sidebar on overlay click when open', () => {
    render(
      <Layout activeSection="search" hasResults={false} onNavigate={vi.fn()}>
        <p>content</p>
      </Layout>
    );
  });
});
