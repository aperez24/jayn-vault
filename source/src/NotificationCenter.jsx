import { useEffect, useMemo, useState } from 'react'

function BellIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z" /><path d="M10 21h4" /></svg>
}

function CloseIcon() {
  return <svg viewBox="0 0 18 18" aria-hidden="true"><path d="m4 4 10 10M14 4 4 14" /></svg>
}

function formatWhen(value) {
  if (!value) return 'Pending'
  try {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
      timeZone: 'America/New_York',
    }).format(new Date(value))
  } catch {
    return value
  }
}

function severityLabel(event) {
  if (event.type === 'test') return 'TEST'
  return String(event.severity || 'success').toUpperCase()
}

export function NotificationBell({ count = 0, onClick }) {
  return <button className="notification-bell" type="button" onClick={onClick} aria-label="Open notification center">
    <BellIcon />
    {count > 0 && <span>{count > 9 ? '9+' : count}</span>}
  </button>
}

export default function NotificationCenter({ open, onClose, onHistoryChange }) {
  const [tab, setTab] = useState('history')
  const [filter, setFilter] = useState('all')
  const [history, setHistory] = useState([])
  const [settings, setSettings] = useState({
    enabled: true, recipients: [], on_success: true, on_warning: true, on_failure: true,
    smtp_configured: false, smtp_sender: null,
  })
  const [recipientText, setRecipientText] = useState('')
  const [expanded, setExpanded] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  async function load() {
    const [historyResponse, settingsResponse] = await Promise.all([
      fetch('/api/notifications/history?limit=200'),
      fetch('/api/config/notifications'),
    ])
    const historyData = await historyResponse.json()
    const settingsData = await settingsResponse.json()
    if (!historyResponse.ok) throw new Error(historyData.detail || 'Unable to load notification history.')
    if (!settingsResponse.ok) throw new Error(settingsData.detail || 'Unable to load notification settings.')
    setHistory(historyData.items || [])
    setSettings(settingsData)
    setRecipientText((settingsData.recipients || []).join(', '))
    onHistoryChange?.(historyData.items || [])
  }

  useEffect(() => {
    if (!open) return
    setMessage('')
    load().catch((error) => setMessage(error.message))
  }, [open])

  const visibleHistory = useMemo(() => history.filter((event) => {
    if (filter === 'all') return true
    if (filter === 'delivery') return event.delivery_status === 'failed'
    return event.severity === filter
  }), [history, filter])

  async function saveSettings({ keepBusy = false } = {}) {
    setBusy(true)
    setMessage('')
    const recipients = recipientText.split(/[;,\n]/).map((item) => item.trim()).filter(Boolean)
    try {
      const response = await fetch('/api/config/notifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: settings.enabled,
          recipients,
          on_success: settings.on_success,
          on_warning: settings.on_warning,
          on_failure: settings.on_failure,
        }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Unable to save notification settings.')
      setSettings(data)
      setRecipientText((data.recipients || []).join(', '))
      setMessage('Notification settings saved.')
      return true
    } catch (error) {
      setMessage(error.message)
      return false
    } finally {
      if (!keepBusy) setBusy(false)
    }
  }

  async function sendTest() {
    setBusy(true)
    setMessage('')
    try {
      const saved = await saveSettings({ keepBusy: true })
      if (!saved) return
      const response = await fetch('/api/notifications/test', { method: 'POST' })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Unable to send test email.')
      if (data.delivery_status !== 'sent') throw new Error(data.delivery_error || 'Test email delivery failed.')
      setMessage('Test email sent successfully.')
      await load()
    } catch (error) {
      setMessage(error.message)
      await load().catch(() => {})
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  return <div className="notification-overlay" role="presentation" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <section className="notification-panel" role="dialog" aria-modal="true" aria-labelledby="notification-title">
      <header className="notification-header">
        <div><span>VAULT SERVER</span><h2 id="notification-title">Notification Center</h2><p>Backup alerts and email delivery history.</p></div>
        <button type="button" onClick={onClose} aria-label="Close notification center"><CloseIcon /></button>
      </header>

      <nav className="notification-tabs" aria-label="Notification Center sections">
        <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>HISTORY <span>{history.length}</span></button>
        <button className={tab === 'settings' ? 'active' : ''} onClick={() => setTab('settings')}>EMAIL SETTINGS</button>
      </nav>

      {tab === 'history' && <div className="notification-history">
        <div className="notification-filters">
          {[
            ['all', 'All'], ['success', 'Success'], ['warning', 'Warning'], ['failure', 'Failure'], ['delivery', 'Delivery Failed'],
          ].map(([value, label]) => <button key={value} className={filter === value ? 'active' : ''} onClick={() => setFilter(value)}>{label}</button>)}
        </div>
        <div className="notification-list">
          {visibleHistory.length === 0 && <div className="notification-empty"><BellIcon /><strong>No notification records</strong><span>Backup and test-email events will appear here.</span></div>}
          {visibleHistory.map((event) => <button type="button" className={`notification-row severity-${event.severity || 'success'}`} key={event.id} onClick={() => setExpanded(expanded === event.id ? null : event.id)}>
            <span className="notification-severity"><i />{severityLabel(event)}</span>
            <span className="notification-summary"><strong>{event.reason}</strong><small>{event.trigger ? `${String(event.trigger).toUpperCase()} BACKUP` : 'SYSTEM EVENT'} · {formatWhen(event.created_at)}</small></span>
            <span className={`notification-delivery status-${event.delivery_status}`}><i />{String(event.delivery_status || 'pending').replace('_', ' ')}</span>
            {expanded === event.id && <span className="notification-detail">
              <span><b>Run ID</b>{event.backup_run_id || 'Test notification'}</span>
              <span><b>Recipient</b>{(event.recipients || []).join(', ') || 'No recipient configured'}</span>
              <span><b>Delivery</b>{event.sent_at ? formatWhen(event.sent_at) : event.delivery_error || 'Not delivered'}</span>
            </span>}
          </button>)}
        </div>
      </div>}

      {tab === 'settings' && <div className="notification-settings">
        <div className={`smtp-status ${settings.smtp_configured ? 'ready' : 'missing'}`}><i /><div><strong>{settings.smtp_configured ? 'Email service connected' : 'Email service configuration required'}</strong><span>{settings.smtp_configured ? `Sending as ${settings.smtp_sender}` : 'Add the SMTP values to the Vault Server environment.'}</span></div></div>

        <label className="recipient-field"><span>NOTIFICATION RECIPIENTS</span><textarea value={recipientText} onChange={(event) => setRecipientText(event.target.value)} placeholder="alex@jaynconstruction.com" rows="3" /><small>Separate multiple email addresses with commas.</small></label>

        <div className="notification-rule-list">
          {[
            ['enabled', 'Email notifications', 'Allow the Vault Server to send backup alerts.'],
            ['on_success', 'Successful backups', 'Send confirmation after a backup completes normally.'],
            ['on_warning', 'Backups with warnings', 'Send when unreadable or skipped items need review.'],
            ['on_failure', 'Failed backups', 'Send immediately when a backup cannot complete.'],
          ].map(([key, title, detail]) => <label className="notification-rule" key={key}><span><strong>{title}</strong><small>{detail}</small></span><input type="checkbox" checked={Boolean(settings[key])} onChange={(event) => setSettings((current) => ({ ...current, [key]: event.target.checked }))} /><i /></label>)}
        </div>

        {message && <p className="notification-message">{message}</p>}
        <div className="notification-actions"><button type="button" className="secondary" disabled={busy} onClick={sendTest}>SEND TEST EMAIL</button><button type="button" className="primary" disabled={busy} onClick={saveSettings}>{busy ? 'WORKING…' : 'SAVE SETTINGS'}</button></div>
      </div>}
    </section>
  </div>
}

