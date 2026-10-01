import { render } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import {
  LogoIcon, SearchIcon, HistoryIcon, ResultIcon,
  MenuIcon, GlobeIcon, PinIcon, XLSXIcon, PDFIcon,
  RefreshIcon, EmptyIcon,
} from '@/components/Icons';

describe('Icons', () => {
  const iconComponents = [
    ['LogoIcon', LogoIcon],
    ['SearchIcon', SearchIcon],
    ['HistoryIcon', HistoryIcon],
    ['ResultIcon', ResultIcon],
    ['MenuIcon', MenuIcon],
    ['GlobeIcon', GlobeIcon],
    ['PinIcon', PinIcon],
    ['XLSXIcon', XLSXIcon],
    ['PDFIcon', PDFIcon],
    ['RefreshIcon', RefreshIcon],
    ['EmptyIcon', EmptyIcon],
  ] as const;

  it.each(iconComponents)('%s renders an SVG', (_, Component) => {
    const { container } = render(<Component />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute('viewBox');
  });

  it('LogoIcon renders with default size', () => {
    const { container } = render(<LogoIcon />);
    const svg = container.querySelector('svg')!;
    expect(svg.getAttribute('width')).toBe('20');
    expect(svg.getAttribute('height')).toBe('20');
  });

  it('SearchIcon accepts custom size', () => {
    const { container } = render(<SearchIcon width={24} height={24} />);
    const svg = container.querySelector('svg')!;
    expect(svg.getAttribute('width')).toBe('24');
    expect(svg.getAttribute('height')).toBe('24');
  });
});
