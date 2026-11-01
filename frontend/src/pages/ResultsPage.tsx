import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import type { OccupancyRow } from '../types'
import { formatP, formatPsi } from '../utils/occupancy'

interface SpeciesRow {
  species: string
  confirmed: number
  rejected: number
  pending?: number
  unverified: number
}

interface Results {
  session_dir: string
  output_dir: string
  total: number
  confirmed: number
  rejected: number
  unverified: number
  pending: number
  by_species: SpeciesRow[]
  seq_total: number
  seq_confirmed: number
  seq_rejected: number
  seq_unverified: number
  by_species_seqs: SpeciesRow[]
  datapackage_generated: boolean
  datapackage_fabricated_fields: string[]
}

export default function ResultsPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [results, setResults] = useState<Results | null>(null)
  const [occupancy, setOccupancy] = useState<OccupancyRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [opening, setOpening] = useState(false)
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    api.getResults().then((r) => setResults(r as Results)).catch((e: Error) => setError(e.message))
    // El ajuste de ocupación es un extra opcional: si no hay historiales de
    // detección suficientes, se omite la sección en vez de romper la página.
    api.getOccupancy().then(setOccupancy).catch(() => setOccupancy([]))
  }, [])

  async function handleOpenFolder() {
    setOpening(true)
    try { await api.openFolder() } catch { /* ignora si no hay explorador */ }
    finally { setOpening(false) }
  }

  async function handleDownload() {
    setDownloading(true)
    try {
      const res = await fetch('/api/results/download')
      if (!res.ok) throw new Error(await res.text())
      const blob = await res.blob()
      const cd = res.headers.get('content-disposition') ?? ''
      const match = cd.match(/filename="?([^"]+)"?/)
      const filename = match ? match[1] : 'results.zip'
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error('Download failed', e)
    } finally {
      setDownloading(false)
    }
  }

  function handleCopy(text: string) {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  if (error) return (
    <div className="max-w-4xl mx-auto px-4 py-4">
      <div className="px-4 py-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300">
        {error}
      </div>
    </div>
  )

  if (!results) return (
    <div className="flex justify-center items-center" style={{ minHeight: 300 }}>
      <div className="w-8 h-8 border-2 border-zinc-600 border-t-blue-500 rounded-full animate-spin" />
    </div>
  )

  function StatsCards({ confirmed, rejected, unverified, total }: {
    confirmed: number; rejected: number; unverified: number; total: number
  }) {
    const pC = total > 0 ? Math.round(confirmed  / total * 100) : 0
    const pR = total > 0 ? Math.round(rejected   / total * 100) : 0
    const pU = total > 0 ? Math.round(unverified / total * 100) : 0
    return (
      <>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
          <div className="rounded-lg border border-emerald-500 bg-white dark:bg-zinc-900 text-center p-4">
            <div className="text-4xl font-bold text-emerald-500 mb-1">{confirmed}</div>
            <div className="text-zinc-500 dark:text-zinc-400 text-sm">{t('results.confirmed')}</div>
            <div className="text-emerald-500 text-xs">{t('results.pct_total', { pct: pC })}</div>
          </div>
          <div className="rounded-lg border border-red-500 bg-white dark:bg-zinc-900 text-center p-4">
            <div className="text-4xl font-bold text-red-500 mb-1">{rejected}</div>
            <div className="text-zinc-500 dark:text-zinc-400 text-sm">{t('results.rejected')}</div>
            <div className="text-red-500 text-xs">{t('results.pct_total', { pct: pR })}</div>
          </div>
          <div className="rounded-lg border border-zinc-400 bg-white dark:bg-zinc-900 text-center p-4">
            <div className="text-4xl font-bold text-zinc-400 mb-1">{unverified}</div>
            <div className="text-zinc-500 dark:text-zinc-400 text-sm">{t('results.unverified')}</div>
            <div className="text-zinc-400 text-xs">{t('results.pct_total', { pct: pU })}</div>
          </div>
        </div>
        <div className="flex w-full rounded-full overflow-hidden mb-4" style={{ height: 12 }}>
          <div className="bg-emerald-500 transition-all" style={{ width: `${pC}%` }} />
          <div className="bg-red-500 transition-all"     style={{ width: `${pR}%` }} />
          <div className="bg-zinc-400 transition-all"    style={{ width: `${pU}%` }} />
        </div>
      </>
    )
  }

  function OccupancyTable({ rows }: { rows: OccupancyRow[] }) {
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-200 dark:border-zinc-700">
              <th className="text-left py-2 px-3 font-semibold text-zinc-700 dark:text-zinc-300">{t('results.col_species')}</th>
              <th className="text-right py-2 px-3 font-semibold text-zinc-500">{t('results.col_psi_naive')}</th>
              <th className="text-right py-2 px-3 font-semibold text-emerald-600 dark:text-emerald-400">{t('results.col_psi_verified')}</th>
              <th className="text-right py-2 px-3 font-semibold text-zinc-500">{t('results.col_p_naive')}</th>
              <th className="text-right py-2 px-3 font-semibold text-emerald-600 dark:text-emerald-400">{t('results.col_p_verified')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.species} className="border-b border-zinc-100 dark:border-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-800 transition-colors">
                <td className="py-2 px-3 font-mono text-zinc-900 dark:text-zinc-100">{row.species}</td>
                <td className="py-2 px-3 text-right text-zinc-500">
                  {row.degenerate_naive ? t('results.degenerate') : formatPsi(row.psi_naive, row.psi_naive_lo, row.psi_naive_hi, false)}
                </td>
                <td className="py-2 px-3 text-right text-emerald-600 dark:text-emerald-400">
                  {row.degenerate_verified ? t('results.degenerate') : formatPsi(row.psi_verified, row.psi_verified_lo, row.psi_verified_hi, false)}
                </td>
                <td className="py-2 px-3 text-right text-zinc-500">{formatP(row.p_naive)}</td>
                <td className="py-2 px-3 text-right text-emerald-600 dark:text-emerald-400">{formatP(row.p_verified)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  function BreakdownTable({ rows }: { rows: SpeciesRow[] }) {
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-200 dark:border-zinc-700">
              <th className="text-left py-2 px-3 font-semibold text-zinc-700 dark:text-zinc-300">{t('results.col_species')}</th>
              <th className="text-right py-2 px-3 font-semibold text-emerald-600 dark:text-emerald-400">{t('results.col_confirmed')}</th>
              <th className="text-right py-2 px-3 font-semibold text-red-600 dark:text-red-400">{t('results.col_rejected')}</th>
              <th className="text-right py-2 px-3 font-semibold text-zinc-500">{t('results.col_unverified')}</th>
              <th className="text-right py-2 px-3 font-semibold text-zinc-700 dark:text-zinc-300">{t('results.col_total')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const tot = row.confirmed + row.rejected + row.unverified
              return (
                <tr key={row.species} className="border-b border-zinc-100 dark:border-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-800 transition-colors">
                  <td className="py-2 px-3 font-mono text-zinc-900 dark:text-zinc-100">{row.species}</td>
                  <td className="py-2 px-3 text-right text-emerald-600 dark:text-emerald-400">{row.confirmed}</td>
                  <td className="py-2 px-3 text-right text-red-600 dark:text-red-400">{row.rejected}</td>
                  <td className="py-2 px-3 text-right text-zinc-500">{row.unverified}</td>
                  <td className="py-2 px-3 text-right text-zinc-700 dark:text-zinc-300">{tot}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    )
  }

  const cardClass = 'rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900'
  const cardHeader = 'px-4 py-3 border-b border-zinc-200 dark:border-zinc-800 font-medium text-sm text-zinc-700 dark:text-zinc-300'

  return (
    <div className="max-w-4xl mx-auto px-4 py-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-2xl font-semibold mb-0">{t('results.title')}</h2>
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
      <p className="text-zinc-500 dark:text-zinc-400 mb-4 text-sm"
        dangerouslySetInnerHTML={{ __html: t('results.subtitle') }} />

      {results.datapackage_generated && (
        <div className="px-4 py-2 rounded bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300 text-sm mb-4">
          <p className="font-medium">{t('results.datapackage_generated')}</p>
          {results.datapackage_fabricated_fields.length > 0 && (
            <ul className="list-disc list-inside mt-1">
              {results.datapackage_fabricated_fields.map((f) => <li key={f}>{f}</li>)}
            </ul>
          )}
        </div>
      )}

      {results.pending > 0 && (
        <div className="px-4 py-3 rounded bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-300 text-sm mb-4">
          <p className="font-medium mb-1">{t('results.pending_title', { n: results.pending })}</p>
          <p dangerouslySetInnerHTML={{ __html: t('results.pending_hint') }} />
          {results.by_species.filter((r) => (r.pending ?? 0) > 0).length > 0 && (
            <ul className="list-disc list-inside mt-1">
              {results.by_species.filter((r) => (r.pending ?? 0) > 0).map((r) => (
                <li key={r.species}><span className="italic">{r.species}</span>: {r.pending}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Output directory */}
      <div className={`${cardClass} mb-4`}>
        <div className={cardHeader}>{t('results.output_header')}</div>
        <div className="p-4">
          <div className="flex">
            <input
              className="flex-1 px-3 py-2 text-sm rounded-l border border-zinc-300 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 font-mono focus:outline-none"
              readOnly
              value={results.output_dir}
            />
            <button
              className="px-3 py-2 text-sm border-t border-b border-zinc-300 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors"
              onClick={() => handleCopy(results.output_dir)}
            >
              {copied ? t('results.copied') : t('results.copy')}
            </button>
            <button
              className="px-3 py-2 text-sm border border-l-0 border-zinc-300 dark:border-zinc-700 text-blue-600 dark:text-blue-400 hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors rounded-r disabled:opacity-50 flex items-center"
              onClick={handleOpenFolder}
              disabled={opening}
            >
              {opening
                ? <div className="w-4 h-4 border border-zinc-400 border-t-zinc-700 dark:border-t-zinc-300 rounded-full animate-spin" />
                : t('results.open_folder')}
            </button>
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1"
            dangerouslySetInnerHTML={{ __html: t('results.output_hint') }} />
          <div className="flex justify-center mt-4">
            <button
              className="px-5 py-2 text-sm rounded transition-colors bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
              onClick={handleDownload}
              disabled={downloading}
            >
              {downloading
                ? <div className="w-4 h-4 border border-blue-300 border-t-white rounded-full animate-spin" />
                : <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                    <path d="M8 2v8m0 0L5 7m3 3l3-3M2 12h12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
              }
              {t('results.download')}
            </button>
          </div>
        </div>
      </div>

      {/* Periods */}
      <h5 className="text-base font-semibold mb-3">{t('results.periods_title')}</h5>
      <StatsCards
        confirmed={results.confirmed}
        rejected={results.rejected}
        unverified={results.unverified}
        total={results.total}
      />
      {results.by_species.length > 0 && (
        <div className={`${cardClass} mb-10`}>
          <div className={cardHeader}>{t('results.by_species')}</div>
          <BreakdownTable rows={results.by_species} />
        </div>
      )}

      {/* Detection events */}
      <h5 className="text-base font-semibold mb-3">{t('results.seq_title')}</h5>
      <StatsCards
        confirmed={results.seq_confirmed}
        rejected={results.seq_rejected}
        unverified={results.seq_unverified}
        total={results.seq_total}
      />
      {results.by_species_seqs.length > 0 && (
        <div className={`${cardClass} mb-10`}>
          <div className={cardHeader}>{t('results.by_species')}</div>
          <BreakdownTable rows={results.by_species_seqs} />
        </div>
      )}

      {/* Occupancy */}
      {occupancy.length > 0 && (
        <>
          <h5 className="text-base font-semibold mb-3">{t('results.occupancy_title')}</h5>
          <p className="text-zinc-500 dark:text-zinc-400 mb-4 text-sm">{t('results.occupancy_hint')}</p>
          <div className={`${cardClass} mb-4`}>
            <div className={cardHeader}>{t('results.by_species')}</div>
            <OccupancyTable rows={occupancy} />
          </div>
        </>
      )}
    </div>
  )
}
