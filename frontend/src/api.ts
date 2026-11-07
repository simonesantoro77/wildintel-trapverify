import type { CrossSpecies, McClassifier, McJob, McLabels, McRunRequest, DetectionEvent, OccupancyRow, RecentSession, RelinkStatus, SavedProgress, SpeciesStats, WorkflowConfig } from './types'

async function req<T>(url: string, options?: RequestInit): Promise<T> {
  const r = await fetch(url, options)
  if (!r.ok) {
    const text = await r.text().catch(() => r.statusText)
    let message = text
    try { message = JSON.parse(text).detail ?? text } catch { /* not JSON */ }
    throw new Error(message)
  }
  return r.json() as Promise<T>
}

function post<T>(url: string, body: unknown): Promise<T> {
  return req<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function put<T>(url: string, body: unknown): Promise<T> {
  return req<T>(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export const api = {
  getState: () =>
    req<{
      ready: boolean
      config?: WorkflowConfig
      default_output_dir?: string
      session_dir?: string
      relink?: RelinkStatus
    }>('/api/state'),

  setup: (config: WorkflowConfig) =>
    post<{ ok: boolean; n_candidates: number; n_combos: number }>('/api/setup', config),

  getSpecies: () =>
    req<SpeciesStats[]>('/api/species'),

  getEvents: (species: string, iteration: number) =>
    req<DetectionEvent[]>(`/api/species/${species}/events?iteration=${iteration}`),

  getReview: (species: string) =>
    req<DetectionEvent[]>(`/api/species/${species}/review`),

  updateDecisions: (species: string, confirmedKeys: string[]) =>
    put<{ success: boolean; confirmed: number }>(
      `/api/species/${species}/decisions`,
      { confirmed_keys: confirmedKeys },
    ),

  getDecisions: (species: string, iteration: number) =>
    req<{ confirmed: string[]; unsure: string[] }>(`/api/decisions?species=${species}&iteration=${iteration}`),

  saveDecisions: (species: string, iteration: number, confirmed: string[], unsure: string[] = []) =>
    post<{ success: boolean; done: boolean; next_iteration: number | null; remaining: number; pending: number }>(
      '/api/decisions',
      { species, iteration, confirmed, unsure },
    ),

  getPending: (species: string) =>
    req<DetectionEvent[]>(`/api/species/${species}/pending`),

  settlePending: (species: string, confirmed: string[], rejectedKeys: string[]) =>
    post<{ success: boolean; confirmed: number; rejected: number }>(
      `/api/species/${species}/pending`,
      { confirmed, rejected_keys: rejectedKeys },
    ),

  getProgress: (species: string, mode: string, iteration: number) =>
    req<{ progress: SavedProgress | null }>(
      `/api/species/${species}/progress?mode=${encodeURIComponent(mode)}&iteration=${iteration}`,
    ),

  reopenRound: (species: string) =>
    post<{ success: boolean; iteration: number; restored: number }>(`/api/species/${species}/reopen`, {}),

  getCross: (siteId: string, occasion: number) =>
    req<{ species: CrossSpecies[] }>(
      `/api/cross?site_id=${encodeURIComponent(siteId)}&occasion=${occasion}`,
    ),

  addCross: (body: { species: string; site_id: string; occasion: number; media_id: string; observation_id: string; source_species: string }) =>
    post<{ success: boolean; key: string }>('/api/cross', body),

  removeCross: (species: string, siteId: string, occasion: number) =>
    req<{ success: boolean; removed: number }>(
      `/api/cross?species=${encodeURIComponent(species)}&site_id=${encodeURIComponent(siteId)}&occasion=${occasion}`,
      { method: 'DELETE' },
    ),

  /** keepalive lets the request finish while the page is being closed. */
  saveProgress: (species: string, body: Omit<SavedProgress, 'saved_at'>, keepalive = false) =>
    req<{ success: boolean; saved_at: string; n: number }>(`/api/species/${species}/progress`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      keepalive,
    }),

  rejectBurst: (mediaId: string) =>
    post<{ success: boolean; removed: string[] }>('/api/reject', { mediaId }),

  unreject: (media: string[]) =>
    post<{ success: boolean }>('/api/unreject', { media }),

  getRejected: () =>
    req<{ rejected: string[] }>('/api/rejected'),

  inspectDir: (path: string) =>
    req<{
      species: string[]
      study_start: string | null
      study_end: string | null
      datapackage_errors: string[] | null
    }>(`/api/fs/inspect?path=${encodeURIComponent(path)}`),

  checkImages: (camtrapDir: string, imageBaseDir: string, flatSearch = false) =>
    req<{
      total: number
      missing: number
      permission_denied: number
      examples: string[]
      ambiguous: { mediaID: string; fileName: string; deploymentID: string; candidates: string[] }[]
    }>(
      `/api/fs/check-images?camtrap_dir=${encodeURIComponent(camtrapDir)}&image_base_dir=${encodeURIComponent(imageBaseDir)}&flat_search=${flatSearch}`,
    ),

  getResults: () =>
    req<{
      session_dir: string
      output_dir: string
      total: number
      confirmed: number
      rejected: number
      unverified: number
      pending: number
      by_species: { species: string; confirmed: number; rejected: number; pending?: number; unverified: number }[]
      datapackage_generated: boolean
      datapackage_fabricated_fields: string[]
    }>('/api/results'),

  getOccupancy: () =>
    req<OccupancyRow[]>('/api/results/occupancy'),

  loadSession: (session_dir: string) =>
    post<{ ok: boolean; session_dir: string; config: unknown; relink: RelinkStatus }>('/api/session/load', { session_dir }),

  importSession: (zipPath: string) =>
    post<{ ok: boolean; session_dir: string; relink: RelinkStatus }>('/api/session/import', { zip_path: zipPath }),

  relinkSession: (camtrapDir: string, imageBaseDir: string, flatSearch: boolean) =>
    post<{ ok: boolean; relink: RelinkStatus }>(
      '/api/session/relink',
      { camtrap_dir: camtrapDir, image_base_dir: imageBaseDir, flat_search: flatSearch },
    ),

  getRecentSessions: () =>
    req<RecentSession[]>('/api/session/recent'),

  trapperLogin: (url: string, username: string, password: string) =>
    post<{ ok: boolean; base_url: string; research_projects_count: number }>(
      '/api/trapper/login', { url, username, password },
    ),

  trapperResearchProjects: () =>
    req<{ results: { pk: number; name: string; acronym: string }[] }>(
      '/api/trapper/research-projects',
    ),

  trapperClassificationProjects: (researchProjectPk: number) =>
    req<{ results: { pk: number; name: string; is_active: boolean }[] }>(
      `/api/trapper/classification-projects/${researchProjectPk}`,
    ),

  trapperGenerate: (classificationProjectPk: number) =>
    post<{ task_id: string }>('/api/trapper/generate', { classification_project_pk: classificationProjectPk, output_dir: '' }),

  trapperGenerateStatus: (taskId: string) =>
    req<{ status: 'running' | 'done' | 'error'; path: string | null; error: string | null }>(
      `/api/trapper/generate/${taskId}`,
    ),

  convertDeepfaune: (csvPath: string, minScore: number) =>
    post<{ camtrap_dir: string; label_col: string; score_col: string }>(
      '/api/convert/deepfaune',
      { csv_path: csvPath, min_score: minScore },
    ),

  csvHeaders: (path: string) =>
    req<{ columns: string[] }>(`/api/fs/csv-headers?path=${encodeURIComponent(path)}`),

  csvLabels: (path: string, col: string) =>
    req<{ labels: string[]; prefilled: Record<string, string> }>(
      `/api/fs/csv-labels?path=${encodeURIComponent(path)}&col=${encodeURIComponent(col)}`,
    ),

  convertCsv: (body: {
    csv_path: string
    col_filename: string
    col_datetime: string
    col_label: string
    col_score?: string | null
    col_site?: string | null
    species_map: Record<string, string>
  }) => post<{ camtrap_dir: string }>('/api/convert/csv', body),

  mcLabels: (body: { classifiers: McClassifier[]; date_start?: string | null; date_end?: string | null }) =>
    post<McLabels>('/api/multiclassifier/labels', body),

  mcGroups: (species: string[], labels: string[]) =>
    post<{ groups: Record<string, string[]>; aliases?: Record<string, string> }>(
      '/api/multiclassifier/groups', { species, labels }),

  mcRun: (body: McRunRequest) =>
    post<{ job_id: string; out_dir: string }>('/api/multiclassifier/run', body),

  mcJob: (jobId: string) =>
    req<McJob>(`/api/multiclassifier/jobs/${jobId}`),

  mcDefaults: () =>
    req<{ output_dir: string }>('/api/multiclassifier/defaults'),

  openFolder: () =>
    req<{ ok: boolean; path: string }>('/api/open-folder', { method: 'POST' }),

  checkVersion: () =>
    req<{ current: string; latest: string | null; update_available: boolean; release_url: string | null }>('/api/version'),

  checkHealth: async (): Promise<boolean> => {
    try {
      const r = await fetch('/api/health')
      return r.ok
    } catch {
      return false
    }
  },
}
