import { memo, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Decision, DecisionValue, DetectionEvent } from '../types'
import { isPermissionDenied } from '../utils/imageError'

function bestFrameIndex(frames: DetectionEvent['frames']): number {
  let best = 0
  for (let i = 1; i < frames.length; i++) {
    if ((frames[i].prob ?? -1) > (frames[best].prob ?? -1)) best = i
  }
  return best
}

interface Props {
  event: DetectionEvent
  decision: Decision
  needsDecision: boolean
  /**
   * Called with the event key, the observation that represents the decision
   * (on pending cards: the event of the frame being looked at) and the decision.
   */
  onDecide: (key: string, repObsId: string, d: DecisionValue) => void
  onOpenLightbox: (eventIdx: number, frameIdx: number) => void
  eventIdx: number
  readOnly?: boolean
  /** Show the third, "unsure" button (review rounds and pending cells). */
  allowUnsure?: boolean
}

/**
 * One gallery card. It renders a single <img> (the frame being looked at)
 * instead of a carousel with every frame of the event: a round can have well
 * over a thousand cards and some events have hundreds of frames, and mounting
 * all of them made the page unusable. The full event is in the lightbox.
 */
function EventCard({
  event,
  decision,
  needsDecision,
  onDecide,
  onOpenLightbox,
  eventIdx,
  readOnly = false,
  allowUnsure = false,
}: Props) {
  const { t, i18n } = useTranslation()
  const imgError = i18n.language.startsWith('es') ? '/img-error-es.svg' : '/img-error-en.svg'
  const [activeIndex, setActiveIndex] = useState(() => bestFrameIndex(event.frames))
  const n = event.frames.length
  const frame = event.frames[Math.min(activeIndex, n - 1)]
  const repObsId = frame?.burstRepObsId ?? event.repObsId

  // Warm the browser cache with the neighbouring frames so ‹ › switch
  // instantly instead of flashing an empty frame while the next photo loads.
  // Only once the user points at the card, so a page of cards doesn't
  // download every neighbour up front.
  const [warm, setWarm] = useState(false)
  useEffect(() => {
    if (!warm || n < 2) return
    for (const i of [activeIndex + 1, activeIndex - 1]) {
      const f = event.frames[(i + n) % n]
      if (f) new Image().src = f.img
    }
  }, [warm, activeIndex, event.frames, n])

  const borderClass = decision === 'confirmed'
    ? 'border-2 border-emerald-500'
    : decision === 'rejected'
    ? 'border-2 border-red-500'
    : decision === 'unsure'
    ? 'border-2 border-amber-500 border-dashed'
    : needsDecision
    ? 'border-2 border-amber-400'
    : 'border border-zinc-700'

  function step(delta: number) {
    setActiveIndex((i) => (i + delta + n) % n)
  }

  const navBtn = 'absolute top-1/2 -translate-y-1/2 bg-black/50 text-white w-7 h-7 rounded-full flex items-center justify-center opacity-70 hover:opacity-100 transition-opacity'

  return (
    <div
      className={`rounded-lg overflow-hidden bg-zinc-900 text-zinc-100 ${borderClass}`}
      onPointerEnter={() => setWarm(true)}
      onFocus={() => setWarm(true)}
    >
      <div className="relative" style={{ aspectRatio: '16/10', background: '#000' }}>
        {frame && (
          <img
            key={frame.img}
            src={frame.img}
            alt=""
            loading="lazy"
            style={{
              width: '100%', height: '100%', objectFit: 'contain', cursor: 'pointer',
              opacity: frame.isContext ? 0.6 : 1,
            }}
            onClick={() => onOpenLightbox(eventIdx, activeIndex)}
            onError={(e) => {
              const img = e.currentTarget
              const failedSrc = img.src
              img.onerror = null
              img.src = imgError
              img.style.objectFit = 'contain'
              isPermissionDenied(failedSrc).then((denied) => {
                if (denied) img.title = t('gallery.image_permission_denied')
              })
            }}
          />
        )}

        {frame?.isContext && (
          <span
            className="absolute top-1.5 left-8 bg-zinc-800/80 text-zinc-300 text-xs px-1.5 py-0.5 rounded"
            style={{ zIndex: 15, pointerEvents: 'none' }}
          >
            {t('gallery.context_frame')}
          </span>
        )}

        {n > 1 && (
          <>
            <button className={`${navBtn} left-1`} style={{ zIndex: 20 }}
              aria-label={t('gallery.prev_frame')} title={t('gallery.prev_frame')}
              onClick={() => step(-1)}>‹</button>
            <button className={`${navBtn} right-1`} style={{ zIndex: 20 }}
              aria-label={t('gallery.next_frame')} title={t('gallery.next_frame')}
              onClick={() => step(1)}>›</button>
          </>
        )}

        <div
          className="absolute bottom-0 left-0 right-0 flex justify-between items-center px-2 py-1"
          style={{
            background: 'linear-gradient(transparent, rgba(0,0,0,.8))',
            fontSize: 11,
            pointerEvents: 'none',
            zIndex: 10,
          }}
        >
          <span>{frame?.ts}</span>
          {n > 1 && <span className="bg-black/50 px-1.5 py-0.5 rounded">{activeIndex + 1}/{n}</span>}
          {frame && !frame.isContext && frame.prob != null
            ? <span className="bg-black/50 px-1.5 py-0.5 rounded text-xs">{frame.prob.toFixed(2)}</span>
            : <span />}
        </div>

        <span
          className="absolute top-0 right-0 m-1 flex flex-col items-end bg-black/75 px-1.5 py-0.5 rounded"
          style={{ zIndex: 20, fontSize: 11, gap: 2 }}
        >
          <span>{t('gallery.occasion_label', { n: event.occasion })}</span>
          <span>{t('gallery.sequence', { rank: frame?.rank ?? event.rank, total: event.totalSeqs })}</span>
        </span>

        <button
          className="absolute top-0 left-0 m-1 bg-black/50 text-white text-sm px-1.5 py-0.5 rounded opacity-70 hover:opacity-100 transition-opacity"
          style={{ zIndex: 20 }}
          title={t('gallery.zoom')}
          onClick={() => onOpenLightbox(eventIdx, activeIndex)}
        >
          ⊕
        </button>
      </div>

      <div className="flex gap-2 p-2" style={{ opacity: readOnly ? 0.5 : 1 }}>
        <button
          className={`flex-1 py-1.5 text-sm rounded font-medium transition-colors disabled:opacity-50 ${
            decision === 'confirmed'
              ? 'bg-emerald-600 text-white hover:bg-emerald-700'
              : 'border border-emerald-600 text-emerald-400 hover:bg-emerald-600 hover:text-white'
          }`}
          onClick={() => onDecide(event.key, repObsId, 'confirmed')}
          disabled={readOnly}
        >
          {t('gallery.confirmed_btn')}
        </button>
        {allowUnsure && (
          <button
            className={`flex-1 py-1.5 text-sm rounded font-medium transition-colors disabled:opacity-50 ${
              decision === 'unsure'
                ? 'bg-amber-500 text-white hover:bg-amber-600'
                : 'border border-amber-500 text-amber-400 hover:bg-amber-500 hover:text-white'
            }`}
            onClick={() => onDecide(event.key, repObsId, 'unsure')}
            disabled={readOnly}
            title={t('gallery.unsure_tooltip')}
          >
            {t('gallery.unsure_btn')}
          </button>
        )}
        <button
          className={`flex-1 py-1.5 text-sm rounded font-medium transition-colors disabled:opacity-50 ${
            decision === 'rejected'
              ? 'bg-red-600 text-white hover:bg-red-700'
              : 'border border-red-600 text-red-400 hover:bg-red-600 hover:text-white'
          }`}
          onClick={() => onDecide(event.key, repObsId, 'rejected')}
          disabled={readOnly}
        >
          {t('gallery.rejected_btn')}
        </button>
      </div>
    </div>
  )
}

// Re-render a card only when its own props change, not on every decision
// anywhere on the page.
export default memo(EventCard)
