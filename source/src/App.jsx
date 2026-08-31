import { useEffect, useMemo, useState } from 'react'
import FilesystemBrowser from './FilesystemBrowser.jsx'

const lenses = {
  Daily: {
    title: 'Daily passage ready.',
    detail: 'Office File Server',
    route: 'OneDrive',
    time: 'Tomorrow · 06:00 AM',
    metric: '428 GB',
    value: '100',
    suffix: '%',
    kicker: 'NEXT PASSAGE',
    telemetry: '428 GB QUEUED',
  },
  Weekly: {
    title: 'Weekly archive scheduled.',
    detail: 'Accounting Archive',
    route: 'Synology NAS',
    time: 'Sunday · 02:00 AM',
    metric: '86 GB',
    value: '86',
    suffix: 'GB',
    kicker: 'ARCHIVE WINDOW',
    telemetry: 'SUNDAY · 02:00 AM',
  },
  Sources: {
    title: 'Choose what moves.',
    detail: 'Select the folder that JAYN Vault should protect',
    route: 'All subfolders included',
    time: 'Local / mounted storage',
    metric: 'SRC',
    value: '01',
    suffix: '',
    kicker: 'SOURCE NODE',
    telemetry: 'READY TO SELECT',
  },
  Destinations: {
    title: 'Choose where it lands.',
    detail: 'Select the folder that will receive the backup',
    route: 'Writable storage',
    time: 'Local / mounted storage',
    metric: 'DST',
    value: '02',
    suffix: '',
    kicker: 'DESTINATION ROUTE',
    telemetry: 'READY TO SELECT',
  },
}

