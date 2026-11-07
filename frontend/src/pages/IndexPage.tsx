import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import RelinkDialog from '../components/RelinkDialog'
import SessionNameEditor from '../components/SessionNameEditor'
import type { RelinkStatus, SpeciesStats } from '../types'
import { isPermissionDenied } from '../utils/imageError'
import { downloadFile } from '../utils/download'

function Spinner() {
  return <div className="w-8 h-8 border-2 border-zinc-600 border-t-blue-500 rounded-full animate-spin" />
}

export default function IndexPage() {
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const imgError = i18n.language.startsWith('es') ? '/img-error-es.svg' : '/img-error-en.svg'
  const [species, setSpecies] = useState<SpeciesStats[]>([])
  const [sessionInfo, setSessionInfo] = useState<{
    start: string; end: string
    occasion_days: number; gap_seconds: number; min_score: number
  } | null>(null)
  const [sessionName, setSessionName] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [relink, setRelink] = useState<RelinkStatus | null>(null)
  const [relinkOpen, setRelinkOpen] = useState(false)
  const [exporting, setExporting] = useState(false)

  function refresh() {
    return Promise.all([api.getSpecies(), api.getState()])
      .then(([sp, state]) => {
        setSpecies(sp)
        setSessionName(state.config?.session_name ?? '')
        setRelink(state.relink ?? null)
        if (state.config) setSessionInfo({
          start: state.config.study_start,
          end: state.config.study_end,
          occasion_days: state.config.occasion_days,
          gap_seconds: state.config.gap_seconds,
          min_score: state.config.min_score,
        })
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { refresh() }, [])

  async function handleExport() {
    setExporting(true)
    try {
      await downloadFile('/api/session/export', 'camtrap-verify-session.zip')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setExporting(false)
    }
  }

  if (loading) return (
    <div className="flex justify-center items-center" style={{ minHeight: 300 }}>
      <Spinner />
    </div>
  )

  if (error) return (
    <div className="max-w-screen-xl mx-auto px-4 py-4">
      <div className="px-4 py-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300">
        {error}
      </div>
    </div>
  )

  const totalCombos   = species.reduce((s, sp) => s + sp.n_total_combos, 0)
  const totalResolved = species.reduce((s, sp) => s + sp.n_resolved, 0)
  const totalDetected = species.reduce((s, sp) => s + sp.n_confirmed_combos, 0)
  const totalPending  = species.reduce((s, sp) => s + (sp.n_pending ?? 0), 0)
  const overallPct    = totalCombos > 0 ? Math.round(totalResolved / totalCombos * 100) : 0
  const pendingPct    = totalCombos > 0 ? Math.round(totalPending / totalCombos * 100) : 0
  // Results are available once every round is done; cells still pending
  // expert review appear there as NA (and as "pending" in cell_status_*.csv).
  const roundsDone    = species.length > 0 && species.every((sp) => sp.phase !== 'rounds')

  return (
    <div className="max-w-screen-xl mx-auto px-4 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
        <div className="flex flex-wrap items-baseline gap-x-3">
          <h2 className="text-2xl font-semibold mb-0 whitespace-nowrap">{t('index.title')}</h2>
          <span className="text-zinc-500 dark:text-zinc-400 text-sm">
            {t('index.periods_reviewed', { resolved: totalResolved, total: totalCombos })}
            {' · '}
            <span className="text-emerald-600 dark:text-emerald-400 font-semibold">
              {t('index.confirmed', { n: totalDetected })}
            </span>
            {totalPending > 0 && (
              <>
                {' · '}
                <span className="text-amber-500 font-semibold">{t('index.pending', { n: totalPending })}</span>
              </>
            )}
            {' · '}
            <span className={overallPct === 100
              ? 'text-emerald-600 dark:text-emerald-400 font-bold'
              : 'text-amber-500 dark:text-amber-400 font-semibold'}>
              {overallPct}%
            </span>
          </span>
        </div>
        <div className="flex gap-2">
          <button
            className="px-4 py-1.5 text-sm rounded transition-colors border border-zinc-300 dark:border-zinc-600 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-700 disabled:opacity-50 whitespace-nowrap"
            onClick={handleExport}
            disabled={exporting}
            title={t('index.export_session_tooltip')}
          >
            {exporting ? t('index.exporting') : t('index.export_session')}
          </button>
          <button
            className="px-4 py-1.5 text-sm rounded transition-colors bg-zinc-700 text-white hover:bg-zinc-600 flex items-center gap-1.5 whitespace-nowrap"
            onClick={() => navigate(-1)}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M10 3L5 8l5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            {t('gallery.back')}
          </button>
          <button
            className={`px-4 py-1.5 text-sm rounded transition-colors whitespace-nowrap ${
              roundsDone
                ? 'bg-emerald-600 text-white hover:bg-emerald-700'
                : 'bg-zinc-700 text-zinc-400 cursor-not-allowed opacity-60'
            }`}
            disabled={!roundsDone}
            onClick={() => navigate('/results')}
            title={!roundsDone
              ? `${t('index.see_results')} (${overallPct}%)`
              : totalPending > 0
              ? t('index.see_results_pending', { n: totalPending })
              : t('index.see_results')}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true" style={{ display: 'inline', marginRight: 6, verticalAlign: 'middle' }}>
              <path d="M9 17H7A5 5 0 0 1 7 7h2M15 7h2a5 5 0 0 1 0 10h-2M8 12h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
            </svg>
            {t('index.see_results')}
          </button>
        </div>
      </div>

      <div className="text-sm text-zinc-700 dark:text-zinc-300 mb-1 flex items-center gap-1.5 min-w-0">
        <span className="text-zinc-500 dark:text-zinc-400 flex-shrink-0">{t('session_name.session')}</span>
        <SessionNameEditor sessionDir="" name={sessionName} fallback={t('session_name.none')}
          onRenamed={setSessionName} className="font-medium" />
      </div>

      {sessionInfo && (
        <p
          className="text-zinc-500 dark:text-zinc-400 mb-2 text-sm"
          dangerouslySetInnerHTML={{
            __html: t('index.subtitle', {
              start: sessionInfo.start,
              end: sessionInfo.end,
              days: sessionInfo.occasion_days,
            }),
          }}
        />
      )}
      <div className="w-full bg-zinc-200 dark:bg-zinc-700 rounded-full mb-4 flex overflow-hidden" style={{ height: 8 }}>
        <div
          className="bg-emerald-500 h-full transition-all"
          style={{ width: `${overallPct}%` }}
          role="progressbar"
          aria-valuenow={overallPct}
          aria-valuemin={0}
          aria-valuemax={100}
        />
        <div className="bg-amber-500 h-full transition-all" style={{ width: `${pendingPct}%` }} />
      </div>

      {relink?.needs_relink && (
        <div className="flex flex-wrap items-center gap-3 px-4 py-3 rounded-lg bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-300 text-sm mb-4">
          <span className="flex-1 min-w-[16rem]">
            {relink.camtrap_dir_ok ? t('relink.banner_images') : t('relink.banner_data')}
          </span>
          <button
            className="px-3 py-1.5 rounded bg-amber-600 text-white hover:bg-amber-700"
            onClick={() => setRelinkOpen(true)}
          >
            {t('relink.open')}
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {species.map((sp) => {
          const pct  = sp.n_total_combos > 0 ? Math.round(sp.n_resolved / sp.n_total_combos * 100) : 0
          const pPct = sp.n_total_combos > 0 ? Math.round((sp.n_pending ?? 0) / sp.n_total_combos * 100) : 0
          const done = sp.phase === 'complete'
          const pending = sp.phase === 'pending'

          return (
            <Link
              key={sp.species_safe}
              to={`/gallery/${sp.species_safe}`}
              className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 no-underline text-zinc-900 dark:text-zinc-100 block transition-transform hover:-translate-y-0.5"
            >
              {sp.thumbnails?.length > 0 && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 2, height: 80 }}>
                  {sp.thumbnails.slice(0, 4).map((src, i) => (
                    <img key={i} src={src} alt="" loading="lazy"
                      style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                      onError={(e) => {
                        const img = e.currentTarget
                        const failedSrc = img.src
                        img.onerror = null
                        img.src = imgError
                        img.style.objectFit = 'contain'
                        img.style.background = '#18181b'
                        isPermissionDenied(failedSrc).then((denied) => {
                          if (denied) img.title = t('gallery.image_permission_denied')
                        })
                      }} />
                  ))}
                  {Array.from({ length: Math.max(0, 4 - sp.thumbnails.length) }).map((_, i) => (
                    <div key={`empty-${i}`} style={{ background: '#222' }} />
                  ))}
                </div>
              )}
              <div className="p-4">
                <h5 className="italic font-medium text-base mb-1">{sp.species_name}</h5>
                <p className="text-zinc-500 dark:text-zinc-400 text-xs mb-1">
                  {t('index.confirmed_periods', { n: sp.n_confirmed_combos })}
                </p>
                <p className="text-zinc-500 dark:text-zinc-400 text-xs mb-2">
                  {t('index.reviewed_periods', { resolved: sp.n_resolved, total: sp.n_total_combos, pct })}
                </p>
                <div className="w-full bg-zinc-200 dark:bg-zinc-700 rounded-full mb-3 flex overflow-hidden" style={{ height: 6 }}>
                  <div
                    className={`h-full ${done ? 'bg-emerald-500' : 'bg-blue-500'}`}
                    style={{ width: `${pct}%` }}
                  />
                  <div className="h-full bg-amber-500" style={{ width: `${pPct}%` }} />
                </div>
                <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${
                  done ? 'bg-emerald-600 text-white' : pending ? 'bg-amber-500 text-white' : 'bg-zinc-600 text-zinc-200'
                }`}>
                  {done
                    ? t('index.complete')
                    : pending
                    ? t('index.pending_badge', { n: sp.n_pending })
                    : t('index.round', { n: sp.current_iteration })}
                </span>
                {!pending && (sp.n_pending ?? 0) > 0 && (
                  <span className="ml-2 px-2 py-0.5 text-xs font-medium rounded-full bg-amber-500/20 text-amber-600 dark:text-amber-400">
                    {t('index.pending', { n: sp.n_pending })}
                  </span>
                )}
              </div>
            </Link>
          )
        })}
      </div>

      {relinkOpen && relink && (
        <RelinkDialog
          status={relink}
          onClose={() => setRelinkOpen(false)}
          onDone={(status) => { setRelink(status); setRelinkOpen(false); refresh() }}
        />
      )}

      {species.length === 0 && (
        <div className="mt-3 px-4 py-3 rounded-lg bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-300">
          {t('index.no_species')}
        </div>
      )}
    </div>
  )
}
