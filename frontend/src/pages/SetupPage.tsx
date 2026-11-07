import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import DirectoryPicker from '../components/DirectoryPicker'
import CsvImportForm from '../components/CsvImportForm'
import SessionNameEditor from '../components/SessionNameEditor'
import MultiClassifierForm, { type McParams } from '../components/MultiClassifierForm'
import RelinkDialog from '../components/RelinkDialog'
import type { RecentSession, RelinkStatus, WorkflowConfig } from '../types'

interface Props { onSetup: () => void; ready: boolean }

const DEFAULT: WorkflowConfig = {
  session_name: '',
  camtrap_dir: '',
  image_base_dir: '',
  flat_search: false,
  output_dir: '',
  target_species: [],
  study_start: '',
  study_end: '',
  occasion_days: 5,
  total_iterations: 100000,
  gap_seconds: 60,
  min_score: 0.5,
  min_score_by_species: {},
  include_burst_context: false,
  classified_by: 'expert_review',
  extended_confirmation: false,
}

const inputClass = 'w-full px-3 py-2 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-1 focus:ring-blue-500 font-mono'
const labelClass = 'block text-sm font-semibold mb-1.5 text-zinc-700 dark:text-zinc-300'
const hintClass  = 'text-xs text-zinc-500 dark:text-zinc-400 mt-1'
const btnOutline = 'px-4 py-2 text-sm border border-zinc-300 dark:border-zinc-600 text-zinc-700 dark:text-zinc-300 rounded hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors disabled:opacity-50'
const btnPrimary = 'px-6 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors'
const btnSuccess = 'px-6 py-2 text-sm bg-emerald-600 text-white rounded hover:bg-emerald-700 transition-colors disabled:opacity-50 flex items-center gap-2'
const btnOutlineSm = 'px-3 py-1 text-xs border border-zinc-300 dark:border-zinc-600 text-zinc-700 dark:text-zinc-300 rounded hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors'
const browseBtn = 'px-3 py-2 text-sm border border-l-0 border-zinc-300 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 rounded-r hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors disabled:opacity-50 flex-shrink-0 flex items-center'

function SmallSpinner() {
  return <div className="w-4 h-4 border border-zinc-500 border-t-zinc-200 rounded-full animate-spin" />
}

