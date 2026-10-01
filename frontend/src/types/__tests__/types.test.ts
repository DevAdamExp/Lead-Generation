import type { Job, Lead, LeadsResponse, ProgressMessage, SearchFormData, JobStatus } from '@/types';

describe('types', () => {
  describe('JobStatus', () => {
    it('accepts valid statuses', () => {
      const statuses: JobStatus[] = ['pending', 'running', 'completed', 'failed'];
      expect(statuses).toHaveLength(4);
    });
  });

  describe('SearchFormData', () => {
    it('matches expected shape', () => {
      const data: SearchFormData = { niche: 'plumbers', location: 'Austin', country: 'us', limit: 50 };
      expect(data.niche).toBe('plumbers');
      expect(data.location).toBe('Austin');
      expect(data.country).toBe('us');
      expect(data.limit).toBe(50);
    });
  });

  describe('Job', () => {
    it('accepts all fields with optional error_message', () => {
      const job: Job = {
        id: 'j1', niche: 'x', location: 'y', country: 'us', limit: 10,
        status: 'running', progress_pct: 50, current_stage: 'Scraping',
        stage_message: 'working...', total_scraped: 5, total_verified: 2,
        total_leads: 0, created_at: '2026-01-01T00:00:00Z',
      };
      expect(job.status).toBe('running');
    });

    it('accepts error_message', () => {
      const job: Job = {
        id: 'j1', niche: 'x', location: 'y', country: 'us', limit: 10,
        status: 'failed', progress_pct: 0, current_stage: '', stage_message: '',
        total_scraped: 0, total_verified: 0, total_leads: 0,
        created_at: '', error_message: 'Something broke',
      };
      expect(job.error_message).toBe('Something broke');
    });
  });

  describe('Lead', () => {
    it('includes all optional fields', () => {
      const lead: Lead = {
        id: 'l1', job_id: 'j1', business_name: 'Acme', name: 'Acme',
        address: '123 St', city: 'Austin', phone: '555-0100', phone_formatted: '+1 555-0100',
        website: 'https://acme.com', website_status: 'active', website_cms: 'wordpress',
        has_ssl: true, has_google_maps: true, google_rating: 4.5,
        google_review_count: 10, google_maps_url: 'url', google_is_open: true,
        owner: 'John', owner_name: 'John', email: 'john@acme.com', owner_email: 'john@acme.com',
        social_facebook: '', social_instagram: '', social_twitter: '', social_linkedin: '',
        score: 90, lead_score: 90, fuzzy_confidence: 0.9, category: 'high_value',
        status: 'new', source: 'hotfrog', source_query: 'plumbers', created_at: '',
      };
      expect(lead.score).toBe(90);
    });
  });

  describe('LeadsResponse', () => {
    it('holds paginated lead results', () => {
      const resp: LeadsResponse = { items: [], total: 0, page: 1, per_page: 50 };
      expect(resp.items).toHaveLength(0);
    });
  });

  describe('ProgressMessage', () => {
    it('accepts different types', () => {
      const progress: ProgressMessage = { type: 'progress', progress_pct: 50, stage: 'Scraping', message: 'working' };
      expect(progress.type).toBe('progress');
    });

    it('accepts complete type', () => {
      const complete: ProgressMessage = { type: 'complete', progress_pct: 100, stage: 'Done', message: 'Finished' };
      expect(complete.type).toBe('complete');
    });

    it('accepts error type', () => {
      const error: ProgressMessage = { type: 'error', progress_pct: 0, stage: 'Error', message: 'Failed' };
      expect(error.type).toBe('error');
    });

    it('accepts fallback_poll type', () => {
      const fallback: ProgressMessage = { type: 'fallback_poll', progress_pct: 0, stage: 'Polling', message: 'Fallback' };
      expect(fallback.type).toBe('fallback_poll');
    });

    it('includes optional stats', () => {
      const msg: ProgressMessage = {
        type: 'progress', progress_pct: 50, stage: 'Scraping', message: 'working',
        stats: { total_scraped: 10, total_verified: 5 },
      };
      expect(msg.stats?.total_scraped).toBe(10);
    });
  });
});
