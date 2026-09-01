import { useEffect, useMemo, useState } from 'react'
import './restore-browser.css'

function formatBytes(bytes) {
  if (bytes == null || Number.isNaN(Number(bytes))) return '—'
  const amount = Number(bytes)
  if (amount === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(amount) / Math.log(1024)), units.length - 1)
  const scaled = amount / (1024 ** index)
  return `${scaled >= 100 || index === 0 ? scaled.toFixed(0) : scaled.toFixed(1)} ${units[index]}`
}

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

function FolderIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 6.5h6l2 2h9v9a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" /><path d="M3.5 9h17" /></svg>
}

function FileIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3.5h8l4 4v13H6z" /><path d="M14 3.5v4h4" /></svg>
}

export default function RestoreBrowser({ onClose }) {
  const [snapshots, setSnapshots] = useState([])
  const [selectedSnapshot, setSelectedSnapshot] = useState(null)
  const [browse, setBrowse] = useState({ path: '', parent: '', items: [] })
  const [versions, setVersions] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const onKey = (event) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch('/api/restore/snapshots')
      .then(async (response) => {
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || 'Unable to load restore points.')
        if (cancelled) return
        setSnapshots(data.items || [])
        if (data.items?.length) setSelectedSnapshot(data.items[0])
      })
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [])

  async function openPath(path = '', snapshot = selectedSnapshot) {
    if (!snapshot) return
    setError('')
    setVersions(null)
    setLoading(true)
    try {
      const response = await fetch(`/api/restore/browse?snapshot=${encodeURIComponent(snapshot.name)}&path=${encodeURIComponent(path)}`)
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Unable to browse snapshot.')
      setBrowse(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (selectedSnapshot) openPath('', selectedSnapshot)
  }, [selectedSnapshot?.name])

  async function showVersions(item) {
    if (item.type !== 'file') return
    setError('')
    setLoading(true)
    try {
      const response = await fetch(`/api/restore/versions?path=${encodeURIComponent(item.path)}`)
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Unable to load file history.')
      setVersions(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const crumbs = useMemo(() => {
    const parts = browse.path ? browse.path.split('/').filter(Boolean) : []
    return [{ label: 'Snapshot root', path: '' }, ...parts.map((part, index) => ({ label: part, path: parts.slice(0, index + 1).join('/') }))]
  }, [browse.path])

  return <div className="restore-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <section className="restore-panel" role="dialog" aria-modal="true" aria-label="Restore history">
      <header className="restore-header">
        <div><small>JAYN VAULT</small><h2>Restore / History</h2><p>Browse preserved point-in-time versions. Nothing here modifies or deletes backup data.</p></div>
        <button className="restore-close" onClick={onClose} aria-label="Close">×</button>
      </header>

      <div className="restore-layout">
        <aside className="restore-points">
          <div className="restore-section-title"><span>RESTORE POINTS</span><b>{snapshots.length}</b></div>
          {snapshots.map((snapshot) => <button key={snapshot.name} className={selectedSnapshot?.name === snapshot.name ? 'selected' : ''} onClick={() => setSelectedSnapshot(snapshot)}>
            <strong>{formatDate(snapshot.finished_at || snapshot.started_at)}</strong>
            <small>{(snapshot.trigger || 'manual').toUpperCase()} · {snapshot.total_files.toLocaleString()} files</small>
            <em>{formatBytes(snapshot.logical_bytes)}</em>
          </button>)}
          {!loading && snapshots.length === 0 && <p className="restore-empty">No completed snapshots are available yet.</p>}
        </aside>

        <main className="restore-files">
          <div className="restore-toolbar">
            <div className="restore-crumbs">{crumbs.map((crumb, index) => <button key={`${crumb.path}-${index}`} onClick={() => openPath(crumb.path)}>{crumb.label}</button>)}</div>
            <span>{selectedSnapshot?.name || 'No snapshot selected'}</span>
          </div>

          {error && <div className="restore-error">{error}</div>}
          {loading && <div className="restore-loading">Reading protected history…</div>}

          {!loading && !versions && <div className="restore-table">
            {browse.path && <button className="restore-row up" onClick={() => openPath(browse.parent)}><span className="restore-item-icon"><FolderIcon /></span><strong>..</strong><small>Parent folder</small><em>—</em></button>}
            {browse.items.map((item) => <button key={item.path} className="restore-row" onDoubleClick={() => item.type === 'directory' && openPath(item.path)} onClick={() => item.type === 'file' ? showVersions(item) : openPath(item.path)}>
              <span className="restore-item-icon">{item.type === 'directory' ? <FolderIcon /> : <FileIcon />}</span>
              <strong>{item.name}</strong>
              <small>{item.type === 'directory' ? 'Folder' : formatDate(item.modified_at)}</small>
              <em>{item.type === 'file' ? formatBytes(item.size) : '—'}</em>
            </button>)}
            {browse.items.length === 0 && <p className="restore-empty">This folder is empty in the selected snapshot.</p>}
          </div>}

          {!loading && versions && <div className="restore-versions">
            <div className="restore-versions-head"><div><small>FILE HISTORY</small><h3>{versions.path}</h3></div><button onClick={() => setVersions(null)}>Back to snapshot</button></div>
            <p>{versions.count} preserved version{versions.count === 1 ? '' : 's'} found across completed snapshots.</p>
            <div className="restore-version-list">{versions.items.map((version, index) => <div className="restore-version" key={`${version.snapshot}-${index}`}>
              <div><strong>{formatDate(version.backup_at)}</strong><small>{version.snapshot}</small></div>
              <div><span>{formatBytes(version.size)}</span><small>File modified {formatDate(version.modified_at)}</small></div>
              <em>{index === 0 ? 'LATEST' : version.different_from_newer ? 'CHANGED' : 'SAME VERSION'}</em>
            </div>)}</div>
          </div>}
        </main>
      </div>

      <footer className="restore-footer"><span>Snapshots are preserved indefinitely.</span><strong>NO AUTOMATIC DELETION</strong></footer>
    </section>
  </div>
}
