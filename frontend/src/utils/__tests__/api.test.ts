import { apiFetch as rawApiFetch, createJob, getJob, getLeads, getJobs, getExportUrl, openExport } from '@/utils/api';
const apiFetch = rawApiFetch;
import type { Job, LeadsResponse, SearchFormData } from '@/types';

const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

beforeEach(() => {
  mockFetch.mockReset();
});

function mockResponse(data: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(typeof data === 'string' ? data : JSON.stringify(data)),
  });
}

describe('apiFetch', () => {
  it('makes GET request and returns JSON', async () => {
    mockFetch.mockResolvedValue(mockResponse({ id: 'j1' }));
    const result = await apiFetch<{ id: string }>('/api/jobs/j1');
    expect(result.id).toBe('j1');
    expect(mockFetch).toHaveBeenCalledWith('http://localhost:8000/api/jobs/j1', {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('includes custom headers from options', async () => {
    mockFetch.mockResolvedValue(mockResponse({}));
    await apiFetch('/api/jobs', { headers: { 'X-Custom': 'val' } });
    expect(mockFetch).toHaveBeenCalledWith('http://localhost:8000/api/jobs', {
      headers: { 'Content-Type': 'application/json', 'X-Custom': 'val' },
    });
  });

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValue(mockResponse('Not found', 404));
    await expect(apiFetch('/api/jobs/x')).rejects.toThrow('Not found');
  });

  it('throws with status code when no body', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      text: () => Promise.resolve(''),
    });
    await expect(apiFetch('/api/jobs/x')).rejects.toThrow('HTTP 500');
  });

  it('uses empty string API_BASE when env not set', async () => {
    const prev = process.env.NEXT_PUBLIC_API_URL;
    process.env.NEXT_PUBLIC_API_URL = '';

    // Re-import module to pick up new env value
    vi.resetModules();
    const fresh = await import('@/utils/api');
    mockFetch.mockResolvedValue(mockResponse({}));
    await fresh.apiFetch('/test');
    expect(mockFetch).toHaveBeenCalledWith('/test', expect.any(Object));

    process.env.NEXT_PUBLIC_API_URL = prev;
  });
});

describe('createJob', () => {
  it('sends POST with search data', async () => {
    mockFetch.mockResolvedValue(mockResponse({ id: 'j1', status: 'pending' }));
    const data: SearchFormData = { niche: 'plumbers', location: 'Austin', country: 'us', limit: 50 };
    const job = await createJob(data);
    expect(job.id).toBe('j1');
    expect(mockFetch).toHaveBeenCalledWith('http://localhost:8000/api/jobs', {
      method: 'POST',
      body: JSON.stringify(data),
      headers: { 'Content-Type': 'application/json' },
    });
  });
});

describe('getJob', () => {
  it('fetches a job by id', async () => {
    mockFetch.mockResolvedValue(mockResponse({ id: 'j1', status: 'running' }));
    const job = await getJob('j1');
    expect(job.status).toBe('running');
  });
});

describe('getLeads', () => {
  it('builds query params correctly', async () => {
    mockFetch.mockResolvedValue(mockResponse({ items: [], total: 0, page: 1, per_page: 50 }));
    await getLeads('j1', 1, 50, 0, 'no_website', true);
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain('page=1');
    expect(url).toContain('per_page=50');
    expect(url).toContain('min_score=0');
    expect(url).toContain('category=no_website');
    expect(url).toContain('has_email=true');
  });

  it('omits optional params when not provided', async () => {
    mockFetch.mockResolvedValue(mockResponse({ items: [], total: 0, page: 1, per_page: 50 }));
    await getLeads('j1', 1);
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).not.toContain('category=');
    expect(url).not.toContain('has_email=');
  });
});

describe('getJobs', () => {
  it('fetches all jobs', async () => {
    mockFetch.mockResolvedValue(mockResponse([{ id: 'j1' }, { id: 'j2' }]));
    const jobs = await getJobs();
    expect(jobs).toHaveLength(2);
  });
});

describe('getExportUrl', () => {
  it('returns xlsx export URL', () => {
    const url = getExportUrl('j1', 'xlsx');
    expect(url).toBe('http://localhost:8000/api/jobs/j1/export?format=xlsx');
  });

  it('returns pdf export URL', () => {
    const url = getExportUrl('j1', 'pdf');
    expect(url).toBe('http://localhost:8000/api/jobs/j1/export?format=pdf');
  });
});

describe('openExport', () => {
  it('opens export URL in new window', () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    openExport('j1', 'xlsx');
    expect(openSpy).toHaveBeenCalledWith('http://localhost:8000/api/jobs/j1/export?format=xlsx');
    openSpy.mockRestore();
  });
});
