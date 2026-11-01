import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearDraft, draftKey, loadDraft, saveDraft } from './draft'

describe('gallery drafts', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('round-trips decisions per session, species, mode and round', () => {
    const key = draftKey('/sessions/a', 'Vulpes_vulpes', 'rounds', 2)
    expect(loadDraft(key)).toBeNull()
    saveDraft(key, { decisions: { k1: 'confirmed', k2: 'unsure' }, chosenRep: {} })
    expect(loadDraft(key)?.decisions).toEqual({ k1: 'confirmed', k2: 'unsure' })
    expect(loadDraft(draftKey('/sessions/a', 'Vulpes_vulpes', 'rounds', 3))).toBeNull()
    clearDraft(key)
    expect(loadDraft(key)).toBeNull()
  })

  it('never throws when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    const key = draftKey('s', 'sp', 'rounds', 1)
    expect(() => saveDraft(key, { decisions: {}, chosenRep: {} })).not.toThrow()
    expect(loadDraft(key)).toBeNull()
  })
})
