import { beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import EventCard from './EventCard'
import type { DetectionEvent } from '../types'

beforeAll(async () => {
  await i18n.changeLanguage('es')
})

function makeEvent(overrides: Partial<DetectionEvent> = {}): DetectionEvent {
  return {
    key: 'site1|1',
    siteId: 'site1',
    occasion: 1,
    rank: 1,
    totalSeqs: 2,
    repObsId: 'obs-1',
    maxProb: 0.9,
    frames: [
      { obsId: 'obs-1', mediaId: 'media-1', img: '/img1.jpg', ts: '2026-01-01 10:00', prob: 0.9 },
    ],
    ...overrides,
  }
}

describe('EventCard', () => {
  it('opens a confirmed card at the confirmed photo, not the most confident one', () => {
    const event = makeEvent({
      status: 'confirmed',
      repObsId: 'obs-2',
      frames: [
        { obsId: 'obs-1', mediaId: 'media-1', img: '/img1.jpg', ts: '', prob: 0.95 },
        { obsId: 'obs-2', mediaId: 'media-2', img: '/img2.jpg', ts: '', prob: 0.6 },
      ],
    })
    const { container } = render(
      <EventCard event={event} decision="confirmed" needsDecision={false}
        onDecide={vi.fn()} onOpenLightbox={vi.fn()} eventIdx={0} readOnly />,
    )
    expect(container.querySelector('img')?.getAttribute('src')).toBe('/img2.jpg')
  })

  it('marks pending events that need a decision', () => {
    const { container } = render(
      <EventCard
        event={makeEvent()}
        decision={null}
        needsDecision
        onDecide={vi.fn()}
        onOpenLightbox={vi.fn()}
        eventIdx={0}
      />,
    )
    expect(container.firstChild).toHaveClass('border-amber-400')
  })

  it('highlights confirmed events and marks the confirm button active', () => {
    const { container } = render(
      <EventCard
        event={makeEvent()}
        decision="confirmed"
        needsDecision={false}
        onDecide={vi.fn()}
        onOpenLightbox={vi.fn()}
        eventIdx={0}
      />,
    )
    expect(container.firstChild).toHaveClass('border-emerald-500')
    expect(screen.getByRole('button', { name: /^✓/ })).toHaveClass('bg-emerald-600')
  })

  it('calls onDecide with the event key and repObsId when confirming', async () => {
    const onDecide = vi.fn()
    render(
      <EventCard
        event={makeEvent()}
        decision={null}
        needsDecision
        onDecide={onDecide}
        onOpenLightbox={vi.fn()}
        eventIdx={0}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /^✓/ }))
    expect(onDecide).toHaveBeenCalledWith('site1|1', 'obs-1', 'confirmed')
  })

  it('disables decision buttons in read-only mode', () => {
    render(
      <EventCard
        event={makeEvent()}
        decision={null}
        needsDecision
        onDecide={vi.fn()}
        onOpenLightbox={vi.fn()}
        eventIdx={0}
        readOnly
      />,
    )
    expect(screen.getByRole('button', { name: /^✓/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /no confirmada/i })).toBeDisabled()
  })

  it('opens the lightbox at the highest-confidence frame via the zoom button', async () => {
    const onOpenLightbox = vi.fn()
    render(
      <EventCard
        event={makeEvent({
          frames: [
            { obsId: 'obs-1', mediaId: 'media-1', img: '/img1.jpg', ts: '10:00', prob: 0.4 },
            { obsId: 'obs-2', mediaId: 'media-2', img: '/img2.jpg', ts: '10:01', prob: 0.95 },
            { obsId: 'obs-3', mediaId: 'media-3', img: '/img3.jpg', ts: '10:02', prob: 0.7 },
          ],
        })}
        decision={null}
        needsDecision={false}
        onDecide={vi.fn()}
        onOpenLightbox={onOpenLightbox}
        eventIdx={2}
      />,
    )

    await userEvent.click(screen.getByTitle('Abrir en pantalla completa'))
    expect(onOpenLightbox).toHaveBeenCalledWith(2, 1)
  })

  it('shows the context-frame badge only on context frames', () => {
    render(
      <EventCard
        event={makeEvent({
          frames: [
            { obsId: 'obs-1', mediaId: 'media-1', img: '/img1.jpg', ts: '10:00', prob: 0.9, isContext: true },
          ],
        })}
        decision={null}
        needsDecision={false}
        onDecide={vi.fn()}
        onOpenLightbox={vi.fn()}
        eventIdx={0}
      />,
    )
    expect(screen.getByText('Contexto')).toBeInTheDocument()
  })

  it('renders a single image and steps through the frames with the arrows', async () => {
    const { container } = render(
      <EventCard
        event={makeEvent({
          frames: [
            { obsId: 'obs-1', mediaId: 'media-1', img: '/img1.jpg', ts: '10:00', prob: 0.9 },
            { obsId: 'obs-2', mediaId: 'media-2', img: '/img2.jpg', ts: '10:01', prob: 0.5 },
            { obsId: 'obs-3', mediaId: 'media-3', img: '/img3.jpg', ts: '10:02', prob: 0.4 },
          ],
        })}
        decision={null}
        needsDecision={false}
        onDecide={vi.fn()}
        onOpenLightbox={vi.fn()}
        eventIdx={0}
      />,
    )
    const imgs = () => container.querySelectorAll('img')
    expect(imgs()).toHaveLength(1)
    expect(imgs()[0]).toHaveAttribute('src', '/img1.jpg')
    expect(screen.getByText('1/3')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Imagen siguiente' }))
    expect(imgs()[0]).toHaveAttribute('src', '/img2.jpg')
    await userEvent.click(screen.getByRole('button', { name: 'Imagen anterior' }))
    await userEvent.click(screen.getByRole('button', { name: 'Imagen anterior' }))
    expect(imgs()[0]).toHaveAttribute('src', '/img3.jpg')
  })

  it('only offers the unsure button when allowed', async () => {
    const onDecide = vi.fn()
    const { rerender } = render(
      <EventCard event={makeEvent()} decision={null} needsDecision={false}
        onDecide={onDecide} onOpenLightbox={vi.fn()} eventIdx={0} />,
    )
    expect(screen.queryByRole('button', { name: /dudosa/i })).not.toBeInTheDocument()
    rerender(
      <EventCard event={makeEvent()} decision={null} needsDecision={false}
        onDecide={onDecide} onOpenLightbox={vi.fn()} eventIdx={0} allowUnsure />,
    )
    await userEvent.click(screen.getByRole('button', { name: /dudosa/i }))
    expect(onDecide).toHaveBeenCalledWith('site1|1', 'obs-1', 'unsure')
  })

  it('marks unsure events with a dashed amber border', () => {
    const { container } = render(
      <EventCard event={makeEvent()} decision="unsure" needsDecision={false}
        onDecide={vi.fn()} onOpenLightbox={vi.fn()} eventIdx={0} allowUnsure />,
    )
    expect(container.firstChild).toHaveClass('border-dashed')
  })

  it('on pending cards, confirms the event of the frame being viewed', async () => {
    const onDecide = vi.fn()
    render(
      <EventCard
        event={makeEvent({
          repObsId: 'rep-a',
          frames: [
            { obsId: 'a1', mediaId: 'm1', img: '/a1.jpg', ts: '10:00', prob: 0.9, burstRepObsId: 'rep-a', rank: 1 },
            { obsId: 'b1', mediaId: 'm2', img: '/b1.jpg', ts: '12:00', prob: 0.6, burstRepObsId: 'rep-b', rank: 3 },
          ],
        })}
        decision={null}
        needsDecision={false}
        onDecide={onDecide}
        onOpenLightbox={vi.fn()}
        eventIdx={0}
        allowUnsure
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Imagen siguiente' }))
    await userEvent.click(screen.getByRole('button', { name: /^✓/ }))
    expect(onDecide).toHaveBeenCalledWith('site1|1', 'rep-b', 'confirmed')
  })
})