function LensIcon({ name }) {
  return (
    <svg className="lens-icon" viewBox="0 0 24 24" aria-hidden="true">
      {name === 'Daily' && <><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M8 3v4M16 3v4M4 10h16M8 14h2M14 14h2M8 17h2" /></>}
      {name === 'Weekly' && <><path d="M5 7h14v13H5z" /><path d="M8 4v6M16 4v6M8 14h2M14 14h2M8 17h2M4 7h16" /></>}
      {name === 'Sources' && <><path d="M4 6.5A1.5 1.5 0 0 1 5.5 5H10l2 2h6.5A1.5 1.5 0 0 1 20 8.5v9A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" /><path d="M4 10h16" /></>}
      {name === 'Destinations' && <><path d="M7.5 18.5h9.2a4.3 4.3 0 0 0 .3-8.6A6 6 0 0 0 5.7 8.7a4.2 4.2 0 0 0 1.8 9.8Z" /><path d="m12 12 0 5M10 14l2-2 2 2" /></>}
    </svg>
  )
}

function ActionIcon({ running = false }) {
  return (
    <svg className={running ? 'action-icon is-running' : 'action-icon'} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="8.5" />
      <path className="action-arc" d="M12 3.5a8.5 8.5 0 0 1 7.2 4" />
      <path className="action-play" d="m10 8.7 5 3.3-5 3.3z" />
    </svg>
  )
}

function ChevronIcon() {
  return <svg className="chevron-icon" viewBox="0 0 18 18" aria-hidden="true"><path d="M4 9h9M9 5l4 4-4 4" /></svg>
}

export default function App() {
  const [lens, setLens] = useState('Daily')
  const [running, setRunning] = useState(false)
  const [saved, setSaved] = useState(false)
  const [pickerKind, setPickerKind] = useState(null)
  const [selections, setSelections] = useState({ source: null, destination: null })
  const [storageRoots, setStorageRoots] = useState([])
  const active = lenses[lens]
  const filesystemLens = lens === 'Sources' || lens === 'Destinations'
  const currentKind = lens === 'Sources' ? 'source' : lens === 'Destinations' ? 'destination' : null
  const selectionPath = currentKind ? selections[currentKind] : null

  useEffect(() => {
    let cancelled = false
    Promise.all([
      fetch('/api/config/selection').then((response) => response.json()),
      fetch('/api/fs/roots').then((response) => response.json()).catch(() => ({ roots: [] })),
    ])
      .then(([selection, rootsData]) => {
        if (cancelled) return
        setSelections({ source: selection?.source || null, destination: selection?.destination || null })
        setStorageRoots(rootsData?.roots || [])
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  const friendlySelectionPath = useMemo(() => {
    if (!selectionPath) return ''
    const root = storageRoots.find((candidate) => selectionPath === candidate.path || selectionPath.startsWith(`${candidate.path}/`))
    if (!root) return selectionPath
    const relative = selectionPath.slice(root.path.length).replace(/^\/+/, '')
    return [root.name, ...relative.split('/').filter(Boolean)].join(' › ')
  }, [selectionPath, storageRoots])

  const runBackup = () => {
    setRunning(true)
    window.setTimeout(() => setRunning(false), 2200)
  }

  const selectLens = (name) => {
    setLens(name)
    setSaved(false)
  }

  const openPicker = (kind) => setPickerKind(kind)

  const saveSelection = (kind, path) => {
    setSelections((current) => ({ ...current, [kind]: path }))
    setSaved(true)
  }

  return (
    <main className="vault-shell">
      <div className="ambient" />
      <div className="mesh" />

      <header className="topbar">
        <button className="brand" onClick={() => selectLens('Daily')} aria-label="JAYN Vault home">
          <img className="brand-emblem" src="/jayn-emblem.png" alt="" />
          <span className="vault-word"><small>JAYN</small><b>VAULT</b></span>
        </button>
        <div className="top-meta">
          <span><i /> BRIDGE ONLINE</span>
          <button className="profile">AP</button>
        </div>
      </header>

      <section className="hero">
        <div className="hero-copy">
          <div className="eyebrow"><span>JAYN VAULT</span><b /><em>CONTINUITY, IN MOTION</em></div>
          <h1>Everything important<br /><i>keeps moving.</i></h1>
          <p>A quiet control surface for the files that keep the office moving forward.</p>
          <button className="run" onClick={runBackup}>
            <ActionIcon running={running} />
            <span>{running ? 'BACKUP IN PROGRESS' : 'RUN BACKUP NOW'}</span>
            <ChevronIcon />
          </button>
          <small className="last-run">{running ? 'Synchronizing with office node…' : 'Last successful passage · Today 06:00 AM'}</small>
        </div>

        <div className={`dial-wrap mode-${lens.toLowerCase()}`}>
          <div className="dial dial-outer" />
          <div className="dial dial-middle" />
          <div className="dial-cross cross-a" />
          <div className="dial-cross cross-b" />
          <div className="scan-beam" />
          <div className="dial-burst" />
          <div className="telemetry-streak" />
          <div className="data-node node-a" />
          <div className="data-node node-b" />

          <div key={`core-${lens}-${selectionPath || 'empty'}`} className="dial-core">
            <img className="core-emblem" src="/jayn-emblem.png" alt="" />
            <small>{lens.toUpperCase()} LENS</small>
            <strong>{active.value}<sup>{active.suffix}</sup></strong>
            <em>{selectionPath && filesystemLens ? 'CONFIGURED' : active.kicker}</em>
            <span className="core-foot">LIVE TELEMETRY · {selectionPath && filesystemLens ? 'PATH SAVED' : active.telemetry}</span>
            <div className="core-rule" />
            <h3>{selectionPath && filesystemLens ? `${lens === 'Sources' ? 'Source' : 'Destination'} configured.` : active.title}</h3>
            <p title={selectionPath || undefined}>{selectionPath ? friendlySelectionPath : active.detail} {!selectionPath && <><span>→</span> {active.route}</>}</p>
            <div className="core-actions">
              <strong>{active.metric}</strong>
              <small>{selectionPath && filesystemLens ? 'READY' : active.time}</small>
              {filesystemLens ? (
                <button onClick={() => openPicker(currentKind)}>
                  {selectionPath ? 'CHANGE FOLDER' : 'CHOOSE FOLDER'}
                  <ChevronIcon />
                </button>
              ) : (
                <button onClick={() => setSaved(true)}>{saved ? 'SAVED' : 'SELECT / CONFIGURE'}<ChevronIcon /></button>
              )}
            </div>
          </div>
        </div>
      </section>

      <section className="control-surface">
        <div className="surface-tags">
          <span>LOCAL NODE</span>
          <span>DAILY <b>06:00</b></span>
          <span>WEEKLY <b>SUN</b></span>
        </div>
        <div className="surface-head">
          <div><span className="eyebrow"><span>CONTROL SURFACE</span><b /></span><h2>Choose a lens.</h2></div>
          <span className="surface-status"><i /> API ONLINE</span>
        </div>
        <div className="lens-nav">
          {Object.keys(lenses).map((name, index) => (
            <button className={lens === name ? 'selected' : ''} onClick={() => selectLens(name)} key={name}>
              <span>0{index + 1}</span><LensIcon name={name} /><label>{name}</label>
            </button>
          ))}
        </div>

        {filesystemLens && (
          <div className="selection-summary">
            <div className="selection-summary-icon"><LensIcon name={lens} /></div>
            <div className="selection-summary-copy">
              <span>{lens === 'Sources' ? 'SOURCE FOLDER' : 'DESTINATION FOLDER'}</span>
              <strong title={selectionPath || ''}>{selectionPath ? friendlySelectionPath : 'No folder selected'}</strong>
              <small>{lens === 'Sources' ? 'The selected folder and all of its subfolders will be protected.' : 'Backup data will be written to this location.'}</small>
            </div>
            <button type="button" className="selection-summary-action" onClick={() => openPicker(currentKind)}>
              {selectionPath ? 'Change folder' : 'Choose folder'}
              <ChevronIcon />
            </button>
          </div>
        )}
      </section>

      <footer className="footer">
        <span>© 2026 JAYN Construction, Inc. All Rights Reserved.</span>
        <span>BUILT FOR GENERATIONS <b>·</b> VAULT / 01</span>
      </footer>

      {pickerKind && (
        <FilesystemBrowser
          kind={pickerKind}
          onClose={() => setPickerKind(null)}
          onSaved={(path) => saveSelection(pickerKind, path)}
        />
      )}
    </main>
  )
}
