import { useEffect, useMemo, useState } from 'react'
import FilesystemBrowser from './FilesystemBrowser.jsx'

const lenses = {
  Daily: { title: 'Daily passage ready.', detail: 'Office File Server', route: 'OneDrive', time: 'Tomorrow · 06:00 AM', metric: '428 GB', value: '100', suffix: '%', kicker: 'NEXT PASSAGE', telemetry: '428 GB QUEUED' },
  Weekly: { title: 'Weekly archive scheduled.', detail: 'Accounting Archive', route: 'Synology NAS', time: 'Sunday · 02:00 AM', metric: '86 GB', value: '86', suffix: 'GB', kicker: 'ARCHIVE WINDOW', telemetry: 'SUNDAY · 02:00 AM' },
  Sources: { title: 'Choose what moves.', detail: 'Select the folder that JAYN Vault should protect', route: 'All subfolders included', time: 'Local / mounted storage', metric: 'SRC', value: '01', suffix: '', kicker: 'SOURCE NODE', telemetry: 'READY TO SELECT' },
  Destinations: { title: 'Choose where it lands.', detail: 'Select the folder that will receive the backup', route: 'Writable storage', time: 'Local / mounted storage', metric: 'DST', value: '02', suffix: '', kicker: 'DESTINATION ROUTE', telemetry: 'READY TO SELECT' },
}

function formatBytesParts(bytes) {
  if (bytes == null || Number.isNaN(Number(bytes))) return { value: '—', unit: '' }
  const amount = Number(bytes)
  if (amount === 0) return { value: '0', unit: 'B' }
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const index = Math.min(Math.floor(Math.log(amount) / Math.log(1024)), units.length - 1)
  const scaled = amount / (1024 ** index)
  const decimals = scaled >= 100 || index === 0 ? 0 : scaled >= 10 ? 1 : 2
  return { value: scaled.toFixed(decimals).replace(/\.0+$|(?<=\.[0-9])0+$/, ''), unit: units[index] }
}

function formatBytes(bytes) {
  const parts = formatBytesParts(bytes)
  return `${parts.value}${parts.unit ? ` ${parts.unit}` : ''}`
}

