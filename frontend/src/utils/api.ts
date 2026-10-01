const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

import type { Job, LeadsResponse, SearchFormData } from '@/types';

export function createJob(data: SearchFormData): Promise<Job> {
  return apiFetch<Job>('/api/jobs', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function getJob(jobId: string): Promise<Job> {
  return apiFetch<Job>(`/api/jobs/${jobId}`);
}

export function getLeads(
  jobId: string,
  page: number,
  perPage: number = 50,
  minScore: number = 0,
  category?: string,
  hasEmail?: boolean,
): Promise<LeadsResponse> {
  let path = `/api/jobs/${jobId}/leads?page=${page}&per_page=${perPage}&min_score=${minScore}`;
  if (category) path += `&category=${category}`;
  if (hasEmail) path += `&has_email=true`;
  return apiFetch<LeadsResponse>(path);
}

export function getJobs(): Promise<Job[]> {
  return apiFetch<Job[]>('/api/jobs');
}

export function getExportUrl(jobId: string, format: 'xlsx' | 'pdf'): string {
  return `${API_BASE}/api/jobs/${jobId}/export?format=${format}`;
}

export function getWsUrl(jobId: string): string {
  // When the API runs on its own host (NEXT_PUBLIC_API_URL), the socket goes there too.
  const base = API_BASE ? new URL(API_BASE) : location;
  const proto = base.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${base.host}/ws/jobs/${jobId}`;
}

export function openExport(jobId: string, format: 'xlsx' | 'pdf'): void {
  window.open(getExportUrl(jobId, format));
}
