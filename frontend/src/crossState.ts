/**
 * State of an "Another species in this photo?" button for the detection event
 * open in the lightbox.
 *
 * - `confirmed`: the species was confirmed in its own review (green, fixed);
 * - `cross`: confirmed from a photo of this detection event (blue; a click undoes it);
 * - `elsewhere`: confirmed from a photo of another detection event of the same
 *   location and occasion (grey, fixed: it can only be undone from that photo,
 *   and showing it blue here would suggest this photo shows that species);
 * - `none`: not confirmed.
 */
export type CrossButtonState = 'confirmed' | 'cross' | 'elsewhere' | 'none'

export function crossButtonState(
  entry: { status: string; mediaID?: string | null } | undefined,
  frames: { mediaId: string }[],
): CrossButtonState {
  if (!entry) return 'none'
  if (entry.status === 'confirmed') return 'confirmed'
  if (entry.status === 'cross') {
    return frames.some((f) => f.mediaId === entry.mediaID) ? 'cross' : 'elsewhere'
  }
  return 'none'
}