function LensIcon({ name }) {
  return <svg className="lens-icon" viewBox="0 0 24 24" aria-hidden="true">
    {name === 'Daily' && <><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M8 3v4M16 3v4M4 10h16M8 14h2M14 14h2M8 17h2" /></>}
    {name === 'Weekly' && <><path d="M5 7h14v13H5z" /><path d="M8 4v6M16 4v6M8 14h2M14 14h2M8 17h2M4 7h16" /></>}
    {name === 'Sources' && <><path d="M4 6.5A1.5 1.5 0 0 1 5.5 5H10l2 2h6.5A1.5 1.5 0 0 1 20 8.5v9A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" /><path d="M4 10h16" /></>}
    {name === 'Destinations' && <><path d="M7.5 18.5h9.2a4.3 4.3 0 0 0 .3-8.6A6 6 0 0 0 5.7 8.7a4.2 4.2 0 0 0 1.8 9.8Z" /><path d="m12 12 0 5M10 14l2-2 2 2" /></>}
  </svg>
}

function ActionIcon({ running = false }) {
  return <svg className={running ? 'action-icon is-running' : 'action-icon'} viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" /><path className="action-arc" d="M12 3.5a8.5 8.5 0 0 1 7.2 4" /><path className="action-play" d="m10 8.7 5 3.3-5 3.3z" /></svg>
}

function ChevronIcon() { return <svg className="chevron-icon" viewBox="0 0 18 18" aria-hidden="true"><path d="M4 9h9M9 5l4 4-4 4" /></svg> }

export default function App() {
  const [lens, setLens] = useState('Daily')
  const [running, setRunning] = useState(false)
  const [saved, setSaved] = useState(false)
  const [pickerKind, setPickerKind] = useState(null)
  const [selections, setSelections] = useState({ source: null, destination: null })
  const [storageRoots, setStorageRoots] = useState([])
  const [storageStatus, setStorageStatus] = useState({ source: null, destination: null, capacity_ok: null, shortfall_bytes: 0 })

  const active = lenses[lens]
  const filesystemLens = lens === 'Sources' || lens === 'Destinations'
  const currentKind = lens === 'Sources' ? 'source' : lens === 'Destinations' ? 'destination' : null
  const selectionPath = currentKind ? selections[currentKind] : null
  const capacityWarning = storageStatus.capacity_ok === false

  async function refreshStorageStatus() {
    try {
      const response = await fetch('/api/storage/status')
      const data = await response.json()
      if (response.ok) {
        setStorageStatus(data)
        return data
      }
    } catch {
      // Keep last known telemetry if refresh temporarily fails.
    }
    return null
  }

  useEffect(() => {
    let cancelled = false
    Promise.all([
      fetch('/api/config/selection').then((response) => response.json()),
      fetch('/api/fs/roots').then((response) => response.json()).catch(() => ({ roots: [] })),
      fetch('/api/storage/status').then((response) => response.json()).catch(() => null),
    ]).then(([selection, rootsData, status]) => {
      if (cancelled) return
      setSelections({ source: selection?.source || null, destination: selection?.destination || null })
      setStorageRoots(rootsData?.roots || [])
      if (status) setStorageStatus(status)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  const friendlySelectionPath = useMemo(() => {
    if (!selectionPath) return ''
    const root = storageRoots.find((candidate) => selectionPath === candidate.path || selectionPath.startsWith(`${candidate.path}/`))
    if (!root) return selectionPath
    const relative = selectionPath.slice(root.path.length).replace(/^\/+/, '')
    return [root.name, ...relative.split('/').filter(Boolean)].join(' › ')
  }, [selectionPath, storageRoots])

  const selectedFolderLabel = useMemo(() => {
    if (!selectionPath) return ''
    const parts = selectionPath.split('/').filter(Boolean)
    return parts.length ? `/${parts[parts.length - 1]}` : '/'
  }, [selectionPath])

  const sourceSize = formatBytesParts(storageStatus.source?.bytes)
  const destinationTotal = formatBytesParts(storageStatus.destination?.total_bytes)
  const destinationFree = formatBytes(storageStatus.destination?.free_bytes)

  let dialValue = active.value, dialSuffix = active.suffix, dialMetric = active.metric
  let dialTime = selectionPath && filesystemLens ? 'READY' : active.time
  let dialKicker = selectionPath && filesystemLens ? 'CONFIGURED' : active.kicker
  let dialTelemetry = selectionPath && filesystemLens ? 'PATH SAVED' : active.telemetry

  if (lens === 'Sources' && storageStatus.source) {
    dialValue = sourceSize.value; dialSuffix = sourceSize.unit
    dialMetric = `${storageStatus.source.files.toLocaleString()} FILES`
    dialTime = `${storageStatus.source.directories.toLocaleString()} FOLDERS`
    dialKicker = 'SOURCE SIZE'; dialTelemetry = 'LIVE STORAGE INDEX'
  }
  if (lens === 'Destinations' && storageStatus.destination) {
    dialValue = destinationTotal.value; dialSuffix = destinationTotal.unit
    dialMetric = capacityWarning ? `SHORT ${formatBytes(storageStatus.shortfall_bytes)}` : `${destinationFree} FREE`
    dialTime = capacityWarning ? 'CAPACITY ALERT' : 'CAPACITY READY'
    dialKicker = 'TOTAL CAPACITY'; dialTelemetry = capacityWarning ? 'INSUFFICIENT FREE SPACE' : 'FREE SPACE VERIFIED'
  }

  const runBackup = async () => {
    const fresh = await refreshStorageStatus()
    if (!fresh || fresh.capacity_ok === false) return
    setRunning(true)
    window.setTimeout(() => setRunning(false), 2200)
  }

  const selectLens = (name) => { setLens(name); setSaved(false) }
  const openPicker = (kind) => setPickerKind(kind)
  const saveSelection = (kind, path) => {
    setSelections((current) => ({ ...current, [kind]: path }))
    setSaved(true)
    window.setTimeout(refreshStorageStatus, 120)
  }

  return <main className={`vault-shell${capacityWarning ? ' capacity-warning' : ''}`}>
    <div className="ambient" /><div className="mesh" />
    <header className="topbar">
      <button className="brand" onClick={() => selectLens('Daily')} aria-label="JAYN Vault home"><img className="brand-emblem" src="/jayn-emblem.png" alt="" /><span className="vault-word"><small>JAYN</small><b>VAULT</b></span></button>
      <div className="top-meta"><span><i /> BRIDGE ONLINE</span><button className="profile">AP</button></div>
    </header>

    <section className="hero">
      <div className="hero-copy">
        <div className="eyebrow"><span>JAYN VAULT</span><b /><em>CONTINUITY, IN MOTION</em></div>
        <h1>Everything important<br /><i>keeps moving.</i></h1>
        <p>A quiet control surface for the files that keep the office moving forward.</p>
        <button className="run" onClick={runBackup} disabled={capacityWarning} title={capacityWarning ? 'Destination does not have enough free space for the selected source.' : undefined}><ActionIcon running={running} /><span>{capacityWarning ? 'CAPACITY REQUIRED' : running ? 'BACKUP IN PROGRESS' : 'RUN BACKUP NOW'}</span><ChevronIcon /></button>
        <small className={`last-run${capacityWarning ? ' is-warning' : ''}`}>{capacityWarning ? `Destination is short ${formatBytes(storageStatus.shortfall_bytes)}.` : running ? 'Synchronizing with office node…' : 'Last successful passage · Today 06:00 AM'}</small>
      </div>

      <div className={`dial-wrap mode-${lens.toLowerCase()}${capacityWarning && lens === 'Destinations' ? ' dial-warning' : ''}`}>
        <div className="dial dial-outer" /><div className="dial dial-middle" /><div className="dial-cross cross-a" /><div className="dial-cross cross-b" /><div className="scan-beam" /><div className="dial-burst" /><div className="telemetry-streak" /><div className="data-node node-a" /><div className="data-node node-b" />
        <div key={`core-${lens}-${selectionPath || 'empty'}-${dialValue}`} className="dial-core">
          <small>{lens.toUpperCase()} LENS</small><strong>{dialValue}<sup>{dialSuffix}</sup></strong><em>{dialKicker}</em><span className="core-foot">LIVE TELEMETRY · {dialTelemetry}</span><div className="core-rule" />
          <h3>{capacityWarning && lens === 'Destinations' ? 'Destination capacity low.' : selectionPath && filesystemLens ? `${lens === 'Sources' ? 'Source' : 'Destination'} configured.` : active.title}</h3>
          <p title={selectionPath || undefined}>{selectionPath ? friendlySelectionPath : active.detail} {!selectionPath && <><span>→</span> {active.route}</>}</p>
          <div className="core-actions"><strong>{dialMetric}</strong><small>{dialTime}</small>{filesystemLens ? <button onClick={() => openPicker(currentKind)}>{selectionPath ? selectedFolderLabel : 'CHANGE FOLDER'}<ChevronIcon /></button> : <button onClick={() => setSaved(true)}>{saved ? 'SAVED' : 'SELECT / CONFIGURE'}<ChevronIcon /></button>}</div>
        </div>
      </div>
    </section>

    <section className="control-surface">
      <div className="surface-tags"><span>LOCAL NODE</span><span>DAILY <b>06:00</b></span><span>WEEKLY <b>SUN</b></span></div>
      <div className="surface-head"><div><span className="eyebrow"><span>CONTROL SURFACE</span><b /></span><h2>Choose a lens.</h2></div><span className={`surface-status${capacityWarning ? ' warning' : ''}`}><i /> {capacityWarning ? 'CAPACITY ALERT' : 'API ONLINE'}</span></div>
      <div className="lens-nav">{Object.keys(lenses).map((name, index) => <button className={lens === name ? 'selected' : ''} onClick={() => selectLens(name)} key={name}><span>0{index + 1}</span><LensIcon name={name} /><label>{name}</label></button>)}</div>
      {filesystemLens && <div className={`selection-summary${capacityWarning && lens === 'Destinations' ? ' warning' : ''}`}>
        <div className="selection-summary-icon"><LensIcon name={lens} /></div>
        <div className="selection-summary-copy"><span>{lens === 'Sources' ? 'SOURCE FOLDER' : 'DESTINATION FOLDER'}</span><strong title={selectionPath || ''}>{selectionPath ? friendlySelectionPath : 'No folder selected'}</strong><small>{lens === 'Sources' && storageStatus.source ? `${formatBytes(storageStatus.source.bytes)} · ${storageStatus.source.files.toLocaleString()} files` : lens === 'Destinations' && storageStatus.destination ? `${destinationFree} free of ${formatBytes(storageStatus.destination.total_bytes)}` : lens === 'Sources' ? 'The selected folder and all of its subfolders will be protected.' : 'Backup data will be written to this location.'}</small></div>
        <button type="button" className="selection-summary-action" onClick={() => openPicker(currentKind)}>{selectionPath ? 'Change folder' : 'Choose folder'}<ChevronIcon /></button>
      </div>}
    </section>

    <footer className="footer"><span>© 2026 JAYN Construction, Inc. All Rights Reserved.</span><span>BUILT FOR GENERATIONS <b>·</b> VAULT / 01</span></footer>
    {pickerKind && <FilesystemBrowser kind={pickerKind} onClose={() => setPickerKind(null)} onSaved={(path) => saveSelection(pickerKind, path)} />}
  </main>
}
