import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import type { RelinkStatus } from '../types'
import DirectoryPicker from './DirectoryPicker'

interface Props {
  status: RelinkStatus
  onDone: (status: RelinkStatus) => void
  onClose: () => void
}

const inputClass = 'flex-1 min-w-0 px-3 py-2 text-sm rounded-l border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 font-mono focus:outline-none focus:ring-1 focus:ring-blue-500'
const browseBtn = 'px-3 py-2 text-sm border border-l-0 border-zinc-300 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 rounded-r hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors flex-shrink-0'

/**
 * Asks where the session's data and photos are on *this* computer.
 *
 * Shown after importing a session made on another computer (Windows paths on
 * a Mac, or the other way round), or when the folders of a session have moved.
 */
export default function RelinkDialog({ status, onDone, onClose }: Props) {
  const { t } = useTranslation()
  const [camtrapDir, setCamtrapDir] = useState(status.camtrap_dir_ok ? status.camtrap_dir : '')
  const [imageDir, setImageDir] = useState(status.images_ok ? status.image_base_dir : '')
  const [flatSearch, setFlatSearch] = useState(status.flat_search)
  const [picker, setPicker] = useState<'data' | 'images' | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const r = await api.relinkSession(camtrapDir.trim(), imageDir.trim(), flatSearch)
      onDone(r.relink)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[1000] bg-black/60 flex items-center justify-center p-4">
      <div className="w-full max-w-xl rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 p-5 shadow-xl">
        <h4 className="text-lg font-semibold mb-1">{t('relink.title')}</h4>
        <p className="text-sm text-zinc-500 dark:text-zinc-400 mb-4">{t('relink.desc')}</p>

        <label className="block text-sm font-semibold mb-1.5 text-zinc-700 dark:text-zinc-300">{t('relink.data_dir')}</label>
        {!status.camtrap_dir_ok && status.camtrap_dir && (
          <p className="text-xs text-zinc-500 mb-1 break-all">{t('relink.was', { path: status.camtrap_dir })}</p>
        )}
        <div className="flex mb-4">
          <input className={inputClass} value={camtrapDir} onChange={(e) => setCamtrapDir(e.target.value)} placeholder="…/camtrapDP" />
          <button className={browseBtn} onClick={() => setPicker('data')}>{t('relink.browse')}</button>
        </div>

        <label className="block text-sm font-semibold mb-1.5 text-zinc-700 dark:text-zinc-300">{t('relink.images_dir')}</label>
        {!status.images_ok && status.image_base_dir && (
          <p className="text-xs text-zinc-500 mb-1 break-all">{t('relink.was', { path: status.image_base_dir })}</p>
        )}
        <div className="flex mb-1">
          <input className={inputClass} value={imageDir} onChange={(e) => setImageDir(e.target.value)} placeholder={t('relink.images_placeholder')} />
          <button className={browseBtn} onClick={() => setPicker('images')}>{t('relink.browse')}</button>
        </div>
        <label className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400 mb-4">
          <input type="checkbox" checked={flatSearch} onChange={(e) => setFlatSearch(e.target.checked)} />
          {t('relink.flat_search')}
        </label>

        {error && (
          <div className="px-3 py-2 rounded bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm mb-3">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button className="px-4 py-2 text-sm border border-zinc-300 dark:border-zinc-600 rounded hover:bg-zinc-100 dark:hover:bg-zinc-700" onClick={onClose}>
            {t('relink.later')}
          </button>
          <button
            className="px-5 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            onClick={handleSave}
            disabled={saving || !camtrapDir.trim()}
          >
            {saving ? t('relink.saving') : t('relink.save')}
          </button>
        </div>
      </div>

      {picker && (
        <DirectoryPicker
          initialPath={picker === 'data' ? camtrapDir || undefined : imageDir || undefined}
          onSelect={(path) => {
            if (picker === 'data') setCamtrapDir(path)
            else setImageDir(path)
            setPicker(null)
          }}
          onClose={() => setPicker(null)}
        />
      )}
    </div>
  )
}
