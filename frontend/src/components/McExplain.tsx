import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { McResult } from '../types'

/**
 * Explanations for the "Classifiers and reference" form: how the new
 * probability is calculated (before running) and what the combination did
 * (after running), in plain sentences.
 */

const METHODS = ['raw', 'levels', 'levels_calibrated', 'learned', 'learned_context'] as const
const DOCS_URL = 'https://wildintelproject.github.io/wildintel-trapverify/multiclassifier/'
// Species with fewer reference cells than this are not filtered (PipelineConfig.min_ref_cells).
const MIN_REF_CELLS = 100

const pct0 = (x: number) => `${Math.round(100 * x)} %`

export function McHowItWorks() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const block = (k: string) => (
    <p className="mb-2">
      <strong className="text-zinc-800 dark:text-zinc-200">{t(`mc.how_${k}_t`)}</strong> {t(`mc.how_${k}`)}
    </p>
  )
  return (
    <div className="mb-5">
      <button type="button" className="text-sm text-blue-600 dark:text-blue-300 hover:underline"
        aria-expanded={open} onClick={() => setOpen(v => !v)}>
        {open ? '▾' : '▸'} {t('mc.how_title')}
      </button>
      {open && (
        <div className="mt-2 p-4 rounded-lg border border-blue-200 dark:border-blue-900 bg-blue-50/60 dark:bg-blue-950/30 text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed">
          <p className="mb-2">{t('mc.how_intro')}</p>
          {block('presence')}
          {block('species')}
          <p className="mb-3">{t('mc.how_example')}</p>
          <p className="mb-1"><strong className="text-zinc-800 dark:text-zinc-200">{t('mc.how_methods_t')}</strong> {t('mc.how_methods')}</p>
          <ol className="list-decimal ml-5 mb-3 space-y-1">
            {METHODS.map(m => (
              <li key={m}><em>{t(`mc.strategy_${m}`)}</em>: {t(`mc.how_m_${m}`)}</li>
            ))}
          </ol>
          {block('choice')}
          {block('filter')}
          {block('reference')}
          <p className="mb-2">{t('mc.how_noref')}</p>
          <p className="text-xs">
            {t('mc.how_more')}{' '}
            <a href={DOCS_URL} target="_blank" rel="noreferrer" className="text-blue-600 dark:text-blue-300 underline">
              {t('mc.how_more_link')}
            </a>
          </p>
        </div>
      )}
    </div>
  )
}

interface SummaryProps {
  r: McResult
  retention: number          // percentage chosen in the form, e.g. 99
  tieBreak: 'ambiguous' | 'simple' | 'none'
}

export function McResultSummary({ r, retention, tieBreak }: SummaryProps) {
  const { t } = useTranslation()
  const name = (s: string) => t(`mc.strategy_${s}`, { defaultValue: s })
  const chosen = r.strategies.find(s => s.chosen)
  const tied = r.strategies.filter(s => s.tie && !s.chosen && s.eligible)
  const filtered = r.filter_mode !== 'none'
  const lost = r.by_species.reduce((n, s) => n + Math.max(0, s.cells_before - s.cells_after), 0)

  return (
    <div className="mb-3 text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed">
      <h6 className="font-semibold text-zinc-800 dark:text-zinc-200 mb-1">{t('mc.sum_title')}</h6>
      {!r.has_reference || !chosen ? (
        <p className="mb-1">{t('mc.sum_method_noref', { name: name(r.strategy) })}</p>
      ) : (
        <p className="mb-1">
          {t('mc.sum_method', { name: name(chosen.strategy), share: `${retention} %`, rounds: chosen.rounds.toLocaleString() })}
          {tied.length > 0 && <>
            {' '}
            {t('mc.sum_method_tie', { others: tied.map(s => name(s.strategy)).join(', ') })}
            {' '}
            {tieBreak === 'ambiguous'
              ? t('mc.sum_rule_ambiguous', { value: chosen.mean_second_species?.toFixed(3) ?? '' })
              : tieBreak === 'simple' ? t('mc.sum_rule_simple') : t('mc.sum_rule_none')}
          </>}
        </p>
      )}
      <p className="mb-1">
        {filtered
          ? t('mc.sum_events', {
              before: r.events_before.toLocaleString(), after: r.events_after.toLocaleString(),
              pct: pct0(1 - r.events_after / Math.max(1, r.events_before)),
            })
          : t('mc.sum_reorder')}
        {' '}
        {lost === 0 ? t('mc.sum_cells_all') : t('mc.sum_cells_lost', { n: lost.toLocaleString() })}
      </p>
      {filtered && (
        <ul className="list-disc ml-5 mb-1">
          {r.by_species.map(s => (
            <li key={s.species}>
              <em>{s.species}</em>:{' '}
              {s.reference_cells != null && s.reference_cells < MIN_REF_CELLS
                ? t('mc.sum_species_nofilter', { n: s.reference_cells, min: MIN_REF_CELLS })
                : t('mc.sum_species', {
                    threshold: (s.threshold ?? 0).toFixed(3), removed: s.removed.toLocaleString(),
                    before: s.events_before.toLocaleString(), pct: pct0(s.saving),
                  })}
            </li>
          ))}
        </ul>
      )}
      <p className="mb-1">
        {t('mc.sum_two', {
          before: r.photos_high_in_two_before.toLocaleString(), after: r.photos_high_in_two_after.toLocaleString(),
        })}
      </p>
      <p className="text-xs text-zinc-500 dark:text-zinc-400">{t('mc.sum_check')}</p>
    </div>
  )
}