export default function SetupPage({ onSetup, ready }: Props) {
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()

  function formatDate(iso: string): string {
    const [y, m, d] = iso.split('-').map(Number)
    return new Date(y, m - 1, d).toLocaleDateString(i18n.language, {
      day: 'numeric', month: 'long', year: 'numeric',
    })
  }

  const STEP_LABELS: string[] = t('setup.steps', { returnObjects: true }) as string[]

  const [step, setStep] = useState(-1)
  const [form, setForm] = useState<WorkflowConfig>(DEFAULT)
  const [availableSpecies, setAvailableSpecies] = useState<string[]>([])
  const [selectedSpecies, setSelectedSpecies] = useState<Set<string>>(new Set())
  const [dataRange, setDataRange] = useState<{ min: string; max: string } | null>(null)
  // Dataset made by "Several classifiers": period, occasions, gap and minimum
  // score were fixed when it was scored and can't change here.
  const [mcLocked, setMcLocked] = useState(false)
  const [sourceType, setSourceType] = useState<'local' | 'trapper' | null>(null)
  const [trapperForm, setTrapperForm] = useState({ url: '', user: '', password: '', researchProject: '', classificationProject: '' })
  const [trapperConn, setTrapperConn] = useState<{ status: 'idle' | 'testing' | 'ok' | 'error'; message: string }>({ status: 'idle', message: '' })
  const [trapperResearchProjects, setTrapperResearchProjects] = useState<{ pk: number; name: string; acronym: string }[]>([])
  const [trapperClassificationProjects, setTrapperClassificationProjects] = useState<{ pk: number; name: string; is_active: boolean }[]>([])
  const [loadingClassProjects, setLoadingClassProjects] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)
  const [localFormat, setLocalFormat] = useState<'camtrapdp' | 'deepfaune' | 'csv' | 'multiclassifier' | null>(null)
  const [deepfauneForm, setDeepfauneForm] = useState({ csvPath: '' })
  const [converting, setConverting] = useState(false)
  const [convertError, setConvertError] = useState<string | null>(null)
  const [deepfauneColumnsInfo, setDeepfauneColumnsInfo] = useState<{ label_col: string; score_col: string } | null>(null)
  const [datapackageErrors, setDatapackageErrors] = useState<string[] | null>(null)
  const [picker, setPicker] = useState<'camtrap_dir' | 'img_base_dir' | 'df_csv' | 'output_dir' | null>(null)
  const [inspecting, setInspecting] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [stepError, setStepError] = useState<string | null>(null)
  const [sessionPickerOpen, setSessionPickerOpen] = useState(false)
  const [importPickerOpen, setImportPickerOpen] = useState(false)
  const [importRelink, setImportRelink] = useState<RelinkStatus | null>(null)
  const [loadingSession, setLoadingSession] = useState(false)
  const [sessionError, setSessionError] = useState<string | null>(null)
  const [sessionSummary, setSessionSummary] = useState<{
    speciesCount: number; resolved: number; total: number; pct: number
  } | null>(null)
  const [recentSessions, setRecentSessions] = useState<RecentSession[]>([])
  const [renaming, setRenaming] = useState<string | null>(null)   // session_dir being renamed
  const [activeDir, setActiveDir] = useState<string | null>(null)
  const [imageCheck, setImageCheck] = useState<{ total: number; missing: number; permission_denied: number; ambiguous: { fileName: string }[] } | null>(null)
  const [checkingImages, setCheckingImages] = useState(false)

  useEffect(() => {
    api.getState().then((s) => {
      setActiveDir(s.session_dir ?? null)
      if (s.config) {
        setForm(s.config)
        setSelectedSpecies(new Set(s.config.target_species))
        setAvailableSpecies(s.config.target_species)
      }
    }).catch(() => {})
    api.getRecentSessions().then(setRecentSessions).catch(() => setRecentSessions([]))
  }, [])

  useEffect(() => {
    if (!ready) return
    api.getSpecies().then((sp) => {
      const total    = sp.reduce((s, x) => s + x.n_total_combos, 0)
      const resolved = sp.reduce((s, x) => s + x.n_resolved, 0)
      const pct      = total > 0 ? Math.round(resolved / total * 100) : 0
      setSessionSummary({ speciesCount: sp.length, resolved, total, pct })
    }).catch(() => {})
  }, [ready])

  useEffect(() => {
    if (!(step === 0 && sourceType === 'local' && localFormat === 'camtrapdp' && form.camtrap_dir.trim())) {
      setImageCheck(null)
      setCheckingImages(false)
      return
    }
    let cancelled = false
    setCheckingImages(true)
    const handle = setTimeout(() => {
      api.checkImages(form.camtrap_dir, form.image_base_dir, form.flat_search)
        .then((r) => { if (!cancelled) setImageCheck(r) })
        .catch(() => { if (!cancelled) setImageCheck(null) })
        .finally(() => { if (!cancelled) setCheckingImages(false) })
    }, 500)
    return () => { cancelled = true; clearTimeout(handle) }
  }, [step, sourceType, localFormat, form.camtrap_dir, form.image_base_dir, form.flat_search])

  function set<K extends keyof WorkflowConfig>(key: K, value: WorkflowConfig[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function handleDirSelected(path: string) {
    if (picker === 'camtrap_dir') {
      set('camtrap_dir', path)
      setMcLocked(false)
      setPicker(null)
      setInspecting(true)
      try {
        const info = await api.inspectDir(path)
        setAvailableSpecies(info.species)
        setSelectedSpecies(new Set(info.species))
        setDatapackageErrors(info.datapackage_errors)
        if (info.study_start && info.study_end) {
          set('study_start', info.study_start)
          set('study_end', info.study_end)
          setDataRange({ min: info.study_start, max: info.study_end })
        }
      } catch { /* usuario puede continuar manualmente */ }
      finally { setInspecting(false) }
    } else if (picker === 'img_base_dir') {
      set('image_base_dir', path)
      setPicker(null)
    } else if (picker === 'df_csv') {
      setDeepfauneForm(f => ({ ...f, csvPath: path }))
      setPicker(null)
    } else if (picker === 'output_dir') {
      set('output_dir', path)
      setPicker(null)
    }
  }

  // Both DeepFaune and Custom CSV write filePath unresolved (see
  // deepfaune_to_camtrapdp / generic_csv_to_camtrapdp) -- conversion just
  // produces a plain CamtrapDP directory, so after it the wizard drops into
  // the same "CamtrapDP directory" panel as Option A instead of jumping to
  // Species: camtrap_dir is pre-filled, image_base_dir is left for the user
  // to fill in explicitly (not pre-filled), and resolve_media_path takes it
  // from there exactly like a CamtrapDP directory picked directly.
  async function afterConversion(camtrap_dir: string) {
    set('camtrap_dir', camtrap_dir)
    setMcLocked(false)
    setInspecting(true)
    try {
      const info = await api.inspectDir(camtrap_dir)
      if (info.species.length) {
        setAvailableSpecies(info.species)
        setSelectedSpecies(new Set(info.species))
      }
      setDatapackageErrors(info.datapackage_errors)
      if (info.study_start && info.study_end) {
        set('study_start', info.study_start)
        set('study_end', info.study_end)
        setDataRange({ min: info.study_start, max: info.study_end })
      }
    } catch { /* continúa manualmente */ }
    finally { setInspecting(false) }
    setStepError(null)
    setLocalFormat('camtrapdp')
  }

  // The multi-classifier dataset was scored with its own study period,
  // occasion length and event gap: the session must use the same ones, and
  // its probabilities are already filtered, so no minimum score is applied.
  async function afterMcConversion(camtrap_dir: string, p: McParams) {
    await afterConversion(camtrap_dir)
    setForm((f) => ({
      ...f,
      study_start: p.study_start,
      study_end: p.study_end,
      occasion_days: p.occasion_days,
      gap_seconds: p.gap_seconds,
      min_score: 0,
      min_score_by_species: {},
    }))
    setDataRange({ min: p.study_start, max: p.study_end })
    setSelectedSpecies(new Set(p.species))
    setMcLocked(true)
  }

  async function handleDeepfauneConvert() {
    setConverting(true)
    setConvertError(null)
    try {
      const { camtrap_dir, label_col, score_col } = await api.convertDeepfaune(
        deepfauneForm.csvPath,
        form.min_score,
      )
      setDeepfauneColumnsInfo({ label_col, score_col })
      await afterConversion(camtrap_dir)
    } catch (e) {
      setConvertError(e instanceof Error ? e.message : t('setup.err_unknown'))
    } finally {
      setConverting(false)
    }
  }

  function validate(currentDataRange: { min: string; max: string } | null): string | null {
    if (step === 0 && !form.camtrap_dir.trim()) return t('setup.err_no_dir')
    if (step === 0 && imageCheck && imageCheck.ambiguous.length > 0)
      return t('setup.err_ambiguous_images', { count: imageCheck.ambiguous.length })
    if (step === 1 && selectedSpecies.size === 0) return t('setup.err_no_species')
    if (step === 2) {
      if (!form.study_start) return t('setup.err_no_start')
      if (!form.study_end)   return t('setup.err_no_end')
      if (form.study_start > form.study_end) return t('setup.err_dates_order')
      if (currentDataRange) {
        if (form.study_start < currentDataRange.min)
          return t('setup.err_start_too_early', { date: form.study_start, min: currentDataRange.min })
        if (form.study_end > currentDataRange.max)
          return t('setup.err_end_too_late', { date: form.study_end, max: currentDataRange.max })
      }
    }
    return null
  }

  async function goNext() {
    let currentDataRange = dataRange
    if (step === 0 && !dataRange && form.camtrap_dir.trim()) {
      setInspecting(true)
      try {
        const info = await api.inspectDir(form.camtrap_dir)
        if (info.species.length) {
          setAvailableSpecies(info.species)
          setSelectedSpecies(new Set(info.species))
        }
        setDatapackageErrors(info.datapackage_errors)
        if (info.study_start && info.study_end) {
          set('study_start', info.study_start)
          set('study_end', info.study_end)
          currentDataRange = { min: info.study_start, max: info.study_end }
          setDataRange(currentDataRange)
        }
      } catch { /* usuario puede continuar manualmente */ }
      finally { setInspecting(false) }
    }
    const err = validate(currentDataRange)
    if (err) { setStepError(err); return }
    setStepError(null)
    setStep((s) => s + 1)
  }

  function goBack() {
    setStepError(null)
    if (step === 0 && sourceType === 'local' && localFormat !== null) {
      setLocalFormat(null)
      setConvertError(null)
      setMcLocked(false)
    } else if (step === 0 && sourceType !== null) {
      setSourceType(null)
      setLocalFormat(null)
      setMcLocked(false)
    } else {
      setStep((s) => s - 1)
    }
  }

  async function handleSubmit() {
    setSubmitting(true)
    setStepError(null)
    try {
      const overrides = Object.fromEntries(
        Object.entries(form.min_score_by_species ?? {}).filter(([sp, v]) => selectedSpecies.has(sp) && Number.isFinite(v)),
      )
      await api.setup({ ...form, target_species: [...selectedSpecies], min_score_by_species: overrides })
      onSetup()
      navigate('/species')
    } catch (e) {
      setStepError(e instanceof Error ? e.message : t('setup.err_unknown'))
    } finally {
      setSubmitting(false)
    }
  }

  async function handleLoadSession(path: string) {
    setSessionPickerOpen(false)
    setLoadingSession(true)
    setSessionError(null)
    try {
      await api.loadSession(path)
      onSetup()
      navigate('/species')
    } catch (e) {
      setSessionError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingSession(false)
    }
  }

  // A session exported on another computer (see "Export session" on the
  // species index). Its folders usually need pointing at this computer's.
  async function handleImportSession(path: string) {
    setImportPickerOpen(false)
    setLoadingSession(true)
    setSessionError(null)
    try {
      const r = await api.importSession(path)
      onSetup()
      if (r.relink.needs_relink) setImportRelink(r.relink)
      else navigate('/species')
    } catch (e) {
      setSessionError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingSession(false)
    }
  }

  async function handleResearchProjectChange(pk: string) {
    setTrapperForm(f => ({ ...f, researchProject: pk, classificationProject: '' }))
    setTrapperClassificationProjects([])
    if (!pk) return
    setLoadingClassProjects(true)
    try {
      const { results } = await api.trapperClassificationProjects(Number(pk))
      setTrapperClassificationProjects(results)
    } catch {
      setTrapperClassificationProjects([])
    } finally {
      setLoadingClassProjects(false)
    }
  }

  async function handleGenerate() {
    const cpk = Number(trapperForm.classificationProject)
    setGenerating(true)
    setGenError(null)
    try {
      const { task_id } = await api.trapperGenerate(cpk)

      // Poll until the background task finishes
      while (true) {
        await new Promise<void>(r => setTimeout(r, 2000))
        const taskStatus = await api.trapperGenerateStatus(task_id)
        if (taskStatus.status === 'done' && taskStatus.path) {
          const extractedPath = taskStatus.path
          set('camtrap_dir', extractedPath)
          setMcLocked(false)

          // Inspect the extracted CamtrapDP to pre-fill species and dates
          setInspecting(true)
          try {
            const info = await api.inspectDir(extractedPath)
            if (info.species.length) {
              setAvailableSpecies(info.species)
              setSelectedSpecies(new Set(info.species))
            }
            setDatapackageErrors(info.datapackage_errors)
            if (info.study_start && info.study_end) {
              set('study_start', info.study_start)
              set('study_end', info.study_end)
              setDataRange({ min: info.study_start, max: info.study_end })
            }
          } catch {
            // user can fill species and dates manually
          } finally {
            setInspecting(false)
          }

          setStepError(null)
          setStep(1)
          break
        }
        if (taskStatus.status === 'error') {
          throw new Error(taskStatus.error ?? t('setup.err_unknown'))
        }
      }
    } catch (e) {
      setGenError(e instanceof Error ? e.message : String(e))
    } finally {
      setGenerating(false)
    }
  }

  async function handleTestConn() {
    const { url, user, password } = trapperForm
    setTrapperConn({ status: 'testing', message: '' })
    setTrapperResearchProjects([])
    setTrapperForm(f => ({ ...f, researchProject: '', classificationProject: '' }))
    try {
      const res = await api.trapperLogin(url, user, password)
      const { results } = await api.trapperResearchProjects()
      setTrapperResearchProjects(results)
      setTrapperConn({
        status: 'ok',
        message: t('setup.trapper_conn_ok_detail', { count: res.research_projects_count }),
      })
    } catch (e) {
      setTrapperConn({
        status: 'error',
        message: e instanceof Error ? e.message : t('setup.trapper_conn_error_generic'),
      })
    }
  }

  function toggleSpecies(sp: string) {
    setSelectedSpecies((prev) => {
      const next = new Set(prev)
      next.has(sp) ? next.delete(sp) : next.add(sp)
      return next
    })
  }

  // ── Welcome screen ────────────────────────────────────────────────────────
  if (step === -1) return (
    <>
    <div className="flex flex-col items-center justify-center" style={{ minHeight: 'calc(100vh - 56px)' }}>
      <div className="text-center px-4" style={{ maxWidth: 580 }}>
        <div className="mb-3 text-7xl">🦌</div>
        <h1 className="text-4xl font-bold mb-3">{t('welcome.title')}</h1>
        <p className="text-zinc-500 dark:text-zinc-400 mb-6 text-lg">{t('welcome.subtitle')}</p>
        <ul className="list-none text-left text-zinc-500 dark:text-zinc-400 mb-6 mx-auto p-0 space-y-2" style={{ maxWidth: 420 }}>
          <li>📁 <strong className="text-zinc-900 dark:text-zinc-100">{t('welcome.feat_dir')}</strong> — {t('welcome.feat_dir_desc')}</li>
          <li>🐾 <strong className="text-zinc-900 dark:text-zinc-100">{t('welcome.feat_species')}</strong> — {t('welcome.feat_species_desc')}</li>
          <li>📅 <strong className="text-zinc-900 dark:text-zinc-100">{t('welcome.feat_period')}</strong> — {t('welcome.feat_period_desc')}</li>
          <li>⚙️ <strong className="text-zinc-900 dark:text-zinc-100">{t('welcome.feat_params')}</strong> — {t('welcome.feat_params_desc')}</li>
        </ul>
        <div className="flex flex-wrap justify-center gap-3">
          {ready && (
            <button
              className="px-6 py-2.5 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 transition-colors text-base flex items-center gap-2"
              onClick={() => navigate('/species')}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <path d="M12 8v4l3 3m6-3a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              {t('welcome.continue_session')}
            </button>
          )}
          <button
            className="px-6 py-2.5 border border-zinc-300 dark:border-zinc-600 text-zinc-700 dark:text-zinc-300 rounded-lg hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors text-base flex items-center gap-2 disabled:opacity-50"
            onClick={() => setSessionPickerOpen(true)}
            disabled={loadingSession}
          >
            {loadingSession ? (
              <><SmallSpinner />{t('welcome.open_session_loading')}</>
            ) : (
              <>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                  <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                {t('welcome.open_session')}
              </>
            )}
          </button>
          <button
            className="px-6 py-2.5 border border-zinc-300 dark:border-zinc-600 text-zinc-700 dark:text-zinc-300 rounded-lg hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors text-base flex items-center gap-2 disabled:opacity-50"
            onClick={() => setImportPickerOpen(true)}
            disabled={loadingSession}
            title={t('welcome.import_session_tooltip')}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            {t('welcome.import_session')}
          </button>
          <button
            className="px-6 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-base flex items-center gap-2"
            onClick={() => { set('session_name', ''); setStep(0) }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            {ready ? t('welcome.new_session') : t('welcome.start')}
          </button>
          {sessionError && (
            <p className="text-red-500 dark:text-red-400 text-sm mt-1 w-full text-center">{sessionError}</p>
          )}
          {ready && sessionSummary && (
            <p className="text-zinc-500 dark:text-zinc-400 text-sm mt-1 w-full text-center">
              {form.session_name && <><strong className="text-zinc-700 dark:text-zinc-200">{form.session_name}</strong>{' · '}</>}
              {t('welcome.session_summary', {
                speciesCount: sessionSummary.speciesCount,
                resolved: sessionSummary.resolved,
                total: sessionSummary.total,
                pct: sessionSummary.pct,
              })}
            </p>
          )}
        </div>

        {recentSessions.length > 0 && (
          <div className="mt-8 w-full text-left mx-auto" style={{ maxWidth: 460 }}>
            <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500 mb-2">
              {t('welcome.recent_sessions')}
            </p>
            <ul className="list-none p-0 m-0 space-y-1.5">
              {recentSessions.map((s) => renaming === s.session_dir ? (
                <li key={s.session_dir} className="rounded border border-blue-400 px-3 py-2">
                  <SessionNameEditor
                    className="w-full"
                    sessionDir={s.session_dir}
                    name={s.session_name ?? ''}
                    fallback=""
                    startEditing
                    onDone={() => setRenaming(null)}
                    onRenamed={(name) => {
                      setRecentSessions(rs => rs.map(r => r.session_dir === s.session_dir ? { ...r, session_name: name } : r))
                      if (activeDir === s.session_dir) set('session_name', name)
                    }}
                  />
                  <div className="text-xs text-zinc-500 dark:text-zinc-400 truncate font-mono mt-1">{s.session_dir}</div>
                </li>
              ) : (
                <li key={s.session_dir}
                  className="relative rounded border border-zinc-200 dark:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors">
                  <button
                    className="w-full text-left px-3 py-2 pr-24 disabled:opacity-50"
                    onClick={() => handleLoadSession(s.session_dir)}
                    disabled={loadingSession}
                    aria-label={t('session_name.open', { name: s.session_name || s.target_species.join(', ') || t('welcome.recent_untitled') })}
                  >
                    <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate">
                      {s.session_name || s.target_species.join(', ') || t('welcome.recent_untitled')}
                    </div>
                    {s.session_name && s.target_species.length > 0 && (
                      <div className="text-xs text-zinc-600 dark:text-zinc-300 truncate mt-0.5 italic">
                        {s.target_species.join(', ')}
                      </div>
                    )}
                    <div className="text-xs text-zinc-500 dark:text-zinc-400 truncate font-mono mt-0.5">
                      {s.session_dir}
                    </div>
                  </button>
                  <div className="absolute top-2 right-2 flex items-center gap-1">
                    <span className="text-xs text-zinc-400 dark:text-zinc-500">
                      {new Date(s.last_opened).toLocaleDateString(i18n.language, { day: 'numeric', month: 'short' })}
                    </span>
                    <button
                      type="button"
                      className="text-xs px-1.5 py-0.5 rounded text-zinc-500 hover:text-blue-600 dark:hover:text-blue-300 hover:bg-zinc-200 dark:hover:bg-zinc-700"
                      title={t('session_name.rename')}
                      aria-label={t('session_name.rename_one', { name: s.session_name || s.session_dir })}
                      onClick={() => setRenaming(s.session_dir)}
                    >
                      ✎
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>

    {sessionPickerOpen && (
      <DirectoryPicker
        onSelect={handleLoadSession}
        onClose={() => { setSessionPickerOpen(false); setSessionError(null) }}
      />
    )}
    {importPickerOpen && (
      <DirectoryPicker
        showFiles
        fileExt=".zip"
        title={t('welcome.import_session_picker')}
        onSelect={handleImportSession}
        onClose={() => { setImportPickerOpen(false); setSessionError(null) }}
      />
    )}
    {importRelink && (
      <RelinkDialog
        status={importRelink}
        onClose={() => { setImportRelink(null); navigate('/species') }}
        onDone={() => { setImportRelink(null); navigate('/species') }}
      />
    )}
    </>
  )

  // ── Wizard ────────────────────────────────────────────────────────────────
  return (
    <div className="mx-auto px-4 py-4" style={{ maxWidth: 700 }}>

      {/* Step indicator */}
      <div className="flex items-start mb-10">
        {STEP_LABELS.map((label, i) => (
          <div key={i} className="flex items-start flex-1">
            <div className="flex flex-col items-center" style={{ minWidth: 56 }}>
              <div
                className={`w-9 h-9 rounded-full flex items-center justify-center font-bold mb-1 text-sm ${
                  i < step
                    ? 'bg-emerald-600 text-white'
                    : i === step
                    ? 'bg-blue-600 text-white'
                    : 'bg-zinc-700 text-zinc-400'
                }`}
              >
                {i < step ? '✓' : i + 1}
              </div>
              <small className={`text-xs whitespace-nowrap ${
                i === step ? 'text-zinc-900 dark:text-zinc-100' : 'text-zinc-500 dark:text-zinc-400'
              }`}>
                {label}
              </small>
            </div>
            {i < STEP_LABELS.length - 1 && (
              <div className={`flex-1 border-t mx-1 mt-[18px] ${i < step ? 'border-emerald-500' : 'border-zinc-700'}`} />
            )}
          </div>
        ))}
      </div>

      {stepError && (
        <div className="px-4 py-2 rounded bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm mb-4">
          {stepError}
        </div>
      )}

      {deepfauneColumnsInfo && (
        <div className="px-4 py-2 rounded bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-300 text-sm mb-4">
          {t('setup.deepfaune_columns_detected', {
            label: deepfauneColumnsInfo.label_col,
            score: deepfauneColumnsInfo.score_col,
          })}
        </div>
      )}

      {datapackageErrors && datapackageErrors.length > 0 && (
        <div className="px-4 py-2 rounded bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300 text-sm mb-4">
          <p className="font-medium">{t('setup.datapackage_invalid', { count: datapackageErrors.length })}</p>
          <ul className="list-disc list-inside mt-1">
            {datapackageErrors.slice(0, 5).map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </div>
      )}

      {/* ── Step 0a: source selection ── */}
      {step === 0 && sourceType === null && (
        <div>
          <h4 className="text-lg font-semibold mb-1">{t('setup.step0_source_title')}</h4>
          <p className="text-zinc-500 dark:text-zinc-400 mb-6 text-sm">{t('setup.step0_source_desc')}</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <button
              className="flex flex-col items-center gap-3 p-6 rounded-xl border-2 border-zinc-300 dark:border-zinc-700 hover:border-blue-500 dark:hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-blue-950/30 transition-colors text-center group"
              onClick={() => setSourceType('local')}
            >
              <span className="text-4xl">📁</span>
              <strong className="text-zinc-900 dark:text-zinc-100 group-hover:text-blue-600 dark:group-hover:text-blue-300 transition-colors">
                {t('setup.source_local')}
              </strong>
              <span className="text-zinc-500 dark:text-zinc-400 text-sm">{t('setup.source_local_desc')}</span>
            </button>
            <button
              className="flex flex-col items-center gap-3 p-6 rounded-xl border-2 border-zinc-300 dark:border-zinc-700 hover:border-zinc-400 dark:hover:border-zinc-500 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 transition-colors text-center group"
              onClick={() => setSourceType('trapper')}
            >
              <span className="text-4xl">🌐</span>
              <strong className="text-zinc-900 dark:text-zinc-100 transition-colors">
                {t('setup.source_trapper')}
              </strong>
              <span className="text-zinc-500 dark:text-zinc-400 text-sm">{t('setup.source_trapper_desc')}</span>
            </button>
          </div>
        </div>
      )}

      {/* ── Step 0b: Trapper form ── */}
      {step === 0 && sourceType === 'trapper' && (() => {
        const tf = trapperForm
        const setTF = (k: keyof typeof tf, v: string) => {
          setTrapperForm(f => ({ ...f, [k]: v }))
          if (k === 'url' || k === 'user' || k === 'password') {
            setTrapperConn({ status: 'idle', message: '' })
            setTrapperResearchProjects([])
            setTrapperClassificationProjects([])
          }
        }
        const hasProject = tf.researchProject !== ''
        const canTest = tf.url !== '' && tf.user !== '' && tf.password !== ''
        const isTesting = trapperConn.status === 'testing'
        return (
          <div>
            {/* Connection */}
            <h4 className="text-base font-semibold mb-4 text-zinc-700 dark:text-zinc-300 flex items-center gap-2">
              <span className="w-5 h-5 rounded-full bg-zinc-200 dark:bg-zinc-700 text-xs flex items-center justify-center font-bold">1</span>
              {t('setup.trapper_conn_title')}
            </h4>

            <div className="mb-4">
              <label className={labelClass}>{t('setup.trapper_url')}</label>
              <input
                className={inputClass}
                placeholder="https://trapper.example.com"
                value={tf.url}
                onChange={e => setTF('url', e.target.value)}
                autoComplete="url"
              />
            </div>

            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <label className={labelClass}>{t('setup.trapper_user')}</label>
                <input
                  className={inputClass}
                  placeholder={t('setup.trapper_user_placeholder')}
                  value={tf.user}
                  onChange={e => setTF('user', e.target.value)}
                  autoComplete="username"
                />
              </div>
              <div>
                <label className={labelClass}>{t('setup.trapper_password')}</label>
                <input
                  type="password"
                  className={inputClass}
                  placeholder="••••••••"
                  value={tf.password}
                  onChange={e => setTF('password', e.target.value)}
                  autoComplete="current-password"
                />
              </div>
            </div>

            <div className="mb-6">
              <div className="flex justify-end">
                <button
                  type="button"
                  disabled={!canTest || isTesting}
                  onClick={handleTestConn}
                  className={[
                    'px-4 py-2 text-sm rounded border flex items-center gap-2 transition-colors',
                    canTest && !isTesting
                      ? 'border-zinc-400 dark:border-zinc-500 text-zinc-700 dark:text-zinc-200 hover:border-blue-500 hover:text-blue-600 dark:hover:text-blue-400 cursor-pointer'
                      : 'border-zinc-300 dark:border-zinc-700 text-zinc-400 dark:text-zinc-600 cursor-not-allowed',
                  ].join(' ')}
                >
                  {isTesting && <SmallSpinner />}
                  {t(isTesting ? 'setup.trapper_testing' : 'setup.trapper_test')}
                </button>
              </div>

              {trapperConn.status === 'ok' && (
                <div className="flex items-center gap-1.5 mt-2 text-sm text-emerald-600 dark:text-emerald-400 justify-end">
                  <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                  {trapperConn.message}
                </div>
              )}
              {trapperConn.status === 'error' && (
                <div className="mt-2 text-sm text-red-600 dark:text-red-400 text-right">
                  {trapperConn.message}
                </div>
              )}
            </div>

            {/* Divider */}
            <div className="border-t border-zinc-200 dark:border-zinc-700 mb-6" />

            {/* Project selection */}
            <h4 className="text-base font-semibold mb-4 text-zinc-700 dark:text-zinc-300 flex items-center gap-2">
              <span className="w-5 h-5 rounded-full bg-zinc-200 dark:bg-zinc-700 text-xs flex items-center justify-center font-bold">2</span>
              {t('setup.trapper_projects_title')}
            </h4>

            <div className="mb-4">
              <label className={labelClass}>{t('setup.trapper_research_project')}</label>
              <select
                disabled={trapperConn.status !== 'ok' || trapperResearchProjects.length === 0}
                className={[
                  inputClass,
                  trapperConn.status !== 'ok' ? 'cursor-not-allowed opacity-50' : '',
                ].join(' ')}
                value={tf.researchProject}
                onChange={e => handleResearchProjectChange(e.target.value)}
              >
                <option value="">
                  {trapperConn.status !== 'ok'
                    ? t('setup.trapper_research_hint')
                    : t('setup.trapper_research_placeholder')}
                </option>
                {trapperResearchProjects.map(p => (
                  <option key={p.pk} value={String(p.pk)}>
                    {p.acronym ? `${p.acronym} — ${p.name}` : p.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="mb-6">
              <label className={labelClass}>{t('setup.trapper_classification')}</label>
              <select
                disabled={!hasProject || loadingClassProjects}
                className={[
                  inputClass,
                  !hasProject || loadingClassProjects ? 'cursor-not-allowed opacity-50' : '',
                ].join(' ')}
                value={tf.classificationProject}
                onChange={e => setTF('classificationProject', e.target.value)}
              >
                <option value="">
                  {loadingClassProjects
                    ? t('setup.trapper_classification_loading')
                    : !hasProject
                      ? t('setup.trapper_classification_wait')
                      : t('setup.trapper_classification_placeholder')}
                </option>
                {trapperClassificationProjects.map(p => (
                  <option key={p.pk} value={String(p.pk)}>
                    {p.name}{p.is_active ? '' : ` (${t('setup.trapper_inactive')})`}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col items-end gap-2">
              <button
                type="button"
                disabled={!tf.classificationProject || generating}
                onClick={handleGenerate}
                className={[
                  'px-5 py-2 text-sm rounded bg-emerald-600 text-white flex items-center gap-2 transition-opacity',
                  !tf.classificationProject || generating
                    ? 'opacity-40 cursor-not-allowed'
                    : 'hover:bg-emerald-700 cursor-pointer',
                ].join(' ')}
              >
                {generating && <SmallSpinner />}
                {t(generating ? 'setup.trapper_generating' : 'setup.trapper_generate')}
              </button>
              {genError && (
                <p className="text-sm text-red-600 dark:text-red-400 text-right">{genError}</p>
              )}
            </div>
          </div>
        )
      })()}

      {/* ── Step 0c: local format selector ── */}
      {step === 0 && sourceType === 'local' && localFormat === null && (
        <div>
          <h4 className="text-lg font-semibold mb-1">{t('setup.local_format_title')}</h4>
          <p className="text-zinc-500 dark:text-zinc-400 mb-6 text-sm">{t('setup.local_format_desc')}</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <button
              className="flex flex-col items-center gap-3 p-6 rounded-xl border-2 border-zinc-300 dark:border-zinc-700 hover:border-blue-500 dark:hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-blue-950/30 transition-colors text-center group"
              onClick={() => setLocalFormat('camtrapdp')}
            >
              <span className="text-4xl">📁</span>
              <strong className="text-zinc-900 dark:text-zinc-100 group-hover:text-blue-600 dark:group-hover:text-blue-300 transition-colors">
                {t('setup.local_format_camtrapdp')}
              </strong>
              <span className="text-zinc-500 dark:text-zinc-400 text-sm">{t('setup.local_format_camtrapdp_desc')}</span>
            </button>
            <button
              className="flex flex-col items-center gap-3 p-6 rounded-xl border-2 border-zinc-300 dark:border-zinc-700 hover:border-blue-500 dark:hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-blue-950/30 transition-colors text-center group"
              onClick={() => setLocalFormat('deepfaune')}
            >
              <span className="text-4xl">🤖</span>
              <strong className="text-zinc-900 dark:text-zinc-100 group-hover:text-blue-600 dark:group-hover:text-blue-300 transition-colors">
                {t('setup.local_format_deepfaune')}
              </strong>
              <span className="text-zinc-500 dark:text-zinc-400 text-sm">{t('setup.local_format_deepfaune_desc')}</span>
            </button>
            <button
              className="flex flex-col items-center gap-3 p-6 rounded-xl border-2 border-zinc-300 dark:border-zinc-700 hover:border-blue-500 dark:hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-blue-950/30 transition-colors text-center group"
              onClick={() => setLocalFormat('csv')}
            >
              <span className="text-4xl">📊</span>
              <strong className="text-zinc-900 dark:text-zinc-100 group-hover:text-blue-600 dark:group-hover:text-blue-300 transition-colors">
                {t('setup.local_format_csv')}
              </strong>
              <span className="text-zinc-500 dark:text-zinc-400 text-sm">{t('setup.local_format_csv_desc')}</span>
            </button>
            <button
              className="flex flex-col items-center gap-3 p-6 rounded-xl border-2 border-zinc-300 dark:border-zinc-700 hover:border-blue-500 dark:hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-blue-950/30 transition-colors text-center group"
              onClick={() => setLocalFormat('multiclassifier')}
            >
              <span className="text-4xl">🧩</span>
              <strong className="text-zinc-900 dark:text-zinc-100 group-hover:text-blue-600 dark:group-hover:text-blue-300 transition-colors">
                {t('setup.local_format_mc')}
              </strong>
              <span className="text-zinc-500 dark:text-zinc-400 text-sm">{t('setup.local_format_mc_desc')}</span>
            </button>
          </div>
        </div>
      )}

      {/* ── Step 0d: CamtrapDP directory ── */}
      {step === 0 && sourceType === 'local' && localFormat === 'camtrapdp' && (
        <div>
          <h4 className="text-lg font-semibold mb-1">{t('setup.step0_title')}</h4>
          <p className="text-zinc-500 dark:text-zinc-400 mb-6 text-sm">{t('setup.step0_desc')}</p>

          <div className="mb-6">
            <label className={labelClass}>{t('setup.label_data_dir')}</label>
            <div className="flex">
              <input
                className={`${inputClass} rounded-r-none`}
                placeholder={t('setup.placeholder_data_dir')}
                value={form.camtrap_dir}
                onChange={(e) => { set('camtrap_dir', e.target.value); setMcLocked(false) }}
              />
              <button type="button" className={browseBtn}
                onClick={() => setPicker('camtrap_dir')} disabled={inspecting}>
                {inspecting ? <SmallSpinner /> : t('setup.browse')}
              </button>
            </div>
          </div>

          <div className="mb-6">
            <label className={labelClass}>
              {t('setup.label_image_base_dir')}{' '}
              <span className="text-zinc-400 font-normal">{t('setup.label_output_optional')}</span>
            </label>
            <div className="flex">
              <input
                className={`${inputClass} rounded-r-none`}
                placeholder={t('setup.placeholder_image_base_dir')}
                value={form.image_base_dir}
                onChange={(e) => set('image_base_dir', e.target.value)}
              />
              <button type="button" className={browseBtn}
                onClick={() => setPicker('img_base_dir')}>
                {t('setup.browse')}
              </button>
            </div>
            <p className={hintClass}>{t('setup.hint_image_base_dir')}</p>
            {form.image_base_dir.trim() && (
              <div className="mt-3">
                <button
                  type="button"
                  role="switch"
                  aria-checked={form.flat_search}
                  onClick={() => set('flat_search', !form.flat_search)}
                  className="flex items-start gap-3 w-full text-left group"
                >
                  <span
                    className={`mt-0.5 relative flex-shrink-0 w-10 h-6 rounded-full transition-colors ${
                      form.flat_search ? 'bg-blue-600' : 'bg-zinc-600'
                    }`}
                  >
                    <span
                      className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition-transform ${
                        form.flat_search ? 'translate-x-4' : 'translate-x-0'
                      }`}
                    />
                  </span>
                  <span>
                    <span className="block text-sm font-semibold text-zinc-700 dark:text-zinc-300 group-hover:text-zinc-900 dark:group-hover:text-zinc-100 transition-colors">
                      {t('setup.label_flat_search')}
                    </span>
                    <span className={hintClass}>{t('setup.hint_flat_search')}</span>
                  </span>
                </button>
              </div>
            )}
            {checkingImages && (
              <p className={hintClass}>{t('setup.checking_images')}</p>
            )}
            {!checkingImages && imageCheck && imageCheck.ambiguous.length > 0 && (
              <div className="flex items-start gap-2 px-3 py-2 mt-2 rounded-lg border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/50 text-red-700 dark:text-red-300 text-sm">
                <svg className="flex-shrink-0 mt-0.5" width="15" height="15" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                  <path d="M7.5 1C3.91 1 1 3.91 1 7.5S3.91 14 7.5 14 14 11.09 14 7.5 11.09 1 7.5 1zm.75 10.5h-1.5V7h1.5v4.5zm0-6h-1.5V4h1.5v1.5z" fill="currentColor"/>
                </svg>
                <span>
                  {t('setup.err_ambiguous_images', { count: imageCheck.ambiguous.length })}{' '}
                  {imageCheck.ambiguous.slice(0, 5).map((a) => a.fileName).join(', ')}
                </span>
              </div>
            )}
            {!checkingImages && imageCheck && imageCheck.ambiguous.length === 0 && imageCheck.missing > 0 && (
              <div className="flex items-start gap-2 px-3 py-2 mt-2 rounded-lg border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/50 text-amber-700 dark:text-amber-300 text-sm">
                <svg className="flex-shrink-0 mt-0.5" width="15" height="15" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                  <path d="M7.5 1C3.91 1 1 3.91 1 7.5S3.91 14 7.5 14 14 11.09 14 7.5 11.09 1 7.5 1zm.75 10.5h-1.5V7h1.5v4.5zm0-6h-1.5V4h1.5v1.5z" fill="currentColor"/>
                </svg>
                <span>{t('setup.images_missing_warning', { missing: imageCheck.missing, total: imageCheck.total })}</span>
              </div>
            )}
            {!checkingImages && imageCheck && imageCheck.permission_denied > 0 && (
              <div className="flex items-start gap-2 px-3 py-2 mt-2 rounded-lg border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/50 text-red-700 dark:text-red-300 text-sm">
                <svg className="flex-shrink-0 mt-0.5" width="15" height="15" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                  <path d="M7.5 1C3.91 1 1 3.91 1 7.5S3.91 14 7.5 14 14 11.09 14 7.5 11.09 1 7.5 1zm.75 10.5h-1.5V7h1.5v4.5zm0-6h-1.5V4h1.5v1.5z" fill="currentColor"/>
                </svg>
                <span>{t('setup.images_permission_denied_warning', { count: imageCheck.permission_denied, total: imageCheck.total })}</span>
              </div>
            )}
          </div>

        </div>
      )}

      {/* ── Step 0e: DeepFaune CSV ── */}
      {step === 0 && sourceType === 'local' && localFormat === 'deepfaune' && (
        <div>
          <h4 className="text-lg font-semibold mb-1">{t('setup.deepfaune_title')}</h4>
          <p className="text-zinc-500 dark:text-zinc-400 mb-6 text-sm">{t('setup.deepfaune_desc')}</p>

          <div className="mb-6">
            <label className={labelClass}>{t('setup.deepfaune_csv_label')}</label>
            <div className="flex">
              <input
                className={`${inputClass} rounded-r-none`}
                placeholder={t('setup.deepfaune_csv_placeholder')}
                value={deepfauneForm.csvPath}
                onChange={(e) => setDeepfauneForm(f => ({ ...f, csvPath: e.target.value }))}
              />
              <button type="button" className={browseBtn}
                onClick={() => setPicker('df_csv')}>
                {t('setup.browse')}
              </button>
            </div>
            <p className={hintClass}>{t('setup.deepfaune_csv_hint')}</p>
          </div>

          <div className="flex items-center justify-between gap-2">
            <button type="button" className={btnOutline} onClick={goBack}>
              {t('setup.back')}
            </button>
            <div className="flex flex-col items-end gap-2">
              <button
                type="button"
                disabled={!deepfauneForm.csvPath || converting}
                onClick={handleDeepfauneConvert}
                className={[
                  'px-5 py-2 text-sm rounded bg-emerald-600 text-white flex items-center gap-2 transition-opacity',
                  !deepfauneForm.csvPath || converting
                    ? 'opacity-40 cursor-not-allowed'
                    : 'hover:bg-emerald-700 cursor-pointer',
                ].join(' ')}
              >
                {converting && <SmallSpinner />}
                {t(converting ? 'setup.deepfaune_converting' : 'setup.deepfaune_convert')}
              </button>
              {convertError && (
                <p className="text-sm text-red-600 dark:text-red-400 text-right">{convertError}</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Step 0f: CSV personalizado ── */}
      {step === 0 && sourceType === 'local' && localFormat === 'csv' && (
        <CsvImportForm onConverted={afterConversion} onBack={goBack} />
      )}

      {/* ── Step 0g: combine several classifiers ── */}
      {step === 0 && sourceType === 'local' && localFormat === 'multiclassifier' && (
        <MultiClassifierForm onConverted={afterMcConversion} onBack={goBack} />
      )}

      {/* ── Step 1 ── */}
      {step === 1 && (
        <div>
          <h4 className="text-lg font-semibold mb-1">{t('setup.step1_title')}</h4>
          <p className="text-zinc-500 dark:text-zinc-400 mb-1 text-sm">{t('setup.step1_desc')}</p>

          {availableSpecies.length > 0 && (
            <div className="flex gap-2 mb-3">
              <button type="button" className={btnOutlineSm}
                onClick={() => setSelectedSpecies(new Set(availableSpecies))}>
                {t('setup.select_all')}
              </button>
              <button type="button" className={btnOutlineSm}
                onClick={() => setSelectedSpecies(new Set())}>
                {t('setup.deselect_all')}
              </button>
            </div>
          )}

          <div className="border border-zinc-300 dark:border-zinc-700 rounded p-3 mb-3 overflow-y-auto" style={{ maxHeight: 280 }}>
            {availableSpecies.length === 0 && (
              <p className="text-zinc-500 dark:text-zinc-400 mb-0 text-sm">{t('setup.no_species')}</p>
            )}
            {availableSpecies.map((sp) => (
              <label key={sp} className="flex items-center gap-2 mb-1 cursor-pointer">
                <input
                  type="checkbox"
                  className="w-4 h-4 rounded border-zinc-400 accent-blue-600"
                  id={`sp-${sp}`}
                  checked={selectedSpecies.has(sp)}
                  onChange={() => toggleSpecies(sp)}
                />
                <span className="font-mono text-sm text-zinc-900 dark:text-zinc-100">{sp}</span>
              </label>
            ))}
          </div>

          <div className="text-zinc-500 dark:text-zinc-400 text-xs mt-1">
            {t(selectedSpecies.size === 1 ? 'setup.species_count_one' : 'setup.species_count_other',
              { count: selectedSpecies.size })}
          </div>
        </div>
      )}

      {/* ── Step 2 ── */}
      {step === 2 && (
        <div>
          <h4 className="text-lg font-semibold mb-1">{t('setup.step2_title')}</h4>
          <p className="text-zinc-500 dark:text-zinc-400 mb-4 text-sm">{t('setup.step2_desc')}</p>
          {dataRange && (
            <div className="flex items-start gap-2.5 px-3.5 py-2.5 rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 text-sm mb-6">
              <svg className="flex-shrink-0 mt-0.5" width="15" height="15" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <path d="M7.5 1C3.91 1 1 3.91 1 7.5S3.91 14 7.5 14 14 11.09 14 7.5 11.09 1 7.5 1zm.75 10.5h-1.5V7h1.5v4.5zm0-6h-1.5V4h1.5v1.5z" fill="currentColor"/>
              </svg>
              <span>{t('setup.data_range', { min: formatDate(dataRange.min), max: formatDate(dataRange.max) })}</span>
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className={labelClass}>{t('setup.label_start')}</label>
              <input type="date" className={inputClass} value={form.study_start} disabled={mcLocked}
                min={dataRange?.min} max={dataRange?.max}
                onChange={(e) => set('study_start', e.target.value)} />
            </div>
            <div>
              <label className={labelClass}>{t('setup.label_end')}</label>
              <input type="date" className={inputClass} value={form.study_end} disabled={mcLocked}
                min={dataRange?.min} max={dataRange?.max}
                onChange={(e) => set('study_end', e.target.value)} />
            </div>
          </div>
          {mcLocked && <p className={hintClass}>{t('setup.mc_locked')}</p>}
        </div>
      )}

      {/* ── Step 3 ── */}
      {step === 3 && (
        <div>
          <h4 className="text-lg font-semibold mb-6">{t('setup.step3_title')}</h4>

          {/* ── Session name ── */}
          <div className="mb-6">
            <label htmlFor="session-name" className={labelClass}>
              {t('session_name.label')}{' '}
              <span className="text-zinc-400 font-normal">{t('setup.label_output_optional')}</span>
            </label>
            <input
              id="session-name"
              className={inputClass.replace(' font-mono', '')}
              placeholder={t('session_name.placeholder')}
              maxLength={120}
              value={form.session_name ?? ''}
              onChange={(e) => set('session_name', e.target.value)}
            />
            <p className={hintClass}>{t('session_name.hint')}</p>
          </div>

          {/* ── Output location ── */}
          <div className="mb-6">
            <label className={labelClass}>
              {t('setup.label_output_dir')}{' '}
              <span className="text-zinc-400 font-normal">{t('setup.label_output_optional')}</span>
            </label>
            <div className="flex">
              <input
                className={`${inputClass} rounded-r-none`}
                placeholder={t('setup.placeholder_output_dir')}
                value={form.output_dir}
                onChange={(e) => set('output_dir', e.target.value)}
              />
              <button type="button" className={browseBtn}
                onClick={() => setPicker('output_dir')}>
                {t('setup.browse')}
              </button>
            </div>
            <p className={hintClass}>{t('setup.hint_output_dir')}</p>
          </div>

          <div className="border-t border-zinc-200 dark:border-zinc-700 mt-2 mb-5" />

          {/* ── Sampling parameters ── */}
          <p className="text-xs font-semibold uppercase tracking-wider text-zinc-400 dark:text-zinc-500 mb-1">
            {t('setup.section_sampling')}
          </p>
          <p className="text-zinc-500 dark:text-zinc-400 mb-4 text-sm"
            dangerouslySetInnerHTML={{ __html: t('setup.step3_desc') }} />

          <div className="mb-6">
            <label className={labelClass}>{t('setup.label_occasion_days')}</label>
            <input type="number" className={inputClass} min={1} value={form.occasion_days} disabled={mcLocked}
              onChange={(e) => set('occasion_days', Number(e.target.value))} />
            <p className={hintClass}>{t('setup.hint_occasion_days')}</p>
            {mcLocked && <p className={hintClass}>{t('setup.mc_locked')}</p>}
          </div>

          <div className="mb-6">
            <label className={labelClass}>{t('setup.label_gap')}</label>
            <input type="number" className={inputClass} min={1} value={form.gap_seconds} disabled={mcLocked}
              onChange={(e) => set('gap_seconds', Number(e.target.value))} />
            <p className={hintClass}>{t('setup.hint_gap')}</p>
            {mcLocked && <p className={hintClass}>{t('setup.mc_locked')}</p>}
          </div>

          {mcLocked ? (
          <div className="mb-6">
            <label className={labelClass}>{t('setup.label_min_score')}</label>
            <p className="text-sm text-zinc-700 dark:text-zinc-300">{t('setup.mc_locked_score')}</p>
          </div>
          ) : (
          <div className="mb-6">
            <label className={labelClass}>{t('setup.label_min_score')}</label>
            <div className="flex items-center gap-3">
              <input type="range" className="flex-1 accent-blue-600"
                min={0} max={1} step={0.05} value={form.min_score}
                onChange={(e) => set('min_score', Number(e.target.value))} />
              <span className="px-2 py-1 bg-zinc-700 text-zinc-200 text-sm rounded font-mono text-center" style={{ minWidth: 48 }}>
                {form.min_score.toFixed(2)}
              </span>
            </div>
            <p className={hintClass}>{t('setup.hint_min_score')}</p>

            {/* Per-species thresholds: empty = use the general value above */}
            <details className="mt-3 rounded border border-zinc-200 dark:border-zinc-700 px-3 py-2"
              open={Object.keys(form.min_score_by_species ?? {}).length > 0}>
              <summary className="cursor-pointer text-sm font-semibold text-zinc-700 dark:text-zinc-300">
                {t('setup.label_min_score_by_species')}
              </summary>
              <p className={`${hintClass} mb-2`}>{t('setup.hint_min_score_by_species', { value: form.min_score.toFixed(2) })}</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5">
                {[...selectedSpecies].sort().map((sp) => {
                  const v = form.min_score_by_species?.[sp]
                  return (
                    <label key={sp} className="flex items-center justify-between gap-3 text-sm">
                      <span className="italic text-zinc-700 dark:text-zinc-300 truncate">{sp}</span>
                      <input
                        type="number" min={0} max={1} step={0.05}
                        aria-label={`${t('setup.label_min_score')}: ${sp}`}
                        className="w-24 px-2 py-1 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 font-mono"
                        placeholder={form.min_score.toFixed(2)}
                        value={v ?? ''}
                        onChange={(e) => {
                          const raw = e.target.value
                          setForm((f) => {
                            const next = { ...(f.min_score_by_species ?? {}) }
                            if (raw === '') delete next[sp]
                            else next[sp] = Math.min(1, Math.max(0, Number(raw)))
                            return { ...f, min_score_by_species: next }
                          })
                        }}
                      />
                    </label>
                  )
                })}
              </div>
              {Object.keys(form.min_score_by_species ?? {}).length > 0 && (
                <button type="button" className="mt-2 text-xs text-blue-600 dark:text-blue-400 hover:underline"
                  onClick={() => set('min_score_by_species', {})}>
                  {t('setup.reset_min_score_by_species')}
                </button>
              )}
            </details>
          </div>
          )}

          {/* ── Confirmation parameters ── */}
          <div className="border-t border-zinc-200 dark:border-zinc-700 mt-2 mb-5" />
          <p className="text-xs font-semibold uppercase tracking-wider text-zinc-400 dark:text-zinc-500 mb-4">
            {t('setup.section_confirmation')}
          </p>

          <div className="mb-6">
            <label className={labelClass}>{t('setup.label_classified_by')}</label>
            <input
              type="text"
              className={inputClass}
              placeholder="expert_review"
              value={form.classified_by}
              onChange={(e) => set('classified_by', e.target.value)}
            />
            <p className={hintClass}>{t('setup.hint_classified_by')}</p>
          </div>

          <div className="mb-4">
            <button
              type="button"
              role="switch"
              aria-checked={form.extended_confirmation}
              onClick={() => set('extended_confirmation', !form.extended_confirmation)}
              className="flex items-start gap-3 w-full text-left group"
            >
              <span
                className={`mt-0.5 relative flex-shrink-0 w-10 h-6 rounded-full transition-colors ${
                  form.extended_confirmation ? 'bg-blue-600' : 'bg-zinc-600'
                }`}
              >
                <span
                  className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition-transform ${
                    form.extended_confirmation ? 'translate-x-4' : 'translate-x-0'
                  }`}
                />
              </span>
              <span>
                <span className="block text-sm font-semibold text-zinc-700 dark:text-zinc-300 group-hover:text-zinc-900 dark:group-hover:text-zinc-100 transition-colors">
                  {t('setup.label_extended_confirmation')}
                </span>
                <span className={hintClass}>{t('setup.hint_extended_confirmation')}</span>
              </span>
            </button>
          </div>

          <div className="mb-2">
            <button
              type="button"
              role="switch"
              aria-checked={form.include_burst_context}
              onClick={() => set('include_burst_context', !form.include_burst_context)}
              className="flex items-start gap-3 w-full text-left group"
            >
              <span
                className={`mt-0.5 relative flex-shrink-0 w-10 h-6 rounded-full transition-colors ${
                  form.include_burst_context ? 'bg-blue-600' : 'bg-zinc-600'
                }`}
              >
                <span
                  className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition-transform ${
                    form.include_burst_context ? 'translate-x-4' : 'translate-x-0'
                  }`}
                />
              </span>
              <span>
                <span className="block text-sm font-semibold text-zinc-700 dark:text-zinc-300 group-hover:text-zinc-900 dark:group-hover:text-zinc-100 transition-colors">
                  {t('setup.label_burst_context')}
                </span>
                <span className={hintClass}>{t('setup.hint_burst_context')}</span>
              </span>
            </button>
          </div>
        </div>
      )}

      {/* Navigation */}
      <div className="flex justify-between mt-10">
        {step === 0 && sourceType === 'local' && (localFormat === 'deepfaune' || localFormat === 'csv' || localFormat === 'multiclassifier')
          ? <div />
          : <button type="button" className={btnOutline} onClick={goBack}>
              {step === 0 && sourceType === null ? t('setup.back_welcome') : t('setup.back')}
            </button>
        }
        {(step === 0 && sourceType === null) || (step === 0 && sourceType === 'local' && localFormat === null) || (step === 0 && sourceType === 'local' && localFormat === 'deepfaune') || (step === 0 && sourceType === 'local' && localFormat === 'csv') || (step === 0 && sourceType === 'local' && localFormat === 'multiclassifier') || (step === 0 && sourceType === 'trapper') ? (
          <div />
        ) : step < STEP_LABELS.length - 1 ? (
          <button type="button" className={btnPrimary} onClick={goNext}>
            {t('setup.next')}
          </button>
        ) : (
          <button type="button" className={btnSuccess}
            onClick={handleSubmit} disabled={submitting}>
            {submitting && <SmallSpinner />}
            {submitting ? t('setup.submitting') : t('setup.submit')}
          </button>
        )}
      </div>

      {picker && (
        <DirectoryPicker
          initialPath={
            picker === 'camtrap_dir' ? form.camtrap_dir || undefined
            : picker === 'img_base_dir' ? form.image_base_dir || undefined
            : picker === 'df_csv' ? deepfauneForm.csvPath || undefined
            : picker === 'output_dir' ? form.output_dir || undefined
            : undefined
          }
          showFiles={picker === 'df_csv'}
          fileExt={picker === 'df_csv' ? '.csv' : ''}
          title={
            picker === 'img_base_dir' ? t('setup.img_base_dir_picker_title')
            : picker === 'df_csv' ? t('setup.deepfaune_csv_picker_title')
            : picker === 'output_dir' ? t('setup.output_dir_picker_title')
            : undefined
          }
          onSelect={handleDirSelected}
          onClose={() => setPicker(null)}
        />
      )}
    </div>
  )
}
