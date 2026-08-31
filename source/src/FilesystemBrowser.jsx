import { useEffect, useState } from 'react'

const DEFAULT_START = '/mnt/jayn-vault/sources/jaynos'

export default function FilesystemBrowser({ kind, onSaved }) {
  const [currentPath, setCurrentPath] = useState(DEFAULT_START)
  const [parent, setParent] = useState(null)
  const [items, setItems] = useState([])
  const [savedPath, setSavedPath] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const label = kind === 'source' ? 'Source' : 'Destination'

  async function loadDirectory(path) {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(`/api/fs/list?path=${encodeURIComponent(path)}`)
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Unable to browse this directory.')
      setCurrentPath(data.path)
      setParent(data.parent)
      setItems(data.items || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    async function initialize() {
      try {
        const response = await fetch('/api/config/selection')
        const selection = await response.json()
        const configured = selection?.[kind] || null
        if (!cancelled) {
          setSavedPath(configured)
          await loadDirectory(configured || DEFAULT_START)
        }
      } catch {
        if (!cancelled) await loadDirectory(DEFAULT_START)
      }
    }
    initialize()
    return () => { cancelled = true }
  }, [kind])

  async function saveCurrent() {
    setSaving(true)
    setError('')
    try {
      const response = await fetch('/api/config/selection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, path: currentPath }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || `Unable to save ${label.toLowerCase()}.`)
      const nextPath = data?.[kind] || currentPath
      setSavedPath(nextPath)
      onSaved?.(nextPath)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const directories = items.filter((item) => item.type === 'directory')

  return (
    <div className="fs-browser">
      <div className="fs-browser-head">
        <div>
          <span className="fs-kicker">{label.toUpperCase()} BROWSER</span>
          <strong>{savedPath ? 'Configured' : 'Not configured'}</strong>
        </div>
        <button type="button" className="fs-select" onClick={saveCurrent} disabled={saving || loading}>
          {saving ? 'SAVING…' : `USE THIS FOLDER AS ${label.toUpperCase()}`}
        </button>
      </div>

      <div className="fs-path" title={currentPath}>{currentPath}</div>

      <div className="fs-toolbar">
        <button type="button" onClick={() => parent && loadDirectory(parent)} disabled={!parent || loading}>↑ UP ONE LEVEL</button>
        <span>{loading ? 'READING…' : `${directories.length} FOLDERS`}</span>
      </div>

      {error && <div className="fs-error">{error}</div>}

      <div className="fs-list">
        {!loading && directories.length === 0 && <div className="fs-empty">No subfolders in this location.</div>}
        {directories.map((item) => (
          <button type="button" className="fs-row" key={item.path} onClick={() => loadDirectory(item.path)}>
            <span className="fs-folder">▱</span>
            <span className="fs-name">{item.name}</span>
            <span className="fs-permission">{item.writable ? 'R/W' : item.readable ? 'READ' : 'LOCKED'}</span>
            <span className="fs-arrow">→</span>
          </button>
        ))}
      </div>

      {savedPath && <div className="fs-saved">CURRENT {label.toUpperCase()} · {savedPath}</div>}
    </div>
  )
}
