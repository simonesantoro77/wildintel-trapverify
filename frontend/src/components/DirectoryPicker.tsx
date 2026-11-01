import { useEffect, useRef, useState } from 'react'

interface DirEntry { name: string; path: string }
interface BrowseResult { current: string; parent: string | null; dirs: DirEntry[]; files?: DirEntry[] }
interface Props {
  onSelect: (path: string) => void
  onClose: () => void
  initialPath?: string
  showFiles?: boolean
  fileExt?: string
  title?: string
}

function Spinner({ sm }: { sm?: boolean }) {
  const sz = sm ? 'w-4 h-4 border' : 'w-5 h-5 border-2'
  return <div className={`${sz} border-zinc-500 border-t-zinc-200 rounded-full animate-spin inline-block`} />
}

const btnOutline = 'px-3 py-1 text-sm border border-zinc-600 text-zinc-300 rounded hover:bg-zinc-700 transition-colors disabled:opacity-50'
const btnPrimary = 'px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors disabled:opacity-50'

export default function DirectoryPicker({ onSelect, onClose, initialPath, showFiles = false, fileExt = '', title }: Props) {
  const [data, setData] = useState<BrowseResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [manualPath, setManualPath] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  async function browse(path?: string) {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams()
    if (path) params.set('path', path)
    if (showFiles) params.set('show_files', 'true')
    if (fileExt) params.set('ext', fileExt)
    const qs = params.toString() ? `?${params}` : ''
    const url = `/api/fs/browse${qs}`
    try {
      const r = await fetch(url)
      const text = await r.text()
      if (!r.ok) {
        let detail = r.statusText
        try { detail = JSON.parse(text).detail ?? r.statusText } catch { /* */ }
        throw new Error(`HTTP ${r.status} — ${detail}\n(URL: ${url})`)
      }
      const result: BrowseResult = JSON.parse(text)
      setData(result)
      setManualPath(result.current)
    } catch (e) {
      if (path !== undefined) { await browse(undefined); return }
      setError(e instanceof Error ? e.message : 'Error al leer el directorio')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { browse(initialPath) }, [])

  function handleManualGo(e: React.FormEvent) {
    e.preventDefault()
    browse(manualPath)
  }

  // Windows paths (D:\fotos\2023) use backslashes and start with a drive.
  const winPath = !!data && /^[A-Za-z]:[\\/]?/.test(data.current)
  const parts = data
    ? data.current.split(/[\\/]/).filter(Boolean).map((part, i, arr) => ({
        label: part || '/',
        path: winPath
          ? arr.slice(0, i + 1).join('\\') + (i === 0 ? '\\' : '')
          : '/' + arr.slice(0, i + 1).join('/'),
      }))
    : []

  return (
    <div
      className="fixed inset-0 flex items-center justify-center z-[1060]"
      style={{ background: 'rgba(0,0,0,.6)' }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-xl flex flex-col"
        style={{ width: 560, maxHeight: '80vh' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-700">
          <span className="font-semibold text-zinc-100">{title ?? 'Seleccionar directorio'}</span>
          <button
            className="w-7 h-7 flex items-center justify-center text-zinc-400 hover:text-zinc-100 text-xl rounded transition-colors"
            onClick={onClose}
            aria-label="Cerrar"
          >
            ×
          </button>
        </div>

        {/* Manual path input */}
        <form onSubmit={handleManualGo} className="px-3 pt-2 pb-1 flex gap-2">
          <input
            ref={inputRef}
            className="flex-1 px-2 py-1 text-sm rounded border border-zinc-600 bg-zinc-800 text-zinc-100 font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
            value={manualPath}
            onChange={(e) => setManualPath(e.target.value)}
            placeholder="/ruta/del/directorio"
            spellCheck={false}
          />
          <button type="submit" className={btnOutline} disabled={loading}>
            Ir
          </button>
        </form>

        {/* Breadcrumbs */}
        {data && (
          <nav className="px-3 pb-1" aria-label="breadcrumb">
            <ol className="flex flex-wrap items-center gap-1 text-xs text-zinc-400 list-none m-0 p-0">
              <li>
                <button
                  className="text-sky-400 hover:text-sky-300 transition-colors bg-transparent border-0 cursor-pointer p-0"
                  onClick={() => browse('/')}
                >
                  /
                </button>
              </li>
              {parts.map((part, i) => (
                <li key={part.path} className="flex items-center gap-1">
                  <span className="text-zinc-600">/</span>
                  {i < parts.length - 1 ? (
                    <button
                      className="text-sky-400 hover:text-sky-300 transition-colors bg-transparent border-0 cursor-pointer p-0"
                      onClick={() => browse(part.path)}
                    >
                      {part.label}
                    </button>
                  ) : (
                    <span className="text-zinc-200">{part.label}</span>
                  )}
                </li>
              ))}
            </ol>
          </nav>
        )}

        {/* Directory listing */}
        <div className="overflow-auto flex-1 px-2 pb-2" style={{ minHeight: 0 }}>
          {loading && (
            <div className="text-center py-4 text-zinc-500 flex items-center justify-center gap-2">
              <Spinner sm />
              <span>Cargando…</span>
            </div>
          )}

          {error && (
            <div className="mx-1 mt-2 px-3 py-2 text-sm rounded bg-red-950 border border-red-800 text-red-300">
              {error}
            </div>
          )}

          {!loading && !error && data && (
            <ul className="list-none m-0 p-0">
              {data.parent && (
                <li
                  className="flex items-center gap-2 py-1 px-2 text-sm text-zinc-400 hover:bg-zinc-800 rounded cursor-pointer font-mono border-b border-zinc-800"
                  onClick={() => browse(data.parent!)}
                >
                  <span className="text-base">↩</span>
                  <span>..</span>
                </li>
              )}

              {data.dirs.length === 0 && (
                <li className="py-1 px-2 text-sm text-zinc-500">Sin subdirectorios</li>
              )}

              {data.dirs.map((dir) => (
                <li
                  key={dir.path}
                  className="flex items-center gap-2 py-1 px-2 text-sm text-zinc-200 hover:bg-zinc-800 rounded cursor-pointer font-mono"
                  onDoubleClick={() => browse(dir.path)}
                  onClick={() => setManualPath(dir.path)}
                >
                  <span className="text-amber-400">📁</span>
                  <span className="truncate">{dir.name}</span>
                </li>
              ))}

              {(data.files ?? []).map((file) => (
                <li
                  key={file.path}
                  className="flex items-center gap-2 py-1 px-2 text-sm text-zinc-300 hover:bg-zinc-800 rounded cursor-pointer font-mono"
                  onClick={() => setManualPath(file.path)}
                  onDoubleClick={() => { onSelect(file.path); onClose() }}
                >
                  <span className="text-sky-400">📄</span>
                  <span className="truncate">{file.name}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Footer */}
        <div className="px-3 py-2 border-t border-zinc-700 flex items-center justify-between gap-2">
          <span className="font-mono text-zinc-500 truncate text-xs" style={{ maxWidth: 340 }}>
            {manualPath || '—'}
          </span>
          <div className="flex gap-2 flex-shrink-0">
            <button className={btnOutline} onClick={onClose}>Cancelar</button>
            <button
              className={btnPrimary}
              onClick={() => { onSelect(manualPath); onClose() }}
              disabled={!manualPath}
            >
              Seleccionar
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
