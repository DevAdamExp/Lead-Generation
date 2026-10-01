'use client';

import { useState, FormEvent } from 'react';
import { SearchIcon, GlobeIcon, PinIcon } from './Icons';
import type { SearchFormData } from '@/types';

interface SearchCardProps {
  onStart: (data: SearchFormData) => void;
  isRunning: boolean;
}

export default function SearchCard({ onStart, isRunning }: SearchCardProps) {
  const [niche, setNiche] = useState('');
  const [location, setLocation] = useState('');
  const [country, setCountry] = useState('us');
  const [limit, setLimit] = useState(50);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!niche.trim() || !location.trim()) return;
    onStart({ niche: niche.trim(), location: location.trim(), country, limit });
  };

  return (
    <section className="search-section">
      <div className="search-card">
        <div className="search-card-header">
          <h3 className="search-card-header-title">Start a Lead Search</h3>
          <p className="search-card-header-sub">
            Enter a business niche and location to begin the intelligence pipeline
          </p>
        </div>

        <form className="search-form" onSubmit={handleSubmit} autoComplete="off">
          <div className="form-row">
            <div className="form-group">
              <label className="form-label" htmlFor="niche">Business Niche</label>
              <div className="input-wrap">
                <span className="input-icon"><SearchIcon width={16} height={16} /></span>
                <input
                  id="niche"
                  className="input-field"
                  type="text"
                  placeholder="e.g. plumbers, dentists, hvac..."
                  required
                  value={niche}
                  onChange={(e) => setNiche(e.target.value)}
                />
              </div>
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="location">City / Location</label>
              <div className="input-wrap">
                <span className="input-icon"><PinIcon width={16} height={16} /></span>
                <input
                  id="location"
                  className="input-field"
                  type="text"
                  placeholder="e.g. Austin, TX"
                  required
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                />
              </div>
            </div>
          </div>

          <div className="form-row">
            <div className="form-group">
              <label className="form-label" htmlFor="country">Country</label>
              <div className="input-wrap">
                <span className="input-icon"><GlobeIcon width={16} height={16} /></span>
                <select
                  id="country"
                  className="input-field input-field-select"
                  value={country}
                  onChange={(e) => setCountry(e.target.value)}
                >
                  <option value="us">🇺🇸 United States</option>
                  <option value="gb">🇬🇧 United Kingdom</option>
                  <option value="au">🇦🇺 Australia</option>
                  <option value="ca">🇨🇦 Canada</option>
                  <option value="nz">🇳🇿 New Zealand</option>
                  <option value="ie">🇮🇪 Ireland</option>
                </select>
              </div>
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="limit">
                Number of Leads: <span className="slider-value">{limit}</span>
              </label>
              <div className="limit-row">
                <input
                  id="limit"
                  className="slider"
                  type="range"
                  min={5}
                  max={500}
                  step={5}
                  value={limit}
                  onChange={(e) => setLimit(Number(e.target.value))}
                />
                <input
                  className="input-field limit-number"
                  type="number"
                  min={5}
                  max={500}
                  value={limit}
                  aria-label="Number of leads"
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isNaN(v)) return;
                    setLimit(Math.min(500, Math.max(5, v)));
                  }}
                />
              </div>
              <div className="slider-labels"><span>5</span><span>500</span></div>
            </div>
          </div>

          <button type="submit" className="btn-primary" disabled={isRunning}>
            <SearchIcon width={18} height={18} />
            <span>{isRunning ? 'Running...' : 'Find Leads'}</span>
          </button>
        </form>
      </div>
    </section>
  );
}
