import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'

/**
 * A session's name with a small "rename" button that turns it into a text
 * field (Enter saves, Escape cancels). An empty name clears it.
 */
interface Props {
  sessionDir: string          // '' = the active session
  name: string
  fallback: string            // shown when the session has no name
  onRenamed?: (name: string) => void
  startEditing?: boolean      // open straight in the text field
  onDone?: () => void         // after saving or cancelling
  className?: string
}

export default function SessionNameEditor({
  sessionDir, name, fallback, onRenamed, startEditing = false, onDone, className = '',
}: Props) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(startEditing)
  const [value, setValue] = useState(name)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const r = await api.renameSession(sessionDir, value)
      onRenamed?.(r.session_name)
      setEditing(false)
      onDone?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <span className={`inline-flex items-center gap-1.5 min-w-0 ${className}`}>
        <span className={`truncate ${name ? '' : 'italic text-zinc-500 dark:text-zinc-400'}`}>{name || fallback}</span>
        <button
          type="button"
          className="flex-shrink-0 text-xs px-1.5 py-0.5 rounded text-zinc-500 hover:text-blue-600 dark:hover:text-blue-300 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          title={t('session_name.rename')}
          aria-label={t('session_name.rename')}
          onClick={(e) => { e.stopPropagation(); setValue(name); setEditing(true) }}
        >
          ✎
        </button>
      </span>
    )
  }

  return (
    <span className={`inline-flex flex-col gap-1 min-w-0 ${className}`} onClick={(e) => e.stopPropagation()}>
      <span className="inline-flex items-center gap-1.5">
        <input
          autoFocus
          className="min-w-0 flex-1 px-2 py-1 text-sm rounded border border-zinc-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
          aria-label={t('session_name.label')}
          placeholder={t('session_name.placeholder')}
          maxLength={120}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); save() }
            if (e.key === 'Escape') { e.preventDefault(); setEditing(false); onDone?.() }
          }}
        />
        <button type="button" disabled={saving}
          className="text-xs px-2 py-1 rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
          onClick={save}>
          {t('session_name.save')}
        </button>
        <button type="button"
          className="text-xs px-2 py-1 rounded border border-zinc-300 dark:border-zinc-600 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-700"
          onClick={() => { setEditing(false); onDone?.() }}>
          {t('session_name.cancel')}
        </button>
      </span>
      {error && <span className="text-xs text-red-500">{error}</span>}
    </span>
  )
}
