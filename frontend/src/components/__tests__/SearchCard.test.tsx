import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import SearchCard from '@/components/SearchCard';

describe('SearchCard', () => {
  it('renders form fields', () => {
    render(<SearchCard onStart={vi.fn()} isRunning={false} />);
    expect(screen.getByLabelText('Business Niche')).toBeInTheDocument();
    expect(screen.getByLabelText('City / Location')).toBeInTheDocument();
    expect(screen.getByLabelText('Country')).toBeInTheDocument();
    expect(screen.getByLabelText(/Number of Leads/)).toBeInTheDocument();
  });

  it('calls onStart with form data on submit', async () => {
    const onStart = vi.fn();
    const user = userEvent.setup();
    render(<SearchCard onStart={onStart} isRunning={false} />);

    await user.type(screen.getByLabelText('Business Niche'), 'plumbers');
    await user.type(screen.getByLabelText('City / Location'), 'Austin');
    await user.click(screen.getByText('Find Leads'));

    expect(onStart).toHaveBeenCalledWith({
      niche: 'plumbers',
      location: 'Austin',
      country: 'us',
      limit: 50,
    });
  });

  it('does not call onStart when niche is empty', () => {
    const onStart = vi.fn();
    render(<SearchCard onStart={onStart} isRunning={false} />);

    fireEvent.change(screen.getByLabelText('City / Location'), { target: { value: 'Austin' } });
    fireEvent.submit(screen.getByRole('button', { name: /Find Leads/ }).closest('form')!);

    expect(onStart).not.toHaveBeenCalled();
  });

  it('does not call onStart when location is empty', () => {
    const onStart = vi.fn();
    render(<SearchCard onStart={onStart} isRunning={false} />);

    fireEvent.change(screen.getByLabelText('Business Niche'), { target: { value: 'plumbers' } });
    fireEvent.submit(screen.getByRole('button', { name: /Find Leads/ }).closest('form')!);

    expect(onStart).not.toHaveBeenCalled();
  });

  it('disables submit button while running', () => {
    render(<SearchCard onStart={vi.fn()} isRunning={true} />);
    expect(screen.getByText('Running...')).toBeInTheDocument();
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('trims whitespace from input values', async () => {
    const onStart = vi.fn();
    const user = userEvent.setup();
    render(<SearchCard onStart={onStart} isRunning={false} />);

    await user.type(screen.getByLabelText('Business Niche'), '  plumbers  ');
    await user.type(screen.getByLabelText('City / Location'), '  Austin  ');
    await user.click(screen.getByText('Find Leads'));

    expect(onStart).toHaveBeenCalledWith({
      niche: 'plumbers',
      location: 'Austin',
      country: 'us',
      limit: 50,
    });
  });

  it('updates limit slider value display', async () => {
    const user = userEvent.setup();
    render(<SearchCard onStart={vi.fn()} isRunning={false} />);

    const slider = screen.getByLabelText(/Number of Leads/);
    expect(screen.getByText('50')).toBeInTheDocument();

    await user.tab();
    // Simulate slider change
    fireEvent.change(slider, { target: { value: '100' } });
    expect(screen.getByText('100')).toBeInTheDocument();
  });

  it('changes country via select', async () => {
    const onStart = vi.fn();
    const user = userEvent.setup();
    render(<SearchCard onStart={onStart} isRunning={false} />);

    await user.selectOptions(screen.getByLabelText('Country'), 'gb');
    await user.type(screen.getByLabelText('Business Niche'), 'dentists');
    await user.type(screen.getByLabelText('City / Location'), 'London');
    await user.click(screen.getByText('Find Leads'));

    expect(onStart).toHaveBeenCalledWith({
      niche: 'dentists',
      location: 'London',
      country: 'gb',
      limit: 50,
    });
  });
});
