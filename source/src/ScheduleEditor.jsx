import { useEffect, useState } from 'react'

const DAYS = [
  ['monday', 'Monday'],
  ['tuesday', 'Tuesday'],
  ['wednesday', 'Wednesday'],
  ['thursday', 'Thursday'],
  ['friday', 'Friday'],
  ['saturday', 'Saturday'],
  ['sunday', 'Sunday'],
]

export default function ScheduleEditor({ mode, schedule, onClose, onSaved }) {
  const [time, setTime] = useState('06:00')
  const [day, setDay] = useState('sunday')
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const config = mode === 'daily' ? schedule?.daily : schedule?.weekly
    setTime(config?.time || (mode === 'daily' ? '06:00' : '02:00'))
    setDay(schedule?.weekly?.day || 'sunday')
    setEnabled(config?.enabled ?? true)
  }, [mode, schedule])

  function openTimePicker(event) {
    if (!enabled) return
    try {
      event.currentTarget.showPicker?.()
    } catch {
      // Browsers without showPicker still retain normal type="time" behavior.
    }
  }

  async function save() {
    setSaving(true)
    setError('')
    try {
      const next = {
        timezone: schedule?.timezone || 'America/New_York',
        daily: {
          enabled: mode === 'daily' ? enabled : (schedule?.daily?.enabled ?? true),
          time: mode === 'daily' ? time : (schedule?.daily?.time || '06:00'),
        },
        weekly: {
          enabled: mode === 'weekly' ? enabled : (schedule?.weekly?.enabled ?? true),
          day: mode === 'weekly' ? day : (schedule?.weekly?.day || 'sunday'),
          time: mode === 'weekly' ? time : (schedule?.weekly?.time || '02:00'),
        },
      }

      const response = await fetch('/api/config/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data?.detail || 'Unable to save schedule.')
      onSaved(data)
      onClose()
    } catch (err) {
      setError(err.message || 'Unable to save schedule.')
    } finally {
      setSaving(false)
    }
  }

  const label = mode === 'daily' ? 'Daily' : 'Weekly'

  return (
    <div className="schedule-overlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="schedule-dialog" role="dialog" aria-modal="true" aria-label={`${label} backup schedule`}>
        <header className="schedule-header">
          <div>
            <span className="schedule-kicker">{label.toUpperCase()} PASSAGE</span>
            <h2>Set the {label.toLowerCase()} schedule.</h2>
            <p>{mode === 'daily' ? 'Choose the time JAYN Vault should run the daily backup.' : 'Choose the day and time for the weekly backup.'}</p>
          </div>
          <button className="schedule-close" type="button" onClick={onClose} aria-label="Close">×</button>
        </header>

        <div className="schedule-body">
          {mode === 'weekly' && (
            <label className={`schedule-field${enabled ? '' : ' is-disabled'}`}>
              <span>DAY</span>
              <select value={day} onChange={(event) => setDay(event.target.value)} disabled={!enabled}>
                {DAYS.map(([value, text]) => <option value={value} key={value}>{text}</option>)}
              </select>
            </label>
          )}

          <label className={`schedule-field${enabled ? '' : ' is-disabled'}`}>
            <span>TIME</span>
            <input
              className="schedule-time-input"
              type="time"
              value={time}
              onChange={(event) => setTime(event.target.value)}
              onClick={openTimePicker}
              disabled={!enabled}
              aria-disabled={!enabled}
              aria-label={`${label} backup time`}
            />
          </label>

          <label className="schedule-toggle">
            <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
            <span className="schedule-toggle-ui" />
            <span><b>{label} backup enabled</b><small>{enabled ? 'JAYN Vault will run this schedule automatically.' : 'This automatic schedule is paused. Turn it back on to edit the schedule.'}</small></span>
          </label>

          <div className="schedule-timezone">
            <span>TIME ZONE</span>
            <strong>{schedule?.timezone || 'America/New_York'}</strong>
          </div>

          {error && <div className="schedule-error">{error}</div>}
        </div>

        <footer className="schedule-footer">
          <button type="button" className="schedule-cancel" onClick={onClose}>Cancel</button>
          <button type="button" className="schedule-save" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save schedule'}</button>
        </footer>
      </section>
    </div>
  )
}
