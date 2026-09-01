import { useEffect, useMemo, useState } from 'react'
import FilesystemBrowser from './FilesystemBrowser.jsx'
import ScheduleEditor from './ScheduleEditor.jsx'
import RestoreBrowser from './RestoreBrowser.jsx'

const lenses = {
  Daily: { title: 'Daily backup scheduled.', detail: 'Automatic daily protection', route: 'Configured route', metric: '—', value: '—', suffix: '', kicker: 'NEXT BACKUP', telemetry: 'SCHEDULE READY' },
  Weekly: { title: 'Weekly backup scheduled.', detail: 'Automatic weekly protection', route: 'Configured route', metric: '—', value: '—', suffix: '', kicker: 'NEXT WEEKLY', telemetry: 'SCHEDULE READY' },
  Sources: { title: 'Choose what moves.', detail: 'Select the folder that JAYN Vault should protect', route: 'All subfolders included', metric: 'SRC', value: '01', suffix: '', kicker: 'SOURCE NODE', telemetry: 'READY TO SELECT' },
  Destinations: { title: 'Choose where it lands.', detail: 'Select the folder that will receive the backup', route: 'Writable storage', metric: 'DST', value: '02', suffix: '', kicker: 'DESTINATION ROUTE', telemetry: 'READY TO SELECT' },
}

const EMPTY_STORAGE_STATUS = { source: null, destination: null, capacity_ok: null, shortfall_bytes: 0 }
const STORAGE_STATUS_CACHE_KEY = 'jayn-vault:storage-status:v1'

function loadCachedStorageStatus() {
  if (typeof window === 'undefined') return EMPTY_STORAGE_STATUS
  try {
    const cached = JSON.parse(window.localStorage.getItem(STORAGE_STATUS_CACHE_KEY) || 'null')
    return cached && typeof cached === 'object' ? { ...EMPTY_STORAGE_STATUS, ...cached } : EMPTY_STORAGE_STATUS
  } catch {
    return EMPTY_STORAGE_STATUS
  }
}

