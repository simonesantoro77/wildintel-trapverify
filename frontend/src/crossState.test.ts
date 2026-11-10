import { describe, expect, it } from 'vitest'
import { crossButtonState } from './crossState'

const frames = [{ mediaId: 'm1' }, { mediaId: 'm2' }]

describe('crossButtonState', () => {
  it('is blue only when confirmed from a photo of this detection event', () => {
    expect(crossButtonState({ status: 'cross', mediaID: 'm2' }, frames)).toBe('cross')
  })
  it('is "elsewhere" when confirmed from another detection event of the occasion', () => {
    // round 2 of fallow deer: red deer was confirmed from the round-1 photo
    expect(crossButtonState({ status: 'cross', mediaID: 'm9' }, frames)).toBe('elsewhere')
  })
  it('keeps own-review confirmations and empty states', () => {
    expect(crossButtonState({ status: 'confirmed' }, frames)).toBe('confirmed')
    expect(crossButtonState({ status: 'none' }, frames)).toBe('none')
    expect(crossButtonState(undefined, frames)).toBe('none')
  })
})
