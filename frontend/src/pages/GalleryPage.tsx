import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import YARLightbox, { type ControllerRef, type SlideImage } from 'yet-another-react-lightbox'
import Zoom from 'yet-another-react-lightbox/plugins/zoom'
import Captions from 'yet-another-react-lightbox/plugins/captions'
import 'yet-another-react-lightbox/styles.css'
import 'yet-another-react-lightbox/plugins/captions.css'
import { api } from '../api'
import EventCard from '../components/EventCard'
import type { CrossSpecies, DecisionValue, DetectionEvent } from '../types'
import { clearDraft, draftKey, loadDraft, saveDraft } from '../utils/draft'

interface EventSlide extends SlideImage {
  eventKey: string
  repObsId: string
  siteId: string
  occasion: number
  prob: number
  frameTs: string
}

/**
 * rounds:  iterative review, one detection event per cell and round
 * pending: cells whose events ran out with some marked unsure ("pending expert review")
 * review:  species complete: audit / correct decisions (locked by default)
 */
type Mode = 'rounds' | 'pending' | 'review'

function Spinner({ sm }: { sm?: boolean }) {
  const sz = sm ? 'w-4 h-4 border' : 'w-8 h-8 border-2'
  return <div className={`${sz} border-zinc-600 border-t-blue-500 rounded-full animate-spin`} />
}

