export interface Frame {
  obsId: string
  mediaId: string
  img: string
  ts: string
  prob: number | null
  isContext?: boolean
  /** Pending cards only: representative observation of this frame's event. */
  burstRepObsId?: string
  /** Pending cards only: rank of this frame's detection event. */
  rank?: number
}

export interface DetectionEvent {
  key: string
  siteId: string
  occasion: number
  rank: number
  totalSeqs: number
  repObsId: string
  maxProb: number
  frames: Frame[]
  status?: 'confirmed' | 'not_confirmed' | 'pending'
}

export interface SpeciesStats {
  species_name: string
  species_safe: string
  n_total_combos: number
  n_confirmed_combos: number
  n_resolved: number
  /** Cells whose events ran out with at least one marked unsure. */
  n_pending: number
  current_iteration: number
  /** rounds: review in progress · pending: only pending cells left · complete */
  phase: 'rounds' | 'pending' | 'complete'
  thumbnails: string[]
}

export interface WorkflowConfig {
  camtrap_dir: string
  image_base_dir: string
  flat_search: boolean
  output_dir: string
  target_species: string[]
  study_start: string
  study_end: string
  occasion_days: number
  total_iterations: number
  gap_seconds: number
  min_score: number
  /** Per-species overrides of min_score (scientificName -> threshold). */
  min_score_by_species?: Record<string, number>
  include_burst_context: boolean
  classified_by: string
  extended_confirmation: boolean
}

export type DecisionValue = 'confirmed' | 'rejected' | 'unsure'
export type Decision = DecisionValue | null

export interface RelinkStatus {
  camtrap_dir_ok: boolean
  images_ok: boolean
  needs_relink: boolean
  camtrap_dir: string
  image_base_dir: string
  flat_search: boolean
}

export interface OccupancyRow {
  species: string
  psi_naive: number | null
  psi_naive_lo: number | null
  psi_naive_hi: number | null
  p_naive: number | null
  degenerate_naive: boolean
  psi_verified: number | null
  psi_verified_lo: number | null
  psi_verified_hi: number | null
  p_verified: number | null
  degenerate_verified: boolean
}

export interface RecentSession {
  session_dir: string
  last_opened: string
  target_species: string[]
  study_start: string | null
  study_end: string | null
}

/** Decisions of an unfinished round kept in the session folder. */
export interface SavedProgress {
  mode: 'rounds' | 'pending'
  iteration: number
  saved_at: string
  decisions: Record<string, DecisionValue>
  chosen_rep: Record<string, string>
}

/** State of a target species at one site and occasion (lightbox side panel). */
export interface CrossSpecies {
  scientificName: string
  species_safe: string
  /** cross: confirmed from a photo of another species; confirmed: in its own review */
  status: 'none' | 'cross' | 'confirmed'
  mediaID?: string
  observationID?: string
  source?: string
}
