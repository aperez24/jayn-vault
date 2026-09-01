import { useEffect, useMemo, useState } from 'react'

const DAYS = [
  ['monday', 'Monday'],
  ['tuesday', 'Tuesday'],
  ['wednesday', 'Wednesday'],
  ['thursday', 'Thursday'],
  ['friday', 'Friday'],
  ['saturday', 'Saturday'],
  ['sunday', 'Sunday'],
]

const HOURS = Array.from({ length: 12 }, (_, index) => String(index + 1))
const MINUTES = Array.from({ length: 60 }, (_, index) => String(index).padStart(2, '0'))
const FALLBACK_TIMEZONES = [
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Phoenix',
  'America/Los_Angeles',
  'America/Anchorage',
  'Pacific/Honolulu',
  'UTC',
]

function detectedTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/New_York'
  } catch {
    return 'America/New_York'
  }
}

function availableTimezones(current, device) {
  let zones = FALLBACK_TIMEZONES
  try {
    if (typeof Intl.supportedValuesOf === 'function') zones = Intl.supportedValuesOf('timeZone')
  } catch {}
  return Array.from(new Set([device, current, ...zones].filter(Boolean))).sort((a, b) => a.localeCompare(b))
}

function splitTime(value = '06:00') {
  const [rawHour = '06', rawMinute = '00'] = value.split(':')
  const hour24 = Math.max(0, Math.min(23, Number(rawHour) || 0))
  const minute = Math.max(0, Math.min(59, Number(rawMinute) || 0))
  const period = hour24 >= 12 ? 'PM' : 'AM'
  const hour12 = hour24 % 12 || 12
  return { hour: String(hour12), minute: String(minute).padStart(2, '0'), period }
}

function joinTime(hour, minute, period) {
  let hour24 = Number(hour) % 12
  if (period === 'PM') hour24 += 12
  return `${String(hour24).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
}

function formatTime(value) {
  const { hour, minute, period } = splitTime(value)
  return `${hour}:${minute} ${period}`
}

export default function ScheduleEditor({ mode, schedule, onClose, onSaved }) {
  const deviceTimezone = useMemo(() => detectedTimezone(), [])
  const [time, setTime] = useState('06:00')
  const [day, setDay] = useState('sunday')
  const [timezone, setTimezone] = useState(deviceTimezone)
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const config = mode === 'daily' ? schedule?.daily : schedule?.weekly
    setTime(config?.time || (mode === 'daily' ? '06:00' : '02:00'))
    setDay(schedule?.weekly?.day || 'sunday')
    setTimezone(schedule?.timezone || deviceTimezone)
    setEnabled(config?.enabled ?? true)
  }, [mode, schedule, deviceTimezone])

  const timeParts = useMemo(() => splitTime(time), [time])
  const timezoneOptions = useMemo(() => availableTimezones(timezone, deviceTimezone), [timezone, deviceTimezone])
  const otherSchedule = mode === 'daily' ? schedule?.weekly : schedule?.daily
  const conflict = Boolean(enabled && otherSchedule?.enabled && otherSchedule?.time === time)

  function updateTime(part, value) {
    const next = { ...timeParts, [part]: value }
    setTime(joinTime(next.hour, next.minute, next.period))
    setError('')
  }

  async function save() {
    if (conflict) {
      setError(`This time conflicts with the enabled ${mode === 'daily' ? 'weekly' : 'daily'} schedule. Choose a different time.`)
      return
    }

    setSaving(true)
    setError('')
    try {
      const next = {
        timezone,
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

          <div className={`schedule-field${enabled ? '' : ' is-disabled'}`}>
            <span>TIME</span>
            <div className="schedule-time-picker" aria-label={`${label} backup time`}>
              <label><small>HOUR</small><select value={timeParts.hour} onChange={(event) => updateTime('hour', event.target.value)} disabled={!enabled}>{HOURS.map((hour) => <option value={hour} key={hour}>{hour}</option>)}</select></label>
              <span className="schedule-time-separator">:</span>
              <label><small>MINUTE</small><select value={timeParts.minute} onChange={(event) => updateTime('minute', event.target.value)} disabled={!enabled}>{MINUTES.map((minute) => <option value={minute} key={minute}>{minute}</option>)}</select></label>
              <label><small>PERIOD</small><select value={timeParts.period} onChange={(event) => updateTime('period', event.target.value)} disabled={!enabled}><option value="AM">AM</option><option value="PM">PM</option></select></label>
            </div>
            <small className="schedule-time-summary">Scheduled for {formatTime(time)}</small>
          </div>

          {conflict && <div className="schedule-conflict"><strong>SCHEDULE CONFLICT</strong><span>{formatTime(time)} is already used by the enabled {mode === 'daily' ? 'weekly' : 'daily'} backup. Choose a different time.</span></div>}

          <label className="schedule-toggle">
            <input type="checkbox" checked={enabled} onChange={(event) => { setEnabled(event.target.checked); setError('') }} />
            <span className="schedule-toggle-ui" />
            <span><b>{label} backup enabled</b><small>{enabled ? 'JAYN Vault will run this schedule automatically.' : 'This automatic schedule is paused. Turn it back on to edit the schedule.'}</small></span>
          </label>

          <label className="schedule-timezone">
            <span>TIME ZONE</span>
            <select value={timezone} onChange={(event) => { setTimezone(event.target.value); setError('') }}>
              {timezoneOptions.map((zone) => <option value={zone} key={zone}>{zone}{zone === deviceTimezone ? ' — Device' : ''}</option>)}
            </select>
            <small>Applies to both Daily and Weekly schedules.</small>
          </label>

          {error && <div className="schedule-error">{error}</div>}
        </div>

        <footer className="schedule-footer">
          <button type="button" className="schedule-cancel" onClick={onClose}>Cancel</button>
          <button type="button" className="schedule-save" onClick={save} disabled={saving || conflict}>{saving ? 'Saving…' : 'Save schedule'}</button>
        </footer>
      </section>
    </div>
  )
}