export default function GalleryPage() {
  const { species } = useParams<{ species: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()

  const [mode, setMode] = useState<Mode>('rounds')
  const [events, setEvents] = useState<DetectionEvent[]>([])
  const [spStats, setSpStats] = useState<{ species_name: string; n_total_combos: number; n_confirmed_combos: number; n_resolved: number; n_pending: number; current_iteration?: number } | null>(null)
  const [reviewParams, setReviewParams] = useState<{ gap_seconds: number; occasion_days: number; min_score: number } | null>(null)
  const [iteration, setIteration] = useState(1)
  const [totalIterations, setTotalIterations] = useState(1)
  const [sessionId, setSessionId] = useState('')
  const [decisions, setDecisions] = useState<Record<string, DecisionValue>>({})
  // Pending cards can hold several events: remember which one was confirmed.
  const [chosenRep, setChosenRep] = useState<Record<string, string>>({})
  const [needsDecision, setNeedsDecision] = useState<Set<string>>(new Set())
  const [siteIdx, setSiteIdx] = useState(0)
  const [lbOpen, setLbOpen] = useState(false)
  const [lbEvIdx, setLbEvIdx] = useState(0)   // which event is shown in the lightbox
  const [lbIndex, setLbIndex] = useState(0)   // frame index within that event
  const [lbInverted, setLbInverted] = useState(false)
  const [lbBrightness, setLbBrightness] = useState(100)
  const [lbContrast, setLbContrast] = useState(100)
  const [lbRotation, setLbRotation] = useState(0)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [locked, setLocked] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'danger' | 'warning' } | null>(null)
  const decisionsRef = useRef(decisions)
  decisionsRef.current = decisions
  // Progress of the unfinished round kept on the server ("Save progress").
  const [progressAt, setProgressAt] = useState<string | null>(null)
  const [progressState, setProgressState] = useState<'idle' | 'dirty' | 'saving' | 'error'>('idle')
  const lastSentRef = useRef('')
  // Other target species that can be confirmed from a photo (lightbox panel).
  const [targetSpecies, setTargetSpecies] = useState<string[]>([])
  const [crossByCell, setCrossByCell] = useState<Record<string, CrossSpecies[]>>({})
  const [crossBusy, setCrossBusy] = useState(false)
  const toggleCrossRef = useRef<(i: number) => void>(() => {})
  const progressTimerRef = useRef<number | null>(null)
  const decideInLbRef = useRef<(d: DecisionValue) => void>(() => {})
  const lbControllerRef = useRef<ControllerRef>(null)
  const gridTopRef = useRef<HTMLDivElement>(null)

  const completed = mode === 'review'
  const draftId = draftKey(sessionId, species ?? '', mode, iteration)

  function iterationColor(iter: number, total: number): string {
    const ratio = total > 1 ? (iter - 1) / (total - 1) : 0
    const hue = Math.round(120 - ratio * 120)
    return `hsl(${hue}, 70%, 40%)`
  }

  function showToast(msg: string, type: 'success' | 'danger' | 'warning', ms = 3000) {
    setToast({ msg, type })
    setTimeout(() => setToast(null), ms)
  }

  // ── Loading ────────────────────────────────────────────────────────────────
  //
  // The phase reported by /api/species decides what is shown. In the rounds
  // phase the gallery opens at the species' current round (re-entering a
  // species continues where it was left, instead of round 1).
  const load = useCallback(async (iter?: number) => {
    if (!species) return
    setLoading(true)
    setError(null)
    try {
      const [allStats, state] = await Promise.all([api.getSpecies(), api.getState()])
      const sp = allStats.find((s) => s.species_safe === species)
      const sid = state.session_dir ?? ''
      setSessionId(sid)
      if (state.config?.total_iterations) setTotalIterations(state.config.total_iterations)
      if (state.config) setReviewParams({ gap_seconds: state.config.gap_seconds, occasion_days: state.config.occasion_days, min_score: state.config.min_score_by_species?.[sp?.species_name ?? ''] ?? state.config.min_score })
      if (sp) setSpStats(sp)
      setTargetSpecies(state.config?.target_species ?? [])

      let nextMode: Mode = 'rounds'
      let nextIter = iter ?? sp?.current_iteration ?? 1
      let evts: DetectionEvent[] = []
      const pre: Record<string, DecisionValue> = {}

      if (sp?.phase === 'complete') {
        nextMode = 'review'
        evts = await api.getReview(species)
        evts.forEach((ev) => { pre[ev.key] = ev.status === 'confirmed' ? 'confirmed' : 'rejected' })
      } else if (sp?.phase === 'pending') {
        nextMode = 'pending'
        nextIter = 0
        evts = await api.getPending(species)
      } else {
        const [roundEvts, saved] = await Promise.all([
          api.getEvents(species, nextIter),
          api.getDecisions(species, nextIter),
        ])
        evts = roundEvts
        const byObs = new Map<string, string>()
        evts.forEach((e) => {
          byObs.set(e.repObsId, e.key)
          e.frames.forEach((f) => byObs.set(f.obsId, e.key))
        })
        saved.confirmed.forEach((id) => { const k = byObs.get(id); if (k) pre[k] = 'confirmed' })
        ;(saved.unsure ?? []).forEach((id) => { const k = byObs.get(id); if (k) pre[k] = 'unsure' })
      }

      // Unfinished work from an earlier visit: the progress saved in the
      // session folder and/or the draft kept by this browser (older versions
      // only had the latter). The most recent of the two wins.
      const keys = new Set(evts.map((e) => e.key))
      let draft = nextMode === 'review' ? null : loadDraft(draftKey(sid, species, nextMode, nextIter))
      let serverAt: string | null = null
      let serverSig = ''
      if (nextMode !== 'review') {
        const server = await api.getProgress(species, nextMode, nextIter).then((r) => r.progress).catch(() => null)
        if (server && server.mode === nextMode && server.iteration === nextIter) {
          serverAt = server.saved_at
          serverSig = JSON.stringify([server.decisions, server.chosen_rep])
          const tServer = Date.parse(server.saved_at) || 0
          const tLocal = draft?.savedAt ? Date.parse(draft.savedAt) || 0 : 0
          if (!draft || tServer >= tLocal) {
            draft = { decisions: server.decisions, chosenRep: server.chosen_rep, savedAt: server.saved_at }
          }
        }
      }
      if (draft) {
        Object.entries(draft.decisions).forEach(([k, d]) => { if (keys.has(k)) pre[k] = d })
      }
      if (progressTimerRef.current) window.clearTimeout(progressTimerRef.current)
      // When the server already holds exactly this, there is nothing to send;
      // otherwise (e.g. a draft from an older version) the next autosave sends it.
      lastSentRef.current = serverSig && serverSig === JSON.stringify([pre, draft?.chosenRep ?? {}])
        ? JSON.stringify([species, nextMode, nextIter, pre, draft?.chosenRep ?? {}])
        : ''
      setProgressAt(serverAt)
      setProgressState('idle')

      setMode(nextMode)
      setIteration(nextIter)
      setEvents(evts)
      setDecisions(pre)
      setChosenRep(draft?.chosenRep ?? {})
      setNeedsDecision(new Set())
      setSiteIdx(0)
      setLocked(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al cargar eventos')
    } finally {
      setLoading(false)
    }
  }, [species])

  useEffect(() => { load() }, [load])

  // Keep a draft of the decisions taken in this round, so hours of review
  // aren't lost if the browser is closed before saving.
  useEffect(() => {
    if (loading || mode === 'review' || !sessionId) return
    if (Object.keys(decisions).length === 0) return
    saveDraft(draftId, { decisions, chosenRep })
  }, [decisions, chosenRep, draftId, loading, mode, sessionId])

  // Save the progress of the round in the session folder as well: a couple of
  // seconds after the last click, with the "Save progress" button, and when
  // the page is hidden or closed. Results only change when the round is
  // finished; this just lets the reviewer stop half-way and continue later.
  const progressPayload = useCallback(() => {
    if (!species || loading || saving || mode === 'review' || !sessionId) return null
    const sig = JSON.stringify([species, mode, iteration, decisions, chosenRep])
    if (sig === lastSentRef.current) return null
    if (Object.keys(decisions).length === 0 && !lastSentRef.current) return null
    return { sig, body: { mode: mode as 'rounds' | 'pending', iteration, decisions, chosen_rep: chosenRep } }
  }, [species, loading, saving, mode, sessionId, iteration, decisions, chosenRep])

  const flushProgress = useCallback(async (opts: { keepalive?: boolean; announce?: boolean } = {}) => {
    if (progressTimerRef.current) { window.clearTimeout(progressTimerRef.current); progressTimerRef.current = null }
    const p = progressPayload()
    if (!p || !species) {
      if (opts.announce) showToast(t('gallery.progress_up_to_date'), 'success')
      return
    }
    setProgressState('saving')
    try {
      const r = await api.saveProgress(species, p.body, opts.keepalive)
      lastSentRef.current = p.sig
      setProgressAt(r.saved_at)
      setProgressState('idle')
      if (opts.announce) showToast(t('gallery.progress_saved_toast', { n: Object.keys(p.body.decisions).length }), 'success')
    } catch (e) {
      setProgressState('error')
      if (opts.announce) showToast(e instanceof Error ? e.message : t('gallery.toast_error'), 'danger')
    }
  // showToast and t are stable enough here; the payload drives the effect.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progressPayload, species])

  useEffect(() => {
    if (!progressPayload()) return
    setProgressState('dirty')
    progressTimerRef.current = window.setTimeout(() => { flushProgress() }, 2500)
    return () => { if (progressTimerRef.current) window.clearTimeout(progressTimerRef.current) }
  }, [progressPayload, flushProgress])

  const flushRef = useRef(flushProgress)
  flushRef.current = flushProgress
  useEffect(() => {
    const onHide = () => { if (document.visibilityState === 'hidden') flushRef.current({ keepalive: true }) }
    const onPageHide = () => flushRef.current({ keepalive: true })
    document.addEventListener('visibilitychange', onHide)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      document.removeEventListener('visibilitychange', onHide)
      window.removeEventListener('pagehide', onPageHide)
      flushRef.current()   // leaving the gallery inside the app
    }
  }, [])

  // ── Sites (one page per location) ────────────────────────────────────────
  const { sites, bySite, indexByKey } = useMemo(() => {
    const bySite: Record<string, DetectionEvent[]> = {}
    const sites: string[] = []
    const indexByKey = new Map<string, number>()
    events.forEach((ev, i) => {
      indexByKey.set(ev.key, i)
      if (!bySite[ev.siteId]) { bySite[ev.siteId] = []; sites.push(ev.siteId) }
      bySite[ev.siteId].push(ev)
    })
    return { sites, bySite, indexByKey }
  }, [events])

  const currentSite = sites[Math.min(siteIdx, Math.max(sites.length - 1, 0))]
  const siteEvents = currentSite ? bySite[currentSite] : []
  const decidedCount = events.reduce((n, e) => n + (decisions[e.key] ? 1 : 0), 0)

  function goSite(i: number) {
    setSiteIdx(Math.max(0, Math.min(i, sites.length - 1)))
    gridTopRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // The grid behind the lightbox follows the event shown in it.
  useEffect(() => {
    if (!lbOpen) return
    const ev = events[lbEvIdx]
    if (!ev) return
    const i = sites.indexOf(ev.siteId)
    if (i >= 0 && i !== siteIdx) setSiteIdx(i)
  }, [lbOpen, lbEvIdx, events, sites, siteIdx])

  // ── Lightbox keyboard ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!lbOpen) return
    const curSiteId = events[lbEvIdx]?.siteId
    const siteIndices = events
      .map((ev, i) => ({ i, siteId: ev.siteId }))
      .filter(x => x.siteId === curSiteId)
    const posInSite = siteIndices.findIndex(x => x.i === lbEvIdx)

    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'y' || e.key === 'Y') decideInLbRef.current('confirmed')
      if (e.key === 'n' || e.key === 'N') decideInLbRef.current('rejected')
      if ((e.key === 'u' || e.key === 'U') && mode !== 'review') decideInLbRef.current('unsure')
      if (/^[1-9]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) toggleCrossRef.current(Number(e.key) - 1)
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        e.stopPropagation()
        if (posInSite > 0) {
          setLbEvIdx(siteIndices[posInSite - 1].i)
          setLbIndex(0)
        }
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        e.stopPropagation()
        if (posInSite < siteIndices.length - 1) {
          setLbEvIdx(siteIndices[posInSite + 1].i)
          setLbIndex(0)
        }
      }
    }
    window.addEventListener('keydown', onKey, { capture: true })
    return () => window.removeEventListener('keydown', onKey, { capture: true })
  }, [lbOpen, lbEvIdx, events, mode])

  const lbEv = events[lbEvIdx]
  const lbSlides: EventSlide[] = lbEv
    ? lbEv.frames.map((fr, i) => ({
        src: fr.img,
        title: t('gallery.lb_title', {
          site: lbEv.siteId,
          occasion: lbEv.occasion,
          idx: i + 1,
          total: lbEv.frames.length,
          meta: fr.prob != null ? `${(fr.prob * 100).toFixed(0)}%  ·  ${fr.ts}` : fr.ts,
        }),
        eventKey: lbEv.key,
        repObsId: lbEv.repObsId,
        siteId: lbEv.siteId,
        occasion: lbEv.occasion,
        prob: fr.prob ?? 0,
        frameTs: fr.ts,
      }))
    : []

  // ── Other species seen in the photo ───────────────────────────────────────
  const otherSpecies = useMemo(
    () => targetSpecies.filter((sp) => sp !== spStats?.species_name),
    [targetSpecies, spStats?.species_name],
  )
  const crossCell = lbEv ? `${lbEv.siteId}|${lbEv.occasion}` : ''
  const crossReadOnly = completed && locked

  const refreshCross = useCallback(async (siteId: string, occasion: number) => {
    try {
      const r = await api.getCross(siteId, occasion)
      setCrossByCell((prev) => ({ ...prev, [`${siteId}|${occasion}`]: r.species }))
    } catch { /* the panel just stays as it was */ }
  }, [])

  useEffect(() => {
    if (!lbOpen || !lbEv || otherSpecies.length === 0) return
    refreshCross(lbEv.siteId, lbEv.occasion)
  }, [lbOpen, lbEv, otherSpecies.length, refreshCross])

  async function toggleCross(i: number) {
    const sp = otherSpecies[i]
    const ev = events[lbEvIdx]
    const frame = ev?.frames[lbIndex]
    if (!sp || !ev || !frame || crossBusy || crossReadOnly) return
    const cur = crossByCell[`${ev.siteId}|${ev.occasion}`]?.find((c) => c.scientificName === sp)
    if (cur?.status === 'confirmed') {
      showToast(t('gallery.cross_already', { species: sp }), 'warning')
      return
    }
    setCrossBusy(true)
    try {
      if (cur?.status === 'cross') {
        await api.removeCross(sp, ev.siteId, ev.occasion)
        showToast(t('gallery.cross_removed', { species: sp, site: ev.siteId, occasion: ev.occasion }), 'success')
      } else {
        await api.addCross({
          species: sp, site_id: ev.siteId, occasion: ev.occasion,
          media_id: frame.mediaId, observation_id: frame.isContext ? '' : frame.obsId,
          source_species: spStats?.species_name ?? '',
        })
        showToast(t('gallery.cross_added', { species: sp, site: ev.siteId, occasion: ev.occasion }), 'success')
      }
      await refreshCross(ev.siteId, ev.occasion)
    } catch (e) {
      showToast(e instanceof Error ? e.message : t('gallery.toast_error'), 'danger')
    } finally {
      setCrossBusy(false)
    }
  }
  toggleCrossRef.current = (i) => { toggleCross(i) }

  const openLightbox = useCallback((evIdx: number, frIdx: number) => {
    setLbEvIdx(evIdx)
    setLbIndex(frIdx)
    setLbInverted(false)
    setLbOpen(true)
  }, [])

  function resetLbFilters() {
    setLbBrightness(100)
    setLbContrast(100)
    setLbRotation(0)
    setLbInverted(false)
  }

  // Stable identity so memoised cards don't re-render on every decision.
  const decide = useCallback((key: string, repObsId: string, d: DecisionValue) => {
    setDecisions((prev) => (prev[key] === d ? prev : { ...prev, [key]: d }))
    setChosenRep((prev) => (prev[key] === repObsId ? prev : { ...prev, [key]: repObsId }))
    setNeedsDecision((prev) => {
      if (!prev.has(key)) return prev
      const s = new Set(prev); s.delete(key); return s
    })
  }, [])

  function decideInLb(d: DecisionValue) {
    if (completed && locked) return
    const ev = events[lbEvIdx]
    if (!ev) return
    const frameRep = ev.frames[lbIndex]?.burstRepObsId ?? ev.repObsId
    decide(ev.key, frameRep, d)
    if (!completed) {
      // decisionsRef mirrors state as of the last render, so it doesn't
      // include the decision just made above (setDecisions hasn't
      // committed yet) -- merge it in locally, or the fallback loop below
      // (which starts at 0, unlike the same-site one) can find the event
      // we just decided and re-select itself instead of closing.
      const updated = { ...decisionsRef.current, [ev.key]: d }

      // Prefer the next undecided occasion at the same site (matches the
      // Arrow Up/Down navigation), so confirming/rejecting doesn't jump to
      // an unrelated site. Only fall back to searching the whole list once
      // the current site has no undecided occasions left.
      const siteIndices = events
        .map((e, i) => ({ i, siteId: e.siteId }))
        .filter((x) => x.siteId === ev.siteId)
      const posInSite = siteIndices.findIndex((x) => x.i === lbEvIdx)
      for (let p = posInSite + 1; p < siteIndices.length; p++) {
        const i = siteIndices[p].i
        if (!updated[events[i].key]) {
          setLbEvIdx(i)
          setLbIndex(0)
          return
        }
      }
      for (let i = 0; i < events.length; i++) {
        if (!updated[events[i].key]) {
          setLbEvIdx(i)
          setLbIndex(0)
          return
        }
      }
      setLbOpen(false)
    }
  }
  decideInLbRef.current = decideInLb

  function confirmAllInSite() {
    setDecisions((prev) => {
      const next = { ...prev }
      siteEvents.forEach((ev) => { next[ev.key] = 'confirmed' })
      return next
    })
    setNeedsDecision((prev) => {
      const s = new Set(prev)
      siteEvents.forEach((ev) => s.delete(ev.key))
      return s
    })
  }

  function flagUndecided(undecided: DetectionEvent[]) {
    setNeedsDecision(new Set(undecided.map((e) => e.key)))
    const first = undecided[0]
    setSiteIdx(sites.indexOf(first.siteId))
    setTimeout(() => {
      document.querySelector(`[data-key="${CSS.escape(first.key)}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 50)
  }

  async function handleSaveRound() {
    const undecided = events.filter((e) => !decisions[e.key])
    if (undecided.length > 0) {
      flagUndecided(undecided)
      showToast(t('gallery.needs_decision_count', { n: undecided.length }), 'warning', 6000)
      return
    }
    if (!species) return
    if (progressTimerRef.current) window.clearTimeout(progressTimerRef.current)
    setSaving(true)
    try {
      const confirmedIds = events.filter((e) => decisions[e.key] === 'confirmed').map((e) => e.repObsId)
      const unsureIds = events.filter((e) => decisions[e.key] === 'unsure').map((e) => e.repObsId)
      const result = await api.saveDecisions(species, iteration, confirmedIds, unsureIds)
      clearDraft(draftId)
      if (result.done && result.pending > 0) {
        showToast(t('gallery.toast_rounds_done_pending', { n: result.pending }), 'warning', 6000)
        await load()
      } else if (result.done) {
        showToast(t('gallery.toast_done'), 'success')
        setTimeout(() => navigate('/species'), 1500)
      } else {
        showToast(t('gallery.toast_saved', { n: result.next_iteration, remaining: result.remaining }), 'success')
        await load(result.next_iteration!)
      }
    } catch (e) {
      showToast(e instanceof Error ? e.message : t('gallery.toast_error'), 'danger')
    } finally {
      setSaving(false)
    }
  }

  async function handleSavePending() {
    if (!species) return
    const confirmed = events
      .filter((e) => decisions[e.key] === 'confirmed')
      .map((e) => chosenRep[e.key] ?? e.repObsId)
    const rejectedKeys = events.filter((e) => decisions[e.key] === 'rejected').map((e) => e.key)
    if (confirmed.length === 0 && rejectedKeys.length === 0) {
      showToast(t('gallery.pending_nothing_to_save'), 'danger')
      return
    }
    if (progressTimerRef.current) window.clearTimeout(progressTimerRef.current)
    setSaving(true)
    try {
      await api.settlePending(species, confirmed, rejectedKeys)
      clearDraft(draftId)
      const left = events.length - confirmed.length - rejectedKeys.length
      if (left === 0) {
        showToast(t('gallery.toast_done'), 'success')
        setTimeout(() => navigate('/species'), 1500)
      } else {
        showToast(t('gallery.toast_pending_saved', { n: left }), 'success', 5000)
        await load()
      }
    } catch (e) {
      showToast(e instanceof Error ? e.message : t('gallery.toast_error'), 'danger')
    } finally {
      setSaving(false)
    }
  }

  // Go back to the last finished round (e.g. decisions taken thinking of the
  // wrong species). The current round's progress is kept for later.
  const prevRound = mode === 'pending' ? (spStats?.current_iteration ?? 1) - 1 : iteration - 1
  async function handleReopen() {
    if (!species || prevRound < 1) return
    if (!window.confirm(t('gallery.reopen_confirm', { n: prevRound }))) return
    await flushProgress()
    setSaving(true)
    try {
      const r = await api.reopenRound(species)
      showToast(t('gallery.reopen_done', { n: r.iteration }), 'success', 5000)
      await load(r.iteration)
    } catch (e) {
      showToast(e instanceof Error ? e.message : t('gallery.toast_error'), 'danger', 8000)
    } finally {
      setSaving(false)
    }
  }

  async function handleUpdateDecisions() {
    if (!species) return
    setSaving(true)
    try {
      const confirmedKeys = events
        .filter((e) => decisions[e.key] === 'confirmed')
        .map((e) => e.key)
      await api.updateDecisions(species, confirmedKeys)
      setLocked(true)
      showToast(t('gallery.toast_updated'), 'success')
    } catch (e) {
      showToast(e instanceof Error ? e.message : t('gallery.toast_error'), 'danger')
    } finally {
      setSaving(false)
    }
  }

  const lbCurrentEv = events[lbEvIdx]
  const lbDecision  = lbCurrentEv ? decisions[lbCurrentEv.key] : undefined
  // Another species confirmed from a photo of this event while the card is
  // "unsure": consistent only if the photo has more than one animal, so the
  // reviewer gets a reminder (not a block).
  const lbCrossHere = lbCurrentEv
    ? (crossByCell[`${lbCurrentEv.siteId}|${lbCurrentEv.occasion}`] ?? [])
        .filter((c) => c.status === 'cross' && lbCurrentEv.frames.some((f) => f.mediaId === c.mediaID))
        .map((c) => c.scientificName)
    : []
  const lbReadOnly  = completed && locked
  const allowUnsure = mode !== 'review'

  function siteLabel(site: string): string {
    const evs = bySite[site] ?? []
    const done = evs.reduce((n, e) => n + (decisions[e.key] ? 1 : 0), 0)
    return `${site}  ·  ${done}/${evs.length}`
  }

  const siteNav = sites.length > 1 && (
    <div className="flex flex-wrap items-center gap-2 mb-3">
      <button
        className="px-3 py-1.5 text-sm rounded bg-zinc-700 text-white hover:bg-zinc-600 disabled:opacity-40"
        onClick={() => goSite(siteIdx - 1)}
        disabled={siteIdx === 0}
      >
        ‹ {t('gallery.prev_site')}
      </button>
      <select
        aria-label={t('gallery.location')}
        className="px-2 py-1.5 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100"
        value={siteIdx}
        onChange={(e) => goSite(Number(e.target.value))}
      >
        {sites.map((s, i) => <option key={s} value={i}>{siteLabel(s)}</option>)}
      </select>
      <button
        className="px-3 py-1.5 text-sm rounded bg-zinc-700 text-white hover:bg-zinc-600 disabled:opacity-40"
        onClick={() => goSite(siteIdx + 1)}
        disabled={siteIdx >= sites.length - 1}
      >
        {t('gallery.next_site')} ›
      </button>
      <span className="text-sm text-zinc-500 dark:text-zinc-400">
        {t('gallery.site_position', { i: siteIdx + 1, n: sites.length })}
        {' · '}
        {t('gallery.decided_progress', { done: decidedCount, total: events.length })}
      </span>
    </div>
  )

  return (
    <div className="max-w-screen-xl mx-auto px-4 py-4">
      <div className="mb-3">
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-baseline gap-3">
            <h2 className="text-2xl font-semibold mb-0">
              {t('gallery.title')}
            </h2>
            <span className="italic text-zinc-500 dark:text-zinc-400 text-lg">— {species?.replace(/_/g, ' ')}</span>
            {mode === 'review' ? (
              <span className="flex items-center gap-1">
                <span className="px-2 py-0.5 text-xs font-medium bg-emerald-600 text-white rounded-full">
                  {t('gallery.completed_badge')}
                </span>
                <button
                  className="ml-1 bg-transparent border-0 cursor-pointer text-xl leading-none"
                  title={locked ? t('gallery.unlock_tooltip') : t('gallery.lock_tooltip')}
                  onClick={() => setLocked((l) => !l)}
                >
                  {locked ? '🔒' : '🔓'}
                </button>
              </span>
            ) : mode === 'pending' ? (
              <span className="px-2 py-0.5 text-xs font-medium bg-amber-500 text-white rounded-full">
                {t('gallery.pending_badge')}
              </span>
            ) : (
              <span className="px-2 py-0.5 text-xs font-medium bg-zinc-600 text-zinc-200 rounded-full">
                {t('gallery.round', { n: iteration })}
              </span>
            )}
            {mode !== 'review' && prevRound >= 1 && (
              <button
                className="ml-1 px-2 py-0.5 text-xs rounded-full border border-zinc-500 text-zinc-500 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-700 disabled:opacity-50"
                onClick={handleReopen}
                disabled={saving || loading}
                title={t('gallery.reopen_tooltip', { n: prevRound })}
              >
                ‹ {t('gallery.reopen_btn', { n: prevRound })}
              </button>
            )}
          </div>
          <button
            className="px-4 py-1.5 text-sm rounded transition-colors bg-zinc-700 text-white hover:bg-zinc-600 flex items-center gap-1.5"
            onClick={() => navigate('/species')}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M10 3L5 8l5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            {t('gallery.back')}
          </button>
        </div>

        {mode === 'rounds' && reviewParams && (
          <p
            className="text-zinc-500 dark:text-zinc-400 text-sm mb-3"
            dangerouslySetInnerHTML={{
              __html: t('gallery.intro_hint', {
                gap: reviewParams.gap_seconds,
                days: reviewParams.occasion_days,
                minScore: reviewParams.min_score,
                species: spStats?.species_name ?? species?.replace(/_/g, ' '),
              }),
            }}
          />
        )}

        {spStats && (() => {
          const pct = spStats.n_total_combos > 0
            ? Math.round(spStats.n_resolved / spStats.n_total_combos * 100) : 0
          const pctPending = spStats.n_total_combos > 0
            ? Math.round((spStats.n_pending ?? 0) / spStats.n_total_combos * 100) : 0
          return (
            <>
              <span className="text-zinc-500 dark:text-zinc-400 text-sm">
                <span className="text-emerald-500 font-semibold">{spStats.n_confirmed_combos}</span>
                {' '}{t('gallery.confirmed_periods', { n: spStats.n_confirmed_combos }).replace(/^\d+ /, '')}
                {' · '}
                {t('gallery.reviewed', { resolved: spStats.n_resolved, total: spStats.n_total_combos })}
                {(spStats.n_pending ?? 0) > 0 && (
                  <>
                    {' · '}
                    <span className="text-amber-500 font-semibold">
                      {t('gallery.pending_count', { n: spStats.n_pending })}
                    </span>
                  </>
                )}
                {' · '}
                <span className={pct === 100 ? 'text-emerald-500 font-bold' : 'text-amber-400 font-semibold'}>
                  {pct}%
                </span>
              </span>
              <div className="w-full bg-zinc-200 dark:bg-zinc-700 rounded-full mt-2 mb-1 flex overflow-hidden" style={{ height: 6 }}>
                <div className="bg-emerald-500 h-full" style={{ width: `${pct}%` }} />
                <div className="bg-amber-500 h-full" style={{ width: `${pctPending}%` }} />
              </div>
              <div className="flex items-start gap-2.5 px-3.5 py-2.5 rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 text-sm mt-2">
                <svg className="flex-shrink-0 mt-0.5" width="15" height="15" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                  <path d="M7.5 1C3.91 1 1 3.91 1 7.5S3.91 14 7.5 14 14 11.09 14 7.5 11.09 1 7.5 1zm.75 10.5h-1.5V7h1.5v4.5zm0-6h-1.5V4h1.5v1.5z" fill="currentColor"/>
                </svg>
                <span>
                  {mode === 'review'
                    ? (locked ? t('gallery.review_hint_locked') : t('gallery.review_hint_unlocked'))
                    : mode === 'pending'
                    ? t('gallery.pending_hint')
                    : t('gallery.round_hint_unsure', { n: iteration })}
                </span>
              </div>
            </>
          )
        })()}
      </div>

      {/* Floating action buttons */}
      {mode !== 'review' && (
        <div className="fixed bottom-6 right-6 z-[200] flex gap-2">
          {mode === 'rounds' && (
            <button
              className="px-5 py-2.5 bg-zinc-600 text-white rounded-lg shadow-xl hover:bg-zinc-500 transition-colors disabled:opacity-50 flex items-center gap-2"
              onClick={confirmAllInSite}
              disabled={saving || siteEvents.length === 0}
              title={t('gallery.confirm_all_site_tooltip')}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <path d="M2 8.5L5.5 12L10 6M5 5.5L8.5 9L13 3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              {t('gallery.confirm_all_site')}
            </button>
          )}
          <div className="flex flex-col items-end justify-center gap-0.5">
            <button
              className="px-4 py-2.5 bg-white dark:bg-zinc-800 text-zinc-800 dark:text-zinc-100 border border-zinc-300 dark:border-zinc-600 rounded-lg shadow-xl hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors disabled:opacity-50 flex items-center gap-2"
              onClick={() => flushProgress({ announce: true })}
              disabled={saving || progressState === 'saving' || events.length === 0}
              title={t('gallery.save_progress_tooltip')}
            >
              {progressState === 'saving' ? <Spinner sm /> : (
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                  <path d="M8 2v8M5 7l3 3 3-3M3 13.5h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              )}
              {t('gallery.save_progress')}
            </button>
            <span className={`text-[11px] px-1.5 rounded bg-white/80 dark:bg-zinc-900/80 ${progressState === 'error' ? 'text-red-500' : 'text-zinc-500 dark:text-zinc-400'}`}>
              {progressState === 'error'
                ? t('gallery.progress_error')
                : progressState === 'dirty' || progressState === 'saving'
                ? t('gallery.progress_saving')
                : progressAt
                ? t('gallery.progress_saved_at', { time: new Date(progressAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) })
                : ''}
            </span>
          </div>
          <button
            className="px-5 py-2.5 bg-emerald-600 text-white rounded-lg shadow-xl hover:bg-emerald-700 transition-colors disabled:opacity-50 flex items-center gap-2"
            onClick={mode === 'pending' ? handleSavePending : handleSaveRound}
            title={mode === 'rounds' ? t('gallery.finish_round_tooltip') : undefined}
            disabled={saving || events.length === 0}
          >
            {saving ? <Spinner sm /> : (
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <path d="M13 13.5H3a.5.5 0 0 1-.5-.5V3a.5.5 0 0 1 .5-.5h8l2.5 2.5V13a.5.5 0 0 1-.5.5z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M5 13.5V9.5h6v4M5 2.5v3h5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            )}
            {saving ? t('gallery.saving') : mode === 'rounds' ? t('gallery.finish_round') : t('gallery.save')}
          </button>
        </div>
      )}

      {completed && !locked && (
        <div className="fixed bottom-6 right-6 z-[200]">
          <button
            className="px-5 py-2.5 bg-amber-500 text-white rounded-lg shadow-xl hover:bg-amber-600 transition-colors disabled:opacity-50 flex items-center gap-2"
            onClick={handleUpdateDecisions}
            disabled={saving}
          >
            {saving && <Spinner sm />}
            {saving ? t('gallery.updating') : t('gallery.update_decisions')}
          </button>
        </div>
      )}

      {error && (
        <div className="px-4 py-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 mb-3">
          {error}
        </div>
      )}

      <div ref={gridTopRef} />
      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner />
        </div>
      ) : events.length === 0 ? (
        <div className="px-4 py-3 rounded-lg bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-300">
          {t('gallery.no_events')}
        </div>
      ) : currentSite && (
        <>
          {siteNav}
          <div className="mb-4 border border-zinc-700 rounded-lg p-3">
            <h5 className="mb-3 flex items-center text-base font-semibold">
              <span className="text-zinc-500 dark:text-zinc-400 font-normal">{t('gallery.location')}</span>
              <span className="ml-2">{currentSite}</span>
              <span className="ml-auto px-2 py-0.5 text-xs font-normal bg-zinc-600 text-zinc-200 rounded-full">
                {siteEvents.length === 1
                  ? t('gallery.periods', { n: siteEvents.length })
                  : t('gallery.periods_plural', { n: siteEvents.length })}
              </span>
              {mode === 'rounds' && (
                <span className="ml-2 px-2 py-0.5 text-xs font-normal rounded-full text-white"
                  style={{ backgroundColor: iterationColor(iteration, totalIterations) }}>
                  {t('gallery.confidence', {
                    min: Math.min(...siteEvents.map((e) => e.maxProb * 100)).toFixed(0),
                    max: Math.max(...siteEvents.map((e) => e.maxProb * 100)).toFixed(0),
                  })}
                </span>
              )}
            </h5>
            <hr className="border-zinc-700 mt-0 mb-3" />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '1rem' }}>
              {siteEvents.map((ev) => (
                <div key={ev.key} data-key={ev.key}>
                  <EventCard
                    event={ev}
                    decision={decisions[ev.key] ?? null}
                    needsDecision={needsDecision.has(ev.key)}
                    onDecide={decide}
                    onOpenLightbox={openLightbox}
                    eventIdx={indexByKey.get(ev.key) ?? 0}
                    readOnly={completed && locked}
                    allowUnsure={allowUnsure}
                  />
                </div>
              ))}
            </div>
          </div>
          {siteIdx < sites.length - 1 && (
            <div className="flex justify-center mb-24">
              <button
                className="px-5 py-2 text-sm rounded bg-blue-600 text-white hover:bg-blue-700"
                onClick={() => goSite(siteIdx + 1)}
              >
                {t('gallery.next_site_cta', { site: sites[siteIdx + 1] })} ›
              </button>
            </div>
          )}
        </>
      )}

      <style>{`.yarl__slide_image {
        width: 100%; height: 100%; object-fit: contain;
        max-width: 100% !important; max-height: 100% !important;
        filter: brightness(${lbBrightness}%) contrast(${lbContrast}%)${lbInverted ? ' invert(1)' : ''} !important;
        ${lbRotation !== 0 ? `transform: rotate(${lbRotation}deg) !important; ${lbRotation % 180 !== 0 ? 'max-width: 80vh !important; max-height: 80vw !important;' : ''}` : ''}
      }
      .yarl__slide_title {
        white-space: normal !important;
        overflow: visible !important;
      }`}</style>

      <YARLightbox
        key={lbEvIdx}
        open={lbOpen}
        close={() => { setLbOpen(false); resetLbFilters() }}
        index={lbIndex}
        slides={lbSlides}
        plugins={[Zoom, Captions]}
        carousel={{ finite: true, preload: 2 }}
        // No slide / fade transitions: frames of a detection event are compared
        // side by side in time, so they must switch instantly.
        animation={{ fade: 0, swipe: 0, navigation: 0 }}
        controller={{ ref: lbControllerRef }}
        on={{ view: ({ index }) => { setLbIndex(index); resetLbFilters() } }}
        zoom={{ maxZoomPixelRatio: 8, scrollToZoom: true }}
        captions={{ showToggle: false }}
        // Use the whole screen: photos are scaled up to fill it (see the
        // .yarl__slide_image rule), leaving room for the title, the decision
        // buttons and, on the left, the other species.
        styles={{ slide: { padding: `44px 56px 118px ${otherSpecies.length ? 252 : 56}px` } }}
        render={{
          controls: () => lbCurrentEv && (
            <>
            {otherSpecies.length > 0 && (
              <div
                className="absolute left-12 top-1/2 -translate-y-1/2 flex flex-col gap-1.5 w-48"
                style={{ zIndex: 10000 }}
              >
                <span className="text-[11px] leading-tight text-white/70 mb-0.5">{t('gallery.cross_title')}</span>
                {otherSpecies.map((sp, i) => {
                  const st = crossByCell[crossCell]?.find((c) => c.scientificName === sp)
                  const status = st?.status ?? 'none'
                  return (
                    <button
                      key={sp}
                      className={`px-2 py-1.5 text-left text-sm rounded transition-colors flex items-center gap-1.5 disabled:opacity-50 ${
                        status === 'cross'
                          ? 'bg-sky-600 text-white hover:bg-sky-700'
                          : status === 'confirmed'
                          ? 'border border-emerald-700 bg-black/40 text-emerald-400 cursor-default'
                          : 'border border-white/40 bg-black/40 text-white/90 hover:bg-sky-600 hover:border-sky-600 hover:text-white'
                      }`}
                      onClick={() => toggleCross(i)}
                      disabled={crossReadOnly}
                      title={status === 'confirmed'
                        ? t('gallery.cross_already', { species: sp })
                        : status === 'cross'
                        ? t('gallery.cross_undo_tooltip', { species: sp })
                        : t('gallery.cross_tooltip', { species: sp, site: lbCurrentEv.siteId, occasion: lbCurrentEv.occasion })}
                    >
                      {i < 9 && <span className="text-[10px] opacity-60 font-mono w-3">{i + 1}</span>}
                      <span className="italic flex-1 leading-tight break-words">{sp}</span>
                      {status !== 'none' && <span aria-hidden="true">✓</span>}
                    </button>
                  )
                })}
                {lbDecision === 'unsure' && lbCrossHere.length > 0 && (
                  <p role="note" className="mt-1 text-[11px] leading-snug text-amber-300">
                    {t('gallery.cross_unsure_note', { species: lbCrossHere.join(', ') })}
                  </p>
                )}
              </div>
            )}
            <div className="absolute bottom-0 left-0 right-0 flex flex-col items-center gap-3 px-4 pb-6" style={{ zIndex: 10000 }}>
              {lbCurrentEv.frames.length > 1 && lbCurrentEv.frames.length <= 40 && (
                <div className="flex gap-1">
                  {lbCurrentEv.frames.map((_, i) => (
                    <button
                      key={i}
                      aria-label={`${i + 1}/${lbCurrentEv.frames.length}`}
                      onClick={() => {
                        const delta = i - lbIndex
                        if (delta === 0) return
                        if (delta > 0) lbControllerRef.current?.next({ count: delta })
                        else lbControllerRef.current?.prev({ count: -delta })
                      }}
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        padding: 0,
                        border: 'none',
                        cursor: 'pointer',
                        background: i === lbIndex ? '#007aff' : 'rgba(255,255,255,.3)',
                      }}
                    />
                  ))}
                </div>
              )}
              {lbCurrentEv.frames.length > 40 && (
                <span className="text-xs text-white/70">{lbIndex + 1}/{lbCurrentEv.frames.length}</span>
              )}
              <div className="flex gap-3 w-full" style={{ maxWidth: allowUnsure ? 560 : 420 }}>
                <button
                  className={`flex-1 py-2.5 text-base rounded font-medium transition-colors disabled:opacity-50 ${
                    lbDecision === 'confirmed'
                      ? 'bg-emerald-600 text-white hover:bg-emerald-700'
                      : 'border border-emerald-600 bg-black/40 text-emerald-400 hover:bg-emerald-600 hover:text-white'
                  }`}
                  onClick={() => decideInLb('confirmed')}
                  disabled={lbReadOnly}
                >
                  {t('gallery.confirmed_btn')} <span className="opacity-60">(Y)</span>
                </button>
                {allowUnsure && (
                  <button
                    className={`flex-1 py-2.5 text-base rounded font-medium transition-colors disabled:opacity-50 ${
                      lbDecision === 'unsure'
                        ? 'bg-amber-500 text-white hover:bg-amber-600'
                        : 'border border-amber-500 bg-black/40 text-amber-400 hover:bg-amber-500 hover:text-white'
                    }`}
                    onClick={() => decideInLb('unsure')}
                  >
                    {t('gallery.unsure_btn')} <span className="opacity-60">(U)</span>
                  </button>
                )}
                <button
                  className={`flex-1 py-2.5 text-base rounded font-medium transition-colors disabled:opacity-50 ${
                    lbDecision === 'rejected'
                      ? 'bg-red-600 text-white hover:bg-red-700'
                      : 'border border-red-600 bg-black/40 text-red-400 hover:bg-red-600 hover:text-white'
                  }`}
                  onClick={() => decideInLb('rejected')}
                  disabled={lbReadOnly}
                >
                  {t('gallery.rejected_btn')} <span className="opacity-60">(N)</span>
                </button>
              </div>
            </div>
            </>
          ),
        }}
        toolbar={{
          buttons: [
            <label
              key="brightness"
              title={`${t('gallery.brightness')}: ${lbBrightness}%`}
              style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '0 6px', color: lbBrightness !== 100 ? '#fbbf24' : '#aaa', fontSize: 14, cursor: 'default' }}
            >
              ☀
              <input
                type="range" min={50} max={200} step={5} value={lbBrightness}
                onChange={(e) => setLbBrightness(Number(e.target.value))}
                onMouseDown={(e) => e.stopPropagation()}
                onTouchStart={(e) => e.stopPropagation()}
                style={{ width: 70, accentColor: '#fbbf24' }}
              />
            </label>,
            <label
              key="contrast"
              title={`${t('gallery.contrast')}: ${lbContrast}%`}
              style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '0 6px', color: lbContrast !== 100 ? '#fbbf24' : '#aaa', fontSize: 14, cursor: 'default' }}
            >
              ◑
              <input
                type="range" min={50} max={200} step={5} value={lbContrast}
                onChange={(e) => setLbContrast(Number(e.target.value))}
                onMouseDown={(e) => e.stopPropagation()}
                onTouchStart={(e) => e.stopPropagation()}
                style={{ width: 70, accentColor: '#fbbf24' }}
              />
            </label>,
            <button
              key="rotate"
              title={t('gallery.rotate')}
              className="yarl__button"
              onClick={() => setLbRotation((r) => (r + 90) % 360)}
              style={{ color: lbRotation !== 0 ? '#fbbf24' : undefined }}
            >
              ↻
            </button>,
            ...(lbBrightness !== 100 || lbContrast !== 100 || lbRotation !== 0 || lbInverted ? [
              <button
                key="reset"
                title={t('gallery.reset_filters')}
                className="yarl__button"
                onClick={resetLbFilters}
                style={{ color: '#f87171' }}
              >
                ⊘
              </button>,
            ] : []),
            <span key="sep0" style={{ width: 1, background: '#444', margin: '8px 4px' }} />,
            <button
              key="invert"
              title={t('gallery.invert')}
              className={`yarl__button ${lbInverted ? 'yarl__button--active' : ''}`}
              onClick={() => setLbInverted((v) => !v)}
              style={{ color: lbInverted ? '#fbbf24' : undefined }}
            >
              ⊙
            </button>,
            'close',
          ],
        }}
      />

      {toast && (
        <div className={`fixed bottom-0 right-0 m-3 z-[2000] px-4 py-3 rounded-lg text-white shadow-lg max-w-md ${
          toast.type === 'success' ? 'bg-emerald-600' : toast.type === 'warning' ? 'bg-amber-600' : 'bg-red-600'
        }`}>
          {toast.msg}
        </div>
      )}
    </div>
  )
}
