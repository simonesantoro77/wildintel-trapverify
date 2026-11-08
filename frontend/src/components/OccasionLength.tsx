import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

/** "5 days" / "1 hour": the length of a sampling occasion, for sentences. */
export function occasionDuration(t: TFunction, days: number, hours?: number | null): string {
  return hours && hours > 0
    ? t('duration.hours', { count: hours })
    : t('duration.days', { count: days })
}

interface Props {
  id: string
  days: number
  /** 0 or undefined: the occasion is measured in days. */
  hours?: number
  disabled?: boolean
  inputClass: string
  onChange: (days: number, hours: number) => void
}

/**
 * Length of a sampling occasion, in days (the default) or in hours (sub-day
 * occasions, e.g. 1 hour to verify one record per species per hour for
 * activity patterns). Switching to hours starts at 1 hour; back to days keeps
 * the number of days.
 */
export default function OccasionLength({ id, days, hours, disabled, inputClass, onChange }: Props) {
  const { t } = useTranslation()
  const inHours = !!hours && hours > 0
  const value = inHours ? hours! : days
  return (
    <div className="flex gap-2">
      <input id={id} type="number" min={1} className={inputClass} value={value} disabled={disabled}
        onChange={(e) => {
          const n = Math.max(1, Number(e.target.value) || 1)
          if (inHours) onChange(days, n)
          else onChange(n, 0)
        }} />
      <select aria-label={t('duration.unit')} className={`${inputClass} w-auto`} value={inHours ? 'hours' : 'days'}
        disabled={disabled}
        onChange={(e) => onChange(days, e.target.value === 'hours' ? 1 : 0)}>
        <option value="days">{t('duration.unit_days')}</option>
        <option value="hours">{t('duration.unit_hours')}</option>
      </select>
    </div>
  )
}
