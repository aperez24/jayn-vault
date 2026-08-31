import { useEffect, useMemo, useState } from 'react'

function FolderIcon() {
  return (
    <svg className="picker-folder-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3.5 6.8A1.8 1.8 0 0 1 5.3 5h5l2.1 2.2h6.3a1.8 1.8 0 0 1 1.8 1.8v8.7a1.8 1.8 0 0 1-1.8 1.8H5.3a1.8 1.8 0 0 1-1.8-1.8Z" />
      <path d="M3.5 10h17" />
    </svg>
  )
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden="true">
      <path d="M4 4l10 10M14 4 4 14" />
    </svg>
  )
}

export default function FilesystemBrowser({ kind, onSaved, onClose }) {
  const [root, setRoot] = useState(null)
  const [currentPath, setCurrentPath] = useState('')
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
        const [rootsResponse, selectionResponse] = await Promise.all([
          fetch('/api/fs/roots'),
          fetch('/api/config/selection'),
        ])
        const rootsData = await rootsResponse.json()
        const selection = await selectionResponse.json()
        const availableRoots = rootsData?.roots || []
        const configured = selection?.[kind] || null

        let activeRoot = availableRoots.find((candidate) => configured?.startsWith(candidate.path)) || availableRoots[0]
        if (!activeRoot) throw new Error('No storage locations are available to JAYN Vault.')

        if (!cancelled) {
          setRoot(activeRoot)
          setSavedPath(configured)
          await loadDirectory(configured || activeRoot.path)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || 'Unable to load storage locations.')
          setLoading(false)
        }
      }
    }

    initialize()
    return () => { cancelled = true }
  }, [kind])

  useEffect(() => {
    const handleKey = (event) => {
      if (event.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

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
      onClose?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const directories = useMemo(
    () => items.filter((item) => item.type === 'directory'),
    [items],
  )

  const relativeParts = useMemo(() => {
    if (!root || !currentPath.startsWith(root.path)) return []
    const relative = currentPath.slice(root.path.length).replace(/^\/+/, '')
    return relative ? relative.split('/').filter(Boolean) : []
  }, [currentPath, root])

  const breadcrumbs = useMemo(() => {
    if (!root) return []
    const crumbs = [{ name: root.name, path: root.path }]
    let running = root.path
    for (const part of relativeParts) {
      running += `/${part}`
      crumbs.push({ name: part, path: running })
    }
    return crumbs
  }, [root, relativeParts])

  const displayPath = useMemo(() => {
    if (!root) return ''
    return [root.name, ...relativeParts].join(' / ')
  }, [root, relativeParts])

  const savedDisplayPath = useMemo(() => {
    if (!savedPath || !root || !savedPath.startsWith(root.path)) return savedPath
    const relative = savedPath.slice(root.path.length).replace(/^\/+/, '')
    return [root.name, ...relative.split('/').filter(Boolean)].join(' / ')
  }, [savedPath, root])

  const canGoUp = Boolean(root && parent && currentPath !== root.path && currentPath.startsWith(root.path))

  return (
    <div className="picker-overlay" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose?.()
    }}>
      <section className="picker-dialog" role="dialog" aria-modal="true" aria-label={`Choose ${label.toLowerCase()} folder`}>
        <header className="picker-header">
          <div>
            <span className="picker-kicker">JAYN VAULT / {label.toUpperCase()}</span>
            <h2>Choose {label.toLowerCase()} folder</h2>
            <p>{kind === 'source' ? 'Everything inside this folder and its subfolders can be included in the backup.' : 'Backups will be written inside the folder you select.'}</p>
          </div>
          <button type="button" className="picker-close" onClick={onClose} aria-label="Close folder picker">
            <CloseIcon />
          </button>
        </header>

        <nav className="picker-breadcrumbs" aria-label="Current folder path">
          {breadcrumbs.map((crumb, index) => (
            <span key={crumb.path}>
              {index > 0 && <i>›</i>}
              <button type="button" onClick={() => loadDirectory(crumb.path)} disabled={loading || crumb.path === currentPath}>
                {crumb.name}
              </button>
            </span>
          ))}
        </nav>

        <div className="picker-location-bar">
          <div className="picker-location-copy">
            <span>CURRENT LOCATION</span>
            <strong title={currentPath}>{displayPath}</strong>
          </div>
          {canGoUp && (
            <button type="button" className="picker-up" onClick={() => loadDirectory(parent)} disabled={loading}>
              ↑ &nbsp; Up
            </button>
          )}
        </div>

        {error && <div className="picker-error">{error}</div>}

        <div className="picker-list" aria-busy={loading}>
          {loading && <div className="picker-state">Reading folders…</div>}
          {!loading && directories.length === 0 && (
            <div className="picker-state">
              <FolderIcon />
              <strong>This folder has no subfolders.</strong>
              <span>You can still select the current folder.</span>
            </div>
          )}
          {!loading && directories.map((item) => (
            <button type="button" className="picker-row" key={item.path} onClick={() => loadDirectory(item.path)}>
              <span className="picker-folder"><FolderIcon /></span>
              <span className="picker-row-copy">
                <strong>{item.name}</strong>
                <small>{item.writable ? 'Read & write available' : item.readable ? 'Read available' : 'Unavailable'}</small>
              </span>
              <span className="picker-row-arrow">›</span>
            </button>
          ))}
        </div>

        <footer className="picker-footer">
          <div className="picker-saved">
            <span>{savedPath ? `CURRENT ${label.toUpperCase()}` : `${label.toUpperCase()} NOT YET SET`}</span>
            {savedPath && <strong title={savedPath}>{savedDisplayPath}</strong>}
          </div>
          <div className="picker-actions">
            <button type="button" className="picker-cancel" onClick={onClose}>Cancel</button>
            <button type="button" className="picker-select" onClick={saveCurrent} disabled={saving || loading || !currentPath}>
              {saving ? 'Saving…' : 'Select this folder'}
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}
