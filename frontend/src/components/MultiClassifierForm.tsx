import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import type { McClassifier, McJob, McLabels, McResult } from '../types'
import DirectoryPicker from './DirectoryPicker'

/**
 * Combine one or more AI classifiers (e.g. DeepFaune, a YOLO model) and,
 * optionally, a human reference such as Zooniverse into one CamtrapDP whose
 * classificationProbability is recalculated. The backend compares scoring
 * strategies out of sample, picks one, removes the detection events that are
 * not worth reviewing and writes the dataset; the setup then continues with
 * it as with any CamtrapDP.
 */
export interface McParams {
  study_start: string
  study_end: string
  occasion_days: number
  gap_seconds: number
  species: string[]
}

interface Props {
  onConverted: (camtrapDir: string, params: McParams) => void | Promise<void>
  onBack?: () => void
}

const inputClass =
  'flex-1 min-w-0 block rounded-l border border-zinc-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-1 focus:ring-blue-500'
const plainInput =
  'block w-full rounded border border-zinc-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-1 focus:ring-blue-500'
const selectClass =
  'block w-full rounded border border-zinc-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 px-2 py-1.5 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-1 focus:ring-blue-500'
const browseBtn =
  'px-3 py-2 text-sm rounded-r border border-l-0 border-zinc-300 dark:border-zinc-600 bg-zinc-100 dark:bg-zinc-700 text-zinc-700 dark:text-zinc-200 hover:bg-zinc-200 dark:hover:bg-zinc-600 transition-colors whitespace-nowrap'
const labelClass = 'block text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1'
const hintClass = 'mt-1 text-xs text-zinc-500 dark:text-zinc-400'
const boxClass = 'mb-5 p-4 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800/50'
const smallBtn =
  'text-xs px-2 py-1 rounded border border-blue-400 text-blue-600 dark:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-colors disabled:opacity-40'
const stepBtn =
  'px-4 py-2 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-40 disabled:hover:bg-blue-600 flex items-center gap-2'

function SmallSpinner() {
  return <span className="inline-block w-3.5 h-3.5 border-2 border-white/40 border-t-white rounded-full animate-spin" />
}

const isSpecies = (label: string) => /^[A-Z][a-z-]+ [a-z-]+/.test(label)
const pct = (x: number | null | undefined, d = 1) =>
  x == null ? '' : `${(100 * x).toFixed(d)} %`

