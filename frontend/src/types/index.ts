export interface Job {
  id: string;
  niche: string;
  location: string;
  country: string;
  limit: number;
  status: JobStatus;
  progress_pct: number;
  current_stage: string;
  stage_message: string;
  total_scraped: number;
  total_verified: number;
  total_leads: number;
  created_at: string;
  error_message?: string;
}

export type JobStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface Lead {
  id: string;
  job_id: string;
  business_name: string;
  name: string;
  address: string;
  city?: string;
  phone: string;
  phone_formatted: string;
  phone_verified?: boolean;
  business_category?: string | null;
  hours?: string | null;
  price_level?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  sources?: string | null;
  source_url?: string | null;
  website: string;
  website_status: string;
  website_cms: string;
  has_ssl: boolean;
  has_google_maps: boolean;
  google_rating: number | null;
  google_review_count: number;
  google_maps_url: string | null;
  google_is_open: boolean | null;
  owner: string;
  owner_name: string;
  email: string;
  owner_email: string;
  email_verified?: boolean;
  owner_phone?: string | null;
  owner_phone_type?: string | null;
  owner_phone_confidence?: number;
  social_facebook: string;
  social_instagram: string;
  social_twitter: string;
  social_linkedin: string;
  score: number;
  lead_score: number;
  data_confidence?: number;
  fuzzy_confidence: number;
  website_name_found?: boolean;
  category: string;
  status: string;
  source: string;
  source_query: string;
  description_long?: string | null;
  employee_count?: number | null;
  employee_count_source?: string | null;
  year_founded?: number | null;
  partners?: string | null;
  recent_activity?: string | null;
  last_activity_date?: string | null;
  created_at: string;
}

export interface LeadsResponse {
  items: Lead[];
  total: number;
  page: number;
  per_page: number;
}

export interface ProgressMessage {
  type: 'progress' | 'complete' | 'error' | 'fallback_poll';
  progress_pct: number;
  stage: string;
  message: string;
  stats?: {
    total_scraped: number;
    total_verified: number;
  };
}

export interface SearchFormData {
  niche: string;
  location: string;
  country: string;
  limit: number;
}
