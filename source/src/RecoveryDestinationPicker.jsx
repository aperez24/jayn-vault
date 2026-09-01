import { useEffect, useMemo, useState } from 'react'

function FolderIcon() {
  return <svg className="picker-folder-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 6.8A1.8 1.8 0 0 1 5.3 5h5l2.1 2.2h6.3a1.8 1.8 0 0 1 1.8 1.8v8.7a1.8 1.8 0 0 1-1.8 1.8H5.3a1.8 1.8 0 0 1-1.8-1.8Z" /><path d="M3.5 10h17" /></svg>
}

function CloseIcon() {
  return <svg viewBox="0 0 18 18" aria-hidden="true"><path d="M4 4l10 10M14 4 4 14" /></svg>
}

export default function RecoveryDestinationPicker({ onSelect, onClose }) {
  const [root, setRoot] = useState(null)
  const [currentPath, setCurrentPath] = useState('')
  const [parent, setParent] = useState(null)
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [creatingFolder, setCreatingFolder] = useState(false)
  const [folderName, setFolderName] = useState('')
  const [creating, setCreating] = useState(false)

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

  async function createFolder(event) {
    event.preventDefault()
    const name = folderName.trim()
    if (!name || !currentPath || creating) return
    setCreating(true)
    setError('')
    try {
      const response = await fetch('/api/restore/recovery/folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent: currentPath, name }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Unable to create folder.')
      setCreatingFolder(false)
      setFolderName('')
      await loadDirectory(data.path)
    } catch (err) {
      setError(err.message || 'Unable to create folder.')
    } finally {
      setCreating(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    fetch('/api/fs/roots')
      .then((response) => response.json())
      .then(async (data) => {
        const first = data?.roots?.[0]
        if (!first) throw new Error('No storage locations are available to JAYN Vault.')
        if (cancelled) return
        setRoot(first)
        await loadDirectory(first.path)
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || 'Unable to load storage locations.')
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    const handleKey = (event) => {
      if (event.key !== 'Escape') return
      if (creatingFolder) {
        setCreatingFolder(false)
        setFolderName('')
      } else {
        onClose?.()
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose, creatingFolder])

  const directories = useMemo(() => items.filter((item) => item.type === 'directory'), [items])
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
  const displayPath = useMemo(() => root ? [root.name, ...relativeParts].join(' / ') : '', [root, relativeParts])
  const canGoUp = Boolean(root && parent && currentPath !== root.path && currentPath.startsWith(root.path))

  return <div className="picker-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose?.() }}>
    <section className="picker-dialog" role="dialog" aria-modal="true" aria-label="Choose recovery destination">
      <header className="picker-header">
        <div><span className="picker-kicker">JAYN VAULT / RECOVERY</span><h2>Choose recovery destination</h2><p>Recovered items will be copied into a new timestamped recovery folder. Existing files will not be overwritten.</p></div>
        <button type="button" className="picker-close" onClick={onClose} aria-label="Close folder picker"><CloseIcon /></button>
      </header>
      <nav className="picker-breadcrumbs" aria-label="Current folder path">{breadcrumbs.map((crumb, index) => <span key={crumb.path}>{index > 0 && <i>›</i>}<button type="button" onClick={() => loadDirectory(crumb.path)} disabled={loading || crumb.path === currentPath}>{crumb.name}</button></span>)}</nav>
      <div className="picker-location-bar">
        <div className="picker-location-copy"><span>RECOVERY LOCATION</span><strong title={currentPath}>{displayPath}</strong></div>
        <div className="picker-location-actions">
          {canGoUp && <button type="button" className="picker-up" onClick={() => loadDirectory(parent)} disabled={loading || creating}>↑ &nbsp; Up</button>}
          <button type="button" className="picker-new-folder" onClick={() => { setCreatingFolder((value) => !value); setFolderName(''); setError('') }} disabled={loading || creating}>+ &nbsp; New folder</button>
        </div>
      </div>
      {creatingFolder && <form className="picker-new-folder-form" onSubmit={createFolder}>
        <div><span>NEW FOLDER</span><input autoFocus value={folderName} onChange={(event) => setFolderName(event.target.value)} placeholder="Folder name" maxLength={120} disabled={creating} /></div>
        <div className="picker-new-folder-actions"><button type="button" onClick={() => { setCreatingFolder(false); setFolderName('') }} disabled={creating}>Cancel</button><button type="submit" disabled={creating || !folderName.trim()}>{creating ? 'Creating…' : 'Create folder'}</button></div>
      </form>}
      {error && <div className="picker-error">{error}</div>}
      <div className="picker-list" aria-busy={loading}>
        {loading && <div className="picker-state">Reading folders…</div>}
        {!loading && directories.length === 0 && <div className="picker-state"><FolderIcon /><strong>This folder has no subfolders.</strong><span>You can recover into the current folder or create a new one.</span></div>}
        {!loading && directories.map((item) => <button type="button" className="picker-row" key={item.path} onClick={() => loadDirectory(item.path)}><span className="picker-folder"><FolderIcon /></span><span className="picker-row-copy"><strong>{item.name}</strong><small>{item.writable ? 'Read & write available' : 'Unavailable'}</small></span><span className="picker-row-arrow">›</span></button>)}
      </div>
      <footer className="picker-footer"><div className="picker-saved"><span>SAFE RECOVERY MODE</span><strong>A new JAYN-Vault-Recovery folder will be created here.</strong></div><div className="picker-actions"><button type="button" className="picker-cancel" onClick={onClose}>Cancel</button><button type="button" className="picker-select" onClick={() => onSelect?.(currentPath)} disabled={loading || creating || !currentPath}>Recover here</button></div></footer>
    </section>
  </div>
}