function cacheStorageStatus(status) {
  if (typeof window === 'undefined' || !status || typeof status !== 'object') return
  try {
    window.localStorage.setItem(STORAGE_STATUS_CACHE_KEY, JSON.stringify(status))
  } catch {}
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

function timeParts(hhmm) {
  if (!hhmm) return { value: '—', suffix: '' }
  const [rawHour, rawMinute] = hhmm.split(':').map(Number)
  const suffix = rawHour >= 12 ? 'PM' : 'AM'
  const hour = rawHour % 12 || 12
  return { value: `${hour}:${String(rawMinute).padStart(2, '0')}`, suffix }
}

function formatRunDate(iso, timezone, options) {
  if (!iso) return '—'
  try {
    return new Intl.DateTimeFormat('en-US', { timeZone: timezone || 'America/New_York', ...options }).format(new Date(iso))
  } catch {
    return new Date(iso).toLocaleString()
  }
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

function ChevronIcon() {
  return <svg className="chevron-icon" viewBox="0 0 18 18" aria-hidden="true"><path d="M4 9h9M9 5l4 4-4 4" /></svg>
}

function DialScaffold({ lens, value, suffix, kicker, telemetry, title, detail, metric, meta, actionLabel, actionClass = '', onAction }) {
  return (
    <div className="dial-core dial-scaffold">
      <small className="dial-slot-label">{lens.toUpperCase()} LENS</small>
      <strong className="dial-slot-value">{value}<sup>{suffix}</sup></strong>
      <em className="dial-slot-kicker">{kicker}</em>
      <span className="core-foot dial-slot-telemetry">LIVE TELEMETRY · {telemetry}</span>
      <div className="core-rule dial-slot-rule" />
      <h3 className="dial-slot-title">{title}</h3>
      <p className="dial-slot-detail">{detail}</p>
      <div className="core-actions dial-slot-actions">
        <div className="dial-slot-metrics"><strong>{metric}</strong><small>{meta}</small></div>
        <button className={actionClass} onClick={onAction}>{actionLabel}<ChevronIcon /></button>
      </div>
    </div>
  )
}

export default function App() {
  const [lens, setLens] = useState('Daily')
  const [pickerKind, setPickerKind] = useState(null)
  const [scheduleEditorMode, setScheduleEditorMode] = useState(null)
  const [restoreOpen, setRestoreOpen] = useState(false)
  const [selections, setSelections] = useState({ source: null, destination: null })
  const [storageRoots, setStorageRoots] = useState([])
  const [storageStatus, setStorageStatus] = useState(() => loadCachedStorageStatus())
  const [schedule, setSchedule] = useState(null)
  const [job, setJob] = useState({ status: 'idle', phase: 'idle', percent: 0 })
  const [jobError, setJobError] = useState('')

  const active = lenses[lens]
  const filesystemLens = lens === 'Sources' || lens === 'Destinations'
  const currentKind = lens === 'Sources' ? 'source' : lens === 'Destinations' ? 'destination' : null
  const selectionPath = currentKind ? selections[currentKind] : null
  const capacityWarning = storageStatus.capacity_ok === false
  const running = job?.status === 'running'

  async function refreshStorageStatus() {
    try {
      const response = await fetch('/api/storage/status')
      const data = await response.json()
      if (response.ok) {
        setStorageStatus(data)
        cacheStorageStatus(data)
        return data
      }
    } catch {}
    return null
  }

  async function refreshJob() {
    try {
      const response = await fetch('/api/jobs/current')
      const data = await response.json()
      if (response.ok) {
        setJob(data)
        return data
      }
    } catch {}
    return null
  }

  useEffect(() => {
    let cancelled = false

    // Schedule configuration is intentionally loaded independently so the
    // Daily/Weekly dial never waits on slower filesystem/storage telemetry.
    fetch('/api/config/schedule')
      .then((response) => response.json())
      .then((scheduleData) => {
        if (!cancelled && scheduleData) setSchedule(scheduleData)
      })
      .catch(() => {})

    Promise.all([
      fetch('/api/config/selection').then((response) => response.json()),
      fetch('/api/fs/roots').then((response) => response.json()).catch(() => ({ roots: [] })),
      fetch('/api/storage/status').then((response) => response.json()).catch(() => null),
      fetch('/api/jobs/current').then((response) => response.json()).catch(() => null),
    ]).then(([selection, rootsData, status, jobData]) => {
      if (cancelled) return
      setSelections({ source: selection?.source || null, destination: selection?.destination || null })
      setStorageRoots(rootsData?.roots || [])
      if (status) {
        setStorageStatus(status)
        cacheStorageStatus(status)
      }
      if (jobData) setJob(jobData)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!running) return undefined
    const timer = window.setInterval(async () => {
      const next = await refreshJob()
      if (next && next.status !== 'running') await refreshStorageStatus()
    }, 750)
    return () => window.clearInterval(timer)
  }, [running])

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
  const sourceSizeText = formatBytes(storageStatus.source?.bytes)
  const timezone = schedule?.timezone || 'America/New_York'

  const dialModel = useMemo(() => {
    const model = {
      value: active.value, suffix: active.suffix, kicker: active.kicker, telemetry: active.telemetry,
      title: active.title, detail: `${active.detail} → ${active.route}`, metric: active.metric, meta: '',
      actionLabel: 'CONFIGURE', actionClass: '', onAction: () => {},
    }

    if (lens === 'Daily') {
      const config = schedule?.daily
      const clock = timeParts(config?.time || '06:00')
      model.value = config?.enabled === false ? 'OFF' : clock.value
      model.suffix = config?.enabled === false ? '' : clock.suffix
      model.kicker = config?.enabled === false ? 'SCHEDULE PAUSED' : 'NEXT BACKUP'
      model.telemetry = config?.enabled === false ? 'AUTOMATION PAUSED' : 'SCHEDULE READY'
      model.title = config?.enabled === false ? 'Daily backup paused.' : 'Daily backup scheduled.'
      model.detail = config?.enabled === false ? 'Automatic daily protection is paused.' : formatRunDate(config?.next_run, timezone, { weekday: 'long', month: 'short', day: 'numeric' })
      model.metric = storageStatus.source ? `${sourceSizeText} SOURCE` : 'SOURCE —'
      model.meta = storageStatus.destination ? `${destinationFree} FREE` : 'DESTINATION —'
      model.actionLabel = 'EDIT SCHEDULE'
      model.onAction = () => setScheduleEditorMode('daily')
    }

    if (lens === 'Weekly') {
      const config = schedule?.weekly
      const nextDate = config?.next_run
      model.value = config?.enabled === false ? 'OFF' : formatRunDate(nextDate, timezone, { month: 'short', day: '2-digit' }).toUpperCase()
      model.suffix = ''
      model.kicker = config?.enabled === false ? 'SCHEDULE PAUSED' : `${(config?.day || 'sunday').slice(0, 3).toUpperCase()} · ${timeParts(config?.time || '02:00').value} ${timeParts(config?.time || '02:00').suffix}`
      model.telemetry = config?.enabled === false ? 'AUTOMATION PAUSED' : 'WEEKLY WINDOW READY'
      model.title = config?.enabled === false ? 'Weekly backup paused.' : 'Weekly backup scheduled.'
      model.detail = config?.enabled === false ? 'Automatic weekly protection is paused.' : formatRunDate(nextDate, timezone, { weekday: 'long', month: 'long', day: 'numeric' })
      model.metric = storageStatus.source ? `${sourceSizeText} SOURCE` : 'SOURCE —'
      model.meta = storageStatus.destination ? `${destinationFree} FREE` : 'DESTINATION —'
      model.actionLabel = 'EDIT SCHEDULE'
      model.onAction = () => setScheduleEditorMode('weekly')
    }

    if ((lens === 'Daily' || lens === 'Weekly') && running) {
      const percent = Math.max(0, Math.min(100, Number(job?.percent || 0)))
      const scanning = job?.phase === 'starting' || job?.phase === 'scanning'
      model.value = scanning ? '…' : String(Math.round(percent))
      model.suffix = scanning ? '' : '%'
      model.kicker = scanning ? 'SCANNING SOURCE' : 'BACKUP IN PROGRESS'
      model.telemetry = scanning ? 'INDEXING FILES' : `${formatBytes(job?.processed_bytes || 0)} OF ${formatBytes(job?.total_bytes || 0)}`
      model.title = scanning ? 'Preparing backup.' : 'Moving protected files.'
      model.detail = job?.current_file || 'Preparing source and destination…'
      model.metric = `${Number(job?.processed_files || 0).toLocaleString()} / ${Number(job?.total_files || 0).toLocaleString()} FILES`
      model.meta = `${Number(job?.copied_files || 0).toLocaleString()} COPIED`
      model.actionLabel = 'BACKUP RUNNING'
      model.onAction = () => {}
    }

    if (lens === 'Sources') {
      model.value = storageStatus.source ? sourceSize.value : active.value
      model.suffix = storageStatus.source ? sourceSize.unit : active.suffix
      model.kicker = storageStatus.source ? 'SOURCE SIZE' : active.kicker
      model.telemetry = storageStatus.source ? 'LIVE STORAGE INDEX' : active.telemetry
      model.title = selectionPath ? 'Source configured.' : active.title
      model.detail = selectionPath ? friendlySelectionPath : `${active.detail} → ${active.route}`
      model.metric = storageStatus.source ? `${storageStatus.source.files.toLocaleString()} FILES` : active.metric
      model.meta = storageStatus.source ? `${storageStatus.source.directories.toLocaleString()} FOLDERS` : ''
      model.actionLabel = selectionPath ? selectedFolderLabel : 'CHANGE FOLDER'
      model.actionClass = selectionPath ? 'folder-name-button' : ''
      model.onAction = () => setPickerKind('source')
    }

    if (lens === 'Destinations') {
      model.value = storageStatus.destination ? destinationTotal.value : active.value
      model.suffix = storageStatus.destination ? destinationTotal.unit : active.suffix
      model.kicker = storageStatus.destination ? 'TOTAL CAPACITY' : active.kicker
      model.telemetry = storageStatus.destination ? (capacityWarning ? 'INSUFFICIENT FREE SPACE' : 'FREE SPACE VERIFIED') : active.telemetry
      model.title = capacityWarning ? 'Destination capacity low.' : selectionPath ? 'Destination configured.' : active.title
      model.detail = selectionPath ? friendlySelectionPath : `${active.detail} → ${active.route}`
      model.metric = storageStatus.destination ? (capacityWarning ? `SHORT ${formatBytes(storageStatus.shortfall_bytes)}` : `${destinationFree} FREE`) : active.metric
      model.meta = storageStatus.destination ? (capacityWarning ? 'CAPACITY ALERT' : 'CAPACITY READY') : ''
      model.actionLabel = selectionPath ? selectedFolderLabel : 'CHANGE FOLDER'
      model.actionClass = selectionPath ? 'folder-name-button' : ''
      model.onAction = () => setPickerKind('destination')
    }

    return model
  }, [active, lens, schedule, timezone, storageStatus, sourceSize.value, sourceSize.unit, sourceSizeText, destinationTotal.value, destinationTotal.unit, destinationFree, capacityWarning, selectionPath, friendlySelectionPath, selectedFolderLabel, running, job])

  const runBackup = async () => {
    if (running) return
    setJobError('')
    const fresh = await refreshStorageStatus()
    if (!fresh || fresh.capacity_ok === false) return
    try {
      const response = await fetch('/api/jobs/run', { method: 'POST' })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Unable to start backup.')
      setJob(data)
      setLens('Daily')
    } catch (error) {
      setJobError(error.message || 'Unable to start backup.')
    }
  }

  const selectLens = (name) => setLens(name)
  const saveSelection = (kind, path) => {
    setSelections((current) => ({ ...current, [kind]: path }))
    window.setTimeout(refreshStorageStatus, 120)
  }

  const dailyTag = timeParts(schedule?.daily?.time || '06:00')
  const weeklyDay = (schedule?.weekly?.day || 'sunday').slice(0, 3).toUpperCase()
  const heroStatus = capacityWarning
    ? `Destination is short ${formatBytes(storageStatus.shortfall_bytes)}.`
    : jobError ? jobError
      : running ? (job?.phase === 'scanning' || job?.phase === 'starting' ? 'Scanning source before copy…' : `${Math.round(Number(job?.percent || 0))}% · ${Number(job?.processed_files || 0).toLocaleString()} of ${Number(job?.total_files || 0).toLocaleString()} files`)
        : job?.status === 'failed' ? `Last backup failed · ${job?.error || 'Unknown error'}`
          : job?.status === 'completed' ? `Last backup complete · ${Number(job?.copied_files || 0).toLocaleString()} copied · ${Number(job?.skipped_files || 0).toLocaleString()} unchanged`
            : 'Manual passage ready'

  return <main className={`vault-shell${capacityWarning ? ' capacity-warning' : ''}`}>
    <div className="ambient" /><div className="mesh" />
    <header className="topbar">
      <button className="brand" onClick={() => selectLens('Daily')} aria-label="JAYN Vault home"><img className="brand-emblem" src="/jayn-emblem.png" alt="" /><span className="vault-word"><small>JAYN</small><b>VAULT</b></span></button>
      <div className="top-meta"><button className="restore-launch" onClick={() => setRestoreOpen(true)}>RESTORE / HISTORY</button><button className="profile">AP</button></div>
    </header>

    <section className="hero">
      <div className="hero-copy">
        <div className="eyebrow"><span>JAYN VAULT</span><b /><em>CONTINUITY, IN MOTION</em></div>
        <h1>Everything important<br /><i>keeps moving.</i></h1>
        <p>A quiet control surface for the files that keep the office moving forward.</p>
        <button className="run" onClick={runBackup} disabled={capacityWarning || running} title={capacityWarning ? 'Destination does not have enough free space for the selected source.' : undefined}><ActionIcon running={running} /><span>{capacityWarning ? 'CAPACITY REQUIRED' : running ? 'BACKUP IN PROGRESS' : 'RUN BACKUP NOW'}</span><ChevronIcon /></button>
        <small className={`last-run${capacityWarning || jobError || job?.status === 'failed' ? ' is-warning' : ''}`}>{heroStatus}</small>
      </div>

      <div className={`dial-wrap mode-${lens.toLowerCase()}${capacityWarning && lens === 'Destinations' ? ' dial-warning' : ''}`}>
        <div className="dial dial-outer" /><div className="dial dial-middle" /><div className="dial-cross cross-a" /><div className="dial-cross cross-b" /><div className="scan-beam" /><div className="dial-burst" /><div className="telemetry-streak" /><div className="data-node node-a" /><div className="data-node node-b" />
        <DialScaffold key={`dial-${lens}`} lens={lens} {...dialModel} />
      </div>
    </section>

    <section className="control-surface">
      <div className="surface-tags"><span>LOCAL NODE</span><span>DAILY <b>{dailyTag.value}</b></span><span>WEEKLY <b>{weeklyDay}</b></span></div>
      <div className="surface-head"><div><span className="eyebrow"><span>CONTROL SURFACE</span><b /></span><h2>Choose a lens.</h2></div><span className={`surface-status${capacityWarning || job?.status === 'failed' ? ' warning' : ''}`}><i /> {capacityWarning ? 'CAPACITY ALERT' : running ? 'BACKUP ACTIVE' : job?.status === 'failed' ? 'BACKUP ERROR' : 'API ONLINE'}</span></div>
      <div className="lens-nav">{Object.keys(lenses).map((name, index) => <button className={lens === name ? 'selected' : ''} onClick={() => selectLens(name)} key={name}><span>0{index + 1}</span><LensIcon name={name} /><label>{name}</label></button>)}</div>
      {filesystemLens && <div className={`selection-summary${capacityWarning && lens === 'Destinations' ? ' warning' : ''}`}>
        <div className="selection-summary-icon"><LensIcon name={lens} /></div>
        <div className="selection-summary-copy"><span>{lens === 'Sources' ? 'SOURCE FOLDER' : 'DESTINATION FOLDER'}</span><strong title={selectionPath || ''}>{selectionPath ? friendlySelectionPath : 'No folder selected'}</strong><small>{lens === 'Sources' && storageStatus.source ? `${formatBytes(storageStatus.source.bytes)} · ${storageStatus.source.files.toLocaleString()} files` : lens === 'Destinations' && storageStatus.destination ? `${destinationFree} free of ${formatBytes(storageStatus.destination.total_bytes)}` : lens === 'Sources' ? 'The selected folder and all of its subfolders will be protected.' : 'Backup data will be written to this location.'}</small></div>
        <button type="button" className="selection-summary-action" onClick={() => setPickerKind(currentKind)}>{selectionPath ? 'Change folder' : 'Choose folder'}<ChevronIcon /></button>
      </div>}
    </section>

    <footer className="footer"><span>© 2026 JAYN Construction, Inc. All Rights Reserved.</span><span>BUILT FOR GENERATIONS <b>·</b> VAULT / 01</span></footer>

    {pickerKind && <FilesystemBrowser kind={pickerKind} onClose={() => setPickerKind(null)} onSaved={(path) => saveSelection(pickerKind, path)} />}
    {scheduleEditorMode && <ScheduleEditor mode={scheduleEditorMode} schedule={schedule} onClose={() => setScheduleEditorMode(null)} onSaved={setSchedule} />}
    {restoreOpen && <RestoreBrowser onClose={() => setRestoreOpen(false)} />}
  </main>
}
