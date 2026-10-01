import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import JobHistory from '@/components/JobHistory';
import { makeJob } from '@/test/mocks';

const mockGetJobs = vi.fn();
vi.mock('@/utils/api', () => ({
  getJobs: (...args: unknown[]) => mockGetJobs(...args),
}));

beforeEach(() => {
  mockGetJobs.mockReset();
});

describe('JobHistory', () => {
  it('shows loading state initially', () => {
    mockGetJobs.mockReturnValue(new Promise(() => {}));
    render(<JobHistory onSelect={vi.fn()} refreshKey={0} />);
    expect(screen.getByText('Loading history...')).toBeInTheDocument();
  });

  it('shows empty state when no jobs', async () => {
    mockGetJobs.mockResolvedValue([]);
    render(<JobHistory onSelect={vi.fn()} refreshKey={0} />);
    await waitFor(() => {
      expect(screen.getByText('No jobs yet')).toBeInTheDocument();
    });
  });

  it('renders job list sorted by date descending', async () => {
    const jobs = [
      makeJob({ id: '1', niche: 'plumbers', location: 'Austin', status: 'completed', total_leads: 10, created_at: '2026-06-20T12:00:00Z' }),
      makeJob({ id: '2', niche: 'dentists', location: 'Dallas', status: 'running', total_leads: 5, total_verified: 3, created_at: '2026-06-19T12:00:00Z' }),
      makeJob({ id: '3', niche: 'hvac', location: 'Houston', status: 'failed', total_leads: 0, created_at: '2026-06-18T12:00:00Z' }),
    ];
    mockGetJobs.mockResolvedValue(jobs);
    render(<JobHistory onSelect={vi.fn()} refreshKey={0} />);

    await waitFor(() => {
      expect(screen.getByText('plumbers in Austin')).toBeInTheDocument();
      expect(screen.getByText('dentists in Dallas')).toBeInTheDocument();
      expect(screen.getByText('hvac in Houston')).toBeInTheDocument();
    });
  });

  it('renders status badges with correct classes', async () => {
    const jobs = [
      makeJob({ id: '1', niche: 'a', location: 'b', status: 'completed' }),
      makeJob({ id: '2', niche: 'c', location: 'd', status: 'running' }),
      makeJob({ id: '3', niche: 'e', location: 'f', status: 'failed' }),
    ];
    mockGetJobs.mockResolvedValue(jobs);
    render(<JobHistory onSelect={vi.fn()} refreshKey={0} />);

    await waitFor(() => {
      const badges = screen.getAllByText(/completed|running|failed/);
      expect(badges).toHaveLength(3);
    });
  });

  it('calls onSelect when job is clicked', async () => {
    const onSelect = vi.fn();
    const job = makeJob({ id: '1', niche: 'plumbers', location: 'Austin', status: 'completed' });
    mockGetJobs.mockResolvedValue([job]);
    render(<JobHistory onSelect={onSelect} refreshKey={0} />);

    await waitFor(() => {
      fireEvent.click(screen.getByText('plumbers in Austin'));
    });
    expect(onSelect).toHaveBeenCalledWith(job);
  });

  it('shows verified count when available', async () => {
    mockGetJobs.mockResolvedValue([
      makeJob({ id: '1', niche: 'a', location: 'b', status: 'completed', total_leads: 10, total_verified: 7 }),
    ]);
    render(<JobHistory onSelect={vi.fn()} refreshKey={0} />);
    await waitFor(() => {
      expect(screen.getByText(/7 verified/)).toBeInTheDocument();
    });
  });

  it('refetches when refreshKey changes', async () => {
    mockGetJobs.mockResolvedValue([]);
    const { rerender } = render(<JobHistory onSelect={vi.fn()} refreshKey={0} />);
    await waitFor(() => expect(screen.getByText('No jobs yet')).toBeInTheDocument());

    mockGetJobs.mockResolvedValue([makeJob({ id: '1', niche: 'x', location: 'y', status: 'completed' })]);
    rerender(<JobHistory onSelect={vi.fn()} refreshKey={1} />);
    await waitFor(() => {
      expect(screen.getByText('x in y')).toBeInTheDocument();
    });
  });

  it('handles fetch error silently', async () => {
    mockGetJobs.mockRejectedValue(new Error('network error'));
    render(<JobHistory onSelect={vi.fn()} refreshKey={0} />);
    await waitFor(() => {
      expect(screen.getByText('No jobs yet')).toBeInTheDocument();
    });
  });
});