export default function MultiClassifierForm({ onConverted, onBack }: Props) {
  const { t } = useTranslation()

  // One AI classifier and the (optional) reference to start with; rows left
  // completely empty are ignored, so the reference row can stay blank.
  const [classifiers, setClassifiers] = useState<McClassifier[]>([
    { name: '', path: '', role: 'ai' },
    { name: '', path: '', role: 'reference' },
  ])
  const [picker, setPicker] = useState<number | null>(null)   // classifier row being browsed, -1: output folder
  const [outputDir, setOutputDir] = useState('')
  const [defaultOutputDir, setDefaultOutputDir] = useState('')
  const [outputName, setOutputName] = useState('')
  const [inv, setInv] = useState<McLabels | null>(null)
  const [loadingLabels, setLoadingLabels] = useState(false)
  const [dates, setDates] = useState({ start: '', end: '' })
  const [occasionDays, setOccasionDays] = useState(5)
  const [gapSeconds, setGapSeconds] = useState(60)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  // Group of each selected species: proposed by the backend (genus or family
  // labels, groups used before) unless typed by hand; '' = its own group.
  const [proposedGroup, setProposedGroup] = useState<Record<string, string>>({})
  const [manualGroup, setManualGroup] = useState<Record<string, string>>({})
  // What each label that is not a species means: a group, a target species or
  // '' (not a target). Remembered values come from the backend.
  const [labelMeaning, setLabelMeaning] = useState<Record<string, string>>({})
  const [rememberedMeaning, setRememberedMeaning] = useState<Record<string, string>>({})
  const [filterMode, setFilterMode] = useState<'cells' | 'events' | 'none'>('cells')
  const [retention, setRetention] = useState(99)
  const [tieBreak, setTieBreak] = useState<'ambiguous' | 'simple' | 'none'>('ambiguous')
  const [refVerdict, setRefVerdict] = useState(true)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [job, setJob] = useState<McJob | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [opening, setOpening] = useState(false)
  const pollRef = useRef<number | null>(null)

  useEffect(() => {
    api.mcDefaults().then(d => setDefaultOutputDir(d.output_dir)).catch(() => { /* placeholder only */ })
  }, [])

  const filled = useMemo(() => classifiers.filter(c => c.name.trim() || c.path.trim()), [classifiers])
  const hasReference = filled.some(c => c.role === 'reference' && c.path.trim())
  const nAi = filled.filter(c => c.role === 'ai').length
  // Names are typed by hand (folder names such as "2023" are often the same
  // for several classifiers) and must differ: they key the tables and results.
  const duplicateName = useMemo(() => {
    const seen = new Set<string>()
    for (const c of filled) {
      const k = c.name.trim().toLowerCase()
      if (!k) continue
      if (seen.has(k)) return c.name.trim()
      seen.add(k)
    }
    return null
  }, [filled])
  const ready = filled.every(c => c.name.trim() && c.path.trim()) && nAi > 0 && !duplicateName

  function update(i: number, patch: Partial<McClassifier>) {
    setClassifiers(cs => cs.map((c, j) => (j === i ? { ...c, ...patch } : c)))
    setInv(null)
  }

  async function readLabels() {
    setLoadingLabels(true)
    setError(null)
    try {
      const r = await api.mcLabels({ classifiers: filled })
      setInv(r)
      setDates(d => ({ start: d.start || r.date_min || '', end: d.end || r.date_max || '' }))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingLabels(false)
    }
  }

  // photos per label and classifier
  const table = useMemo(() => {
    if (!inv) return []
    const rows = new Map<string, Record<string, number>>()
    for (const r of inv.labels) {
      const row = rows.get(r.label) ?? {}
      row[r.classifier] = r.photos
      rows.set(r.label, row)
    }
    return [...rows.entries()]
      .map(([label, n]) => ({ label, n, total: Object.values(n).reduce((a, b) => a + b, 0) }))
      .sort((a, b) => b.total - a.total)
  }, [inv])
  const speciesRows = table.filter(r => isSpecies(r.label))
  const otherRows = table.filter(r => !isSpecies(r.label))
  const families = inv?.families ?? []

  // propose groups whenever the selection changes (hand-typed groups stay)
  useEffect(() => {
    if (!inv || selected.size === 0) { setProposedGroup({}); return }
    let alive = true
    api.mcGroups([...selected], table.map(r => r.label))
      .then(r => {
        if (!alive) return
        const g: Record<string, string> = {}
        for (const [name, sps] of Object.entries(r.groups)) for (const sp of sps) g[sp] = name
        setProposedGroup(g)
        setRememberedMeaning(r.aliases ?? {})
      })
      .catch(() => { /* keep each species on its own */ })
    return () => { alive = false }
  }, [inv, selected, table])

  const groupOf = (sp: string) => {
    const m = manualGroup[sp]
    return (m !== undefined ? m.trim() : proposedGroup[sp]) || sp
  }

  const groups = useMemo(() => {
    const g: Record<string, string[]> = {}
    for (const sp of selected) {
      const m = manualGroup[sp]
      const name = (m !== undefined ? m.trim() : proposedGroup[sp]) || sp
      ;(g[name] ??= []).push(sp)
    }
    // a group with a single species is named after it
    const out: Record<string, string[]> = {}
    for (const [name, sps] of Object.entries(g)) out[sps.length === 1 ? sps[0] : name] = sps
    return out
  }, [selected, manualGroup, proposedGroup])
  const multiGroups = Object.keys(groups).filter(g => groups[g].length > 1)
  const groupSuggestions = [...new Set([...families, ...otherRows.map(r => r.label).filter(l => /^[A-Z][a-z]+$/.test(l)), ...multiGroups])]

  // Other labels: a group name is read as that group automatically; the rest
  // mean what was chosen (or remembered), or nothing.
  const meaningOf = (label: string): { value: string; automatic: boolean } => {
    if (groups[label] && groups[label].length > 1) return { value: label, automatic: true }
    const chosen = labelMeaning[label] ?? rememberedMeaning[label] ?? ''
    const valid = chosen === '' || multiGroups.includes(chosen) || selected.has(chosen)
    return { value: valid ? chosen : '', automatic: false }
  }
  const aliases = useMemo(() => {
    const out: Record<string, string> = {}
    for (const r of otherRows) {
      const m = meaningOf(r.label)
      if (m.automatic) continue
      // what the user chose now, or a remembered meaning that still applies
      if (labelMeaning[r.label] !== undefined || (rememberedMeaning[r.label] && m.value)) out[r.label] = m.value
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [otherRows, labelMeaning, rememberedMeaning, groups])

  function toggle(sp: string) {
    setSelected(s => {
      const n = new Set(s)
      if (n.has(sp)) n.delete(sp); else n.add(sp)
      return n
    })
  }

  async function run() {
    setError(null)
    setJob(null)
    try {
      const { job_id } = await api.mcRun({
        classifiers: filled,
        groups,
        date_start: dates.start,
        date_end: dates.end,
        occasion_days: occasionDays,
        gap_seconds: gapSeconds,
        filter_mode: hasReference ? filterMode : 'none',
        retention: retention / 100,
        tie_break: tieBreak,
        use_reference_in_output: refVerdict,
        aliases,
        output_dir: outputDir.trim() || null,
        output_name: outputName.trim() || null,
      })
      setJobId(job_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    if (!jobId) return
    const tick = async () => {
      try {
        const j = await api.mcJob(jobId)
        setJob(j)
        if (j.status !== 'running') { setJobId(null); return }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        setJobId(null)
        return
      }
      pollRef.current = window.setTimeout(tick, 1500)
    }
    tick()
    return () => { if (pollRef.current) window.clearTimeout(pollRef.current) }
  }, [jobId])

  const running = jobId !== null
  const result: McResult | undefined = job?.status === 'done' ? job.result : undefined
  const canRun = ready && !!inv && selected.size > 0 && !!dates.start && !!dates.end && dates.start <= dates.end && !running
  // The first thing still missing, said next to the disabled Combine button.
  const missing = running || result ? null
    : duplicateName ? null
    : !ready ? (classifiers.some(c => c.role === 'ai') ? 'mc.missing_rows' : 'mc.missing_ai')
    : !inv ? 'mc.missing_labels'
    : selected.size === 0 ? 'mc.missing_species'
    : !(dates.start && dates.end && dates.start <= dates.end) ? 'mc.missing_period'
    : null

  // Opening the new dataset reads it again (species, dates, datapackage
  // validation), which takes a while on large archives: show it.
  async function chooseResult(r: McResult) {
    setOpening(true)
    try {
      await onConverted(r.camtrap_dir, {
        study_start: r.study_start,
        study_end: r.study_end,
        occasion_days: r.occasion_days,
        gap_seconds: r.gap_seconds,
        species: [...selected],
      })
    } finally {
      setOpening(false)
    }
  }

  return (
    <div>
      <h4 className="text-lg font-semibold mb-1">{t('mc.title')}</h4>
      <p className="text-zinc-500 dark:text-zinc-400 mb-6 text-sm">{t('mc.desc')}</p>

      {/* ── 1. Classifiers ── */}
      <div className={boxClass}>
        <h5 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300 mb-1">{t('mc.classifiers_title')}</h5>
        <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-3">{t('mc.classifiers_desc')}</p>
        {classifiers.map((c, i) => (
          <div key={i} className="grid grid-cols-1 sm:grid-cols-[8rem_1fr_9rem_auto] gap-2 mb-2 items-center">
            <input className={plainInput} placeholder={t('mc.name_placeholder')} aria-label={t('mc.name')}
              value={c.name} onChange={e => update(i, { name: e.target.value })} />
            <div className="flex">
              <input className={inputClass} placeholder={t('mc.folder_placeholder')} aria-label={t('mc.folder')}
                value={c.path} onChange={e => update(i, { path: e.target.value })} />
              <button type="button" className={browseBtn} onClick={() => setPicker(i)}>{t('setup.browse')}</button>
            </div>
            <select className={selectClass} aria-label={t('mc.role')} value={c.role}
              onChange={e => update(i, { role: e.target.value as McClassifier['role'] })}>
              <option value="ai">{t('mc.role_ai')}</option>
              <option value="reference">{t('mc.role_reference')}</option>
            </select>
            <button type="button" className="text-zinc-400 hover:text-red-500 text-sm px-2 disabled:opacity-30"
              disabled={classifiers.length <= 1} title={t('mc.remove')}
              onClick={() => { setClassifiers(cs => cs.filter((_, j) => j !== i)); setInv(null) }}>✕</button>
          </div>
        ))}
        {duplicateName && (
          <p role="alert" className="text-xs text-red-600 dark:text-red-400 mb-1">
            {t('mc.name_duplicate', { name: duplicateName })}
          </p>
        )}
        <p className={hintClass}>{t('mc.name_hint')}</p>
        <div className="flex gap-2 mt-2">
          <button type="button" className={smallBtn}
            onClick={() => setClassifiers(cs => [...cs, { name: '', path: '', role: 'ai' }])}>
            + {t('mc.add')}
          </button>
        </div>
        <p className={hintClass}>{t('mc.reference_hint')}</p>
        {ready && nAi === 1 && !hasReference && (
          <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">{t('mc.single_ai_no_reference')}</p>
        )}
        <div className="mt-4 pt-3 border-t border-zinc-200 dark:border-zinc-700 flex flex-wrap items-center gap-3">
          <button type="button" className={stepBtn} disabled={!ready || loadingLabels} onClick={readLabels}>
            {loadingLabels && <SmallSpinner />}
            {loadingLabels ? t('mc.reading') : inv ? t('mc.read_labels_again') : t('mc.read_labels')}
          </button>
          <span className="text-xs text-zinc-500 dark:text-zinc-400 flex-1 min-w-[14rem]">
            {inv ? t('mc.labels_read', { n: inv.n_photos.toLocaleString() }) : t('mc.read_labels_required')}
          </span>
        </div>
        {inv && inv.remote_paths > 0.5 && (
          <p className="mt-3 text-xs text-blue-700 dark:text-blue-300">
            {t('mc.remote_paths_info', { pct: Math.round(100 * inv.remote_paths) })}
          </p>
        )}
      </div>

      {inv && (
        <>
          {/* ── 2. Period and occasions ── */}
          <div className={boxClass}>
            <h5 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300 mb-3">{t('mc.period_title')}</h5>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div>
                <label htmlFor="mc-start" className={labelClass}>{t('setup.label_start')}</label>
                <input id="mc-start" type="date" className={plainInput} value={dates.start}
                  min={inv.date_min ?? undefined} max={inv.date_max ?? undefined}
                  onChange={e => setDates(d => ({ ...d, start: e.target.value }))} />
              </div>
              <div>
                <label htmlFor="mc-end" className={labelClass}>{t('setup.label_end')}</label>
                <input id="mc-end" type="date" className={plainInput} value={dates.end}
                  min={inv.date_min ?? undefined} max={inv.date_max ?? undefined}
                  onChange={e => setDates(d => ({ ...d, end: e.target.value }))} />
              </div>
              <div>
                <label htmlFor="mc-occ" className={labelClass}>{t('mc.occasion_days')}</label>
                <input id="mc-occ" type="number" min={1} className={plainInput} value={occasionDays}
                  onChange={e => setOccasionDays(Math.max(1, Number(e.target.value) || 1))} />
              </div>
              <div>
                <label htmlFor="mc-gap" className={labelClass}>{t('mc.gap')}</label>
                <input id="mc-gap" type="number" min={1} className={plainInput} value={gapSeconds}
                  onChange={e => setGapSeconds(Math.max(1, Number(e.target.value) || 1))} />
              </div>
            </div>
            <p className={hintClass}>{t('mc.period_hint', { n: inv.n_photos.toLocaleString() })}</p>
          </div>

          {/* ── 3. Target species and groups ── */}
          <div className={boxClass}>
            <h5 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300 mb-1">{t('mc.species_title')}</h5>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-3">{t('mc.species_desc')}</p>
            <div className="max-h-72 overflow-y-auto rounded border border-zinc-200 dark:border-zinc-700">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="bg-zinc-100 dark:bg-zinc-700 text-left">
                    <th className="px-2 py-2 w-8"></th>
                    <th className="px-2 py-2 font-medium text-zinc-600 dark:text-zinc-300">{t('mc.label')}</th>
                    {filled.map(c => (
                      <th key={c.name} className="px-2 py-2 font-medium text-zinc-600 dark:text-zinc-300 text-right">{c.name}</th>
                    ))}
                    <th className="px-2 py-2 font-medium text-zinc-600 dark:text-zinc-300">{t('mc.group')}</th>
                  </tr>
                </thead>
                <tbody>
                  {speciesRows.map(r => {
                    const on = selected.has(r.label)
                    const g = groupOf(r.label)
                    return (
                      <tr key={r.label} className="border-t border-zinc-200 dark:border-zinc-700">
                        <td className="px-2 py-1.5">
                          <input type="checkbox" checked={on} onChange={() => toggle(r.label)} aria-label={r.label} />
                        </td>
                        <td className="px-2 py-1.5 italic text-zinc-800 dark:text-zinc-200">{r.label}</td>
                        {filled.map(c => (
                          <td key={c.name} className="px-2 py-1.5 text-right tabular-nums text-zinc-600 dark:text-zinc-400">
                            {(r.n[c.name] ?? 0).toLocaleString()}
                          </td>
                        ))}
                        <td className="px-2 py-1">
                          {on && (
                            <input className={selectClass} list="mc-group-names" aria-label={t('mc.group_of', { species: r.label })}
                              placeholder={t('mc.own_group')}
                              value={manualGroup[r.label] !== undefined ? manualGroup[r.label] : (g === r.label ? '' : g)}
                              onChange={e => setManualGroup(m => ({ ...m, [r.label]: e.target.value }))} />
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <datalist id="mc-group-names">
              {groupSuggestions.map(o => <option key={o} value={o} />)}
            </datalist>
            {Object.keys(groups).length > 0 && (
              <p className={hintClass}>
                {t('mc.groups_summary')}{' '}
                {Object.entries(groups).map(([g, sps]) => (sps.length > 1 ? `${g} (${sps.join(', ')})` : g)).join('; ')}
              </p>
            )}
            <p className={hintClass}>{t('mc.groups_how')}</p>

            {otherRows.length > 0 && (
              <div className="mt-4">
                <h6 className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1">{t('mc.other_labels_title')}</h6>
                <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-2">{t('mc.other_labels_desc')}</p>
                <div className="max-h-56 overflow-y-auto rounded border border-zinc-200 dark:border-zinc-700">
                  <table className="w-full text-sm border-collapse">
                    <thead>
                      <tr className="bg-zinc-100 dark:bg-zinc-700 text-left">
                        <th className="px-2 py-2 font-medium text-zinc-600 dark:text-zinc-300">{t('mc.label')}</th>
                        {filled.map(c => (
                          <th key={c.name} className="px-2 py-2 font-medium text-zinc-600 dark:text-zinc-300 text-right">{c.name}</th>
                        ))}
                        <th className="px-2 py-2 font-medium text-zinc-600 dark:text-zinc-300">{t('mc.label_means')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {otherRows.map(r => {
                        const m = meaningOf(r.label)
                        return (
                          <tr key={r.label} className="border-t border-zinc-200 dark:border-zinc-700">
                            <td className="px-2 py-1.5 text-zinc-800 dark:text-zinc-200">{r.label}</td>
                            {filled.map(c => (
                              <td key={c.name} className="px-2 py-1.5 text-right tabular-nums text-zinc-600 dark:text-zinc-400">
                                {(r.n[c.name] ?? 0).toLocaleString()}
                              </td>
                            ))}
                            <td className="px-2 py-1">
                              {m.automatic ? (
                                <span className="text-xs text-emerald-700 dark:text-emerald-400">
                                  {t('mc.label_group_auto', { species: groups[r.label].join(', ') })}
                                </span>
                              ) : (
                                <select className={selectClass} aria-label={t('mc.label_means_of', { label: r.label })}
                                  value={m.value} disabled={selected.size === 0}
                                  onChange={e => setLabelMeaning(lm => ({ ...lm, [r.label]: e.target.value }))}>
                                  <option value="">{t('mc.label_not_target')}</option>
                                  {multiGroups.map(g => (
                                    <option key={`g:${g}`} value={g}>{t('mc.label_is_group', { group: g })}</option>
                                  ))}
                                  {[...selected].map(sp => (
                                    <option key={`s:${sp}`} value={sp}>{sp}</option>
                                  ))}
                                </select>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>

          {/* ── 4. Advanced options ── */}
          <div className="mb-5">
            <button type="button" className="text-sm text-blue-600 dark:text-blue-300 hover:underline"
              onClick={() => setShowAdvanced(v => !v)}>
              {showAdvanced ? '▾' : '▸'} {t('mc.advanced')}
            </button>
            {showAdvanced && (
              <div className={`${boxClass} mt-2`}>
                {!hasReference && <p className="text-xs text-amber-600 dark:text-amber-400 mb-3">{t('mc.no_reference')}</p>}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="mc-filter" className={labelClass}>{t('mc.filter')}</label>
                    <select id="mc-filter" className={selectClass} value={filterMode} disabled={!hasReference}
                      onChange={e => setFilterMode(e.target.value as typeof filterMode)}>
                      <option value="cells">{t('mc.filter_cells')}</option>
                      <option value="events">{t('mc.filter_events')}</option>
                      <option value="none">{t('mc.filter_none')}</option>
                    </select>
                    <p className={hintClass}>{t(`mc.filter_${filterMode}_hint`)}</p>
                  </div>
                  <div>
                    <label htmlFor="mc-retention" className={labelClass}>{t('mc.retention')}</label>
                    <input id="mc-retention" type="number" min={50} max={100} step={0.5} className={plainInput} value={retention}
                      disabled={!hasReference || filterMode === 'none'}
                      onChange={e => setRetention(Math.min(100, Math.max(50, Number(e.target.value) || 99)))} />
                  </div>
                  <div>
                    <label htmlFor="mc-tie" className={labelClass}>{t('mc.tie_break')}</label>
                    <select id="mc-tie" className={selectClass} value={tieBreak} disabled={!hasReference}
                      onChange={e => setTieBreak(e.target.value as typeof tieBreak)}>
                      <option value="ambiguous">{t('mc.tie_ambiguous')}</option>
                      <option value="simple">{t('mc.tie_simple')}</option>
                      <option value="none">{t('mc.tie_none')}</option>
                    </select>
                  </div>
                  <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300 mt-6">
                    <input type="checkbox" checked={refVerdict} disabled={!hasReference}
                      onChange={e => setRefVerdict(e.target.checked)} />
                    <span>{t('mc.use_reference')}</span>
                  </label>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {inv && (
        <div className={boxClass}>
          <h5 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300 mb-3">{t('mc.output_title')}</h5>
          <div className="grid grid-cols-1 sm:grid-cols-[1fr_16rem] gap-3">
            <div>
              <label htmlFor="mc-out-dir" className={labelClass}>{t('mc.output_dir')}</label>
              <div className="flex">
                <input id="mc-out-dir" className={inputClass} placeholder={defaultOutputDir}
                  value={outputDir} onChange={e => setOutputDir(e.target.value)} />
                <button type="button" className={browseBtn} onClick={() => setPicker(-1)}>{t('setup.browse')}</button>
              </div>
            </div>
            <div>
              <label htmlFor="mc-out-name" className={labelClass}>{t('mc.output_name')}</label>
              <input id="mc-out-name" className={plainInput} placeholder="multiclassifier_<date>"
                value={outputName} onChange={e => setOutputName(e.target.value)} />
            </div>
          </div>
          <p className={hintClass}>{t('mc.output_hint', { dir: outputDir.trim() || defaultOutputDir })}</p>
        </div>
      )}

      {/* ── Progress and result ── */}
      {job && (
        <div className={boxClass}>
          {job.status === 'running' && (
            <p className="text-sm text-zinc-700 dark:text-zinc-300 mb-2">{t('mc.running')}</p>
          )}
          {job.log.length > 0 && job.status !== 'done' && (
            <pre className="text-xs max-h-40 overflow-y-auto bg-white dark:bg-zinc-900 p-2 rounded border border-zinc-200 dark:border-zinc-700 whitespace-pre-wrap">
              {job.log.join('\n')}
            </pre>
          )}
          {job.status === 'error' && (
            <p className="text-sm text-red-600 dark:text-red-400 mt-2">
              {job.error}
              {job.unmapped?.length ? ` (${job.unmapped.join(', ')})` : ''}
            </p>
          )}
          {result && <ResultView r={result} />}
        </div>
      )}

      {/* ── Buttons ── */}
      <div className="flex items-center justify-between gap-2">
        {onBack && (
          <button type="button"
            className="px-4 py-2 text-sm border border-zinc-300 dark:border-zinc-600 text-zinc-700 dark:text-zinc-300 rounded hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors"
            onClick={onBack}>
            {t('setup.back')}
          </button>
        )}
        <div className="flex flex-col items-end gap-2">
          {result ? (
            <button type="button" onClick={() => chooseResult(result)} disabled={opening}
              className="px-5 py-2 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 flex items-center gap-2 disabled:opacity-60">
              {opening && <SmallSpinner />}
              {t(opening ? 'mc.opening' : 'mc.use_dataset')}
            </button>
          ) : (
            <button type="button" disabled={!canRun} onClick={run}
              className={[
                'px-5 py-2 text-sm rounded bg-emerald-600 text-white flex items-center gap-2 transition-opacity',
                canRun ? 'hover:bg-emerald-700 cursor-pointer' : 'opacity-40 cursor-not-allowed',
              ].join(' ')}>
              {running && <SmallSpinner />}
              {t(running ? 'mc.running_btn' : 'mc.run')}
            </button>
          )}
          {missing && !error && <p className="text-xs text-zinc-500 dark:text-zinc-400 text-right">{t(missing)}</p>}
          {error && <p className="text-sm text-red-600 dark:text-red-400 text-right">{error}</p>}
        </div>
      </div>

      {picker !== null && (
        <DirectoryPicker
          title={t(picker < 0 ? 'mc.output_picker_title' : 'mc.folder_picker_title')}
          initialPath={(picker < 0 ? outputDir || defaultOutputDir : classifiers[picker]?.path) || undefined}
          onSelect={path => {
            if (picker < 0) setOutputDir(path)
            else update(picker, { path })
            setPicker(null)
          }}
          onClose={() => setPicker(null)}
        />
      )}
    </div>
  )
}

function ResultView({ r }: { r: McResult }) {
  const { t } = useTranslation()
  const name = (s: string) => t(`mc.strategy_${s}`, { defaultValue: s })
  return (
    <div className="text-sm text-zinc-700 dark:text-zinc-300">
      <p className="mb-2">
        <strong>{t('mc.result_strategy')}</strong> {name(r.strategy)}
        {' · '}
        {t('mc.result_events', {
          before: r.events_before.toLocaleString(), after: r.events_after.toLocaleString(),
          pct: pct(1 - r.events_after / Math.max(1, r.events_before), 0),
        })}
        {' · '}
        {t('mc.result_two', {
          before: r.photos_high_in_two_before.toLocaleString(), after: r.photos_high_in_two_after.toLocaleString(),
        })}
      </p>
      {r.strategies.length > 0 && (
        <table className="w-full text-xs border-collapse mb-3">
          <thead>
            <tr className="bg-zinc-100 dark:bg-zinc-700 text-left">
              <th className="px-2 py-1">{t('mc.col_strategy')}</th>
              <th className="px-2 py-1 text-right">{t('mc.col_rounds')}</th>
              <th className="px-2 py-1 text-right">{t('mc.col_cells')}</th>
              <th className="px-2 py-1 text-right">{t('mc.col_diff')}</th>
              <th className="px-2 py-1 text-right">{t('mc.col_ambiguous')}</th>
              <th className="px-2 py-1"></th>
            </tr>
          </thead>
          <tbody>
            {r.strategies.map(s => (
              <tr key={s.strategy} className={['border-t border-zinc-200 dark:border-zinc-700', s.chosen ? 'font-semibold' : ''].join(' ')}>
                <td className="px-2 py-1">{name(s.strategy)}</td>
                <td className="px-2 py-1 text-right tabular-nums">{s.rounds.toLocaleString()}</td>
                <td className="px-2 py-1 text-right tabular-nums">{pct(s.cell_retention)}</td>
                <td className="px-2 py-1 text-right tabular-nums">
                  {s.diff_ci_low == null || s.best ? '' : `${Math.round(s.diff_ci_low)} … ${Math.round(s.diff_ci_high ?? 0)}`}
                </td>
                <td className="px-2 py-1 text-right tabular-nums">{s.mean_second_species == null ? '' : s.mean_second_species.toFixed(3)}</td>
                <td className="px-2 py-1">
                  {s.chosen ? t('mc.chosen') : !s.eligible ? t('mc.not_eligible') : s.tie ? t('mc.tie') : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <table className="w-full text-xs border-collapse mb-2">
        <thead>
          <tr className="bg-zinc-100 dark:bg-zinc-700 text-left">
            <th className="px-2 py-1">{t('mc.col_species')}</th>
            <th className="px-2 py-1 text-right">{t('mc.col_events')}</th>
            <th className="px-2 py-1 text-right">{t('mc.col_saving')}</th>
            <th className="px-2 py-1 text-right">{t('mc.col_cells_kept')}</th>
            <th className="px-2 py-1 text-right">{t('mc.col_threshold')}</th>
          </tr>
        </thead>
        <tbody>
          {r.by_species.map(s => (
            <tr key={s.species} className="border-t border-zinc-200 dark:border-zinc-700">
              <td className="px-2 py-1 italic">{s.species}</td>
              <td className="px-2 py-1 text-right tabular-nums">{s.events_before.toLocaleString()} → {s.events_after.toLocaleString()}</td>
              <td className="px-2 py-1 text-right tabular-nums">{pct(s.saving, 0)}</td>
              <td className="px-2 py-1 text-right tabular-nums">{s.cells_after} / {s.cells_before}</td>
              <td className="px-2 py-1 text-right tabular-nums">
                {s.threshold ? s.threshold.toFixed(3) : t('mc.no_threshold')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {r.warnings.map((w, i) => <p key={i} className="text-xs text-amber-600 dark:text-amber-400">{w}</p>)}
      <p className={hintClass}>{t('mc.result_folder', { dir: r.camtrap_dir })}</p>
    </div>
  )
}
