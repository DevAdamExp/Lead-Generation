import type { Job, Lead, LeadsResponse } from '@/types';

export function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 'job-1',
    niche: 'plumbers',
    location: 'Austin',
    country: 'us',
    limit: 50,
    status: 'pending',
    progress_pct: 0,
    current_stage: 'Starting',
    stage_message: 'Initialising pipeline...',
    total_scraped: 0,
    total_verified: 0,
    total_leads: 0,
    created_at: '2026-06-20T12:00:00Z',
    ...overrides,
  };
}

export function makeLead(overrides: Partial<Lead> = {}): Lead {
  return {
    id: 'lead-1',
    job_id: 'job-1',
    business_name: 'Acme Plumbing',
    name: 'Acme Plumbing',
    address: '123 Main St',
    city: 'Austin',
    phone: '512-555-0100',
    phone_formatted: '+1 512-555-0100',
    website: 'https://acmeplumbing.com',
    website_status: 'active',
    website_cms: 'wordpress',
    has_ssl: true,
    has_google_maps: true,
    google_rating: 4.5,
    google_review_count: 23,
    google_maps_url: 'https://maps.google.com/...',
    google_is_open: true,
    owner: 'John Doe',
    owner_name: 'John Doe',
    email: 'john@acmeplumbing.com',
    owner_email: 'john@acmeplumbing.com',
    social_facebook: '',
    social_instagram: '',
    social_twitter: '',
    social_linkedin: '',
    score: 85,
    lead_score: 85,
    fuzzy_confidence: 0.95,
    category: 'no_website',
    status: 'new',
    source: 'hotfrog',
    source_query: 'plumbers in Austin',
    created_at: '2026-06-20T12:01:00Z',
    ...overrides,
  };
}

export function makeLeadsResponse(overrides: Partial<LeadsResponse> = {}): LeadsResponse {
  return {
    items: [makeLead()],
    total: 1,
    page: 1,
    per_page: 50,
    ...overrides,
  };
}
