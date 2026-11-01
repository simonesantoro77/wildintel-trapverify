import type { DecisionValue } from '../types'

/**
 * Unsaved gallery decisions, kept in the browser so that hours of review are
 * not lost if the tab is closed (or the computer sleeps) before "Save".
 *
 * Purely a convenience: every access is wrapped in try/catch because storage
 * can be unavailable (private windows, blocked site data), and the gallery
 * works the same without it.
 */
export interface Draft {
  decisions: Record<string, DecisionValue>
  chosenRep: Record<string, string>
  savedAt?: string
}

const PREFIX = 'camtrap-verify:draft:'

export function draftKey(sessionId: string, species: string, mode: string, iteration: number): string {
  return `${PREFIX}${sessionId}|${species}|${mode}|${iteration}`
}

export function loadDraft(key: string): Draft | null {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Draft
    return parsed && typeof parsed.decisions === 'object' ? parsed : null
  } catch {
    return null
  }
}

export function saveDraft(key: string, draft: Draft): void {
  try {
    localStorage.setItem(key, JSON.stringify({ ...draft, savedAt: new Date().toISOString() }))
  } catch {
    /* storage full or unavailable: the draft is a convenience only */
  }
}

export function clearDraft(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch {
    /* ignore */
  }
}
