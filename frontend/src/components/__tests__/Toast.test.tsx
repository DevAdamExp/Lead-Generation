import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import Toast from '@/components/Toast';

describe('Toast', () => {
  it('renders nothing when not visible', () => {
    const { container } = render(
      <Toast message="test" type="info" visible={false} onClose={() => {}} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when message is null', () => {
    const { container } = render(
      <Toast message={null} type="info" visible={true} onClose={() => {}} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders success toast with checkmark', () => {
    render(<Toast message="Success!" type="success" visible={true} onClose={() => {}} />);
    expect(screen.getByText('Success!')).toBeInTheDocument();
    expect(screen.getByText('\u2713')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveClass('toast-success');
  });

  it('renders error toast with cross', () => {
    render(<Toast message="Error!" type="error" visible={true} onClose={() => {}} />);
    expect(screen.getByText('Error!')).toBeInTheDocument();
    expect(screen.getByText('\u2717')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveClass('toast-error');
  });

  it('renders info toast with info symbol', () => {
    render(<Toast message="Info" type="info" visible={true} onClose={() => {}} />);
    expect(screen.getByText('\u2139')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveClass('toast-info');
  });

  it('calls onClose when close button clicked', () => {
    const onClose = vi.fn();
    render(<Toast message="test" type="info" visible={true} onClose={onClose} />);
    fireEvent.click(screen.getByLabelText('Close'));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('auto-dismisses after 5 seconds', () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(<Toast message="test" type="info" visible={true} onClose={onClose} />);
    vi.advanceTimersByTime(5000);
    expect(onClose).toHaveBeenCalledOnce();
    vi.useRealTimers();
  });

  it('does not auto-dismiss when not visible', () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(<Toast message="test" type="info" visible={false} onClose={onClose} />);
    vi.advanceTimersByTime(5000);
    expect(onClose).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('does not set timer when message is null/empty even if visible', () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(<Toast message={null} type="info" visible={true} onClose={onClose} />);
    vi.advanceTimersByTime(5000);
    expect(onClose).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
