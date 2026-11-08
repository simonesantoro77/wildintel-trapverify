import { afterAll, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import OccasionLength, { occasionDuration } from './OccasionLength'

afterAll(async () => { await i18n.changeLanguage('en') })

describe('OccasionLength', () => {
  it('switches between days and hours', async () => {
    await i18n.changeLanguage('en')
    const user = userEvent.setup()
    const onChange = vi.fn()
    const { rerender } = render(<OccasionLength id="o" days={5} hours={0} inputClass="" onChange={onChange} />)
    expect(screen.getByRole('spinbutton')).toHaveValue(5)
    await user.selectOptions(screen.getByRole('combobox'), 'hours')
    expect(onChange).toHaveBeenLastCalledWith(5, 1)          // hours start at 1, days kept
    rerender(<OccasionLength id="o" days={5} hours={1} inputClass="" onChange={onChange} />)
    expect(screen.getByRole('spinbutton')).toHaveValue(1)
    await user.selectOptions(screen.getByRole('combobox'), 'days')
    expect(onChange).toHaveBeenLastCalledWith(5, 0)
  })

  it('writes the duration in words, with plurals', async () => {
    await i18n.changeLanguage('en')
    expect(occasionDuration(i18n.t, 5, 0)).toBe('5 days')
    expect(occasionDuration(i18n.t, 5, 1)).toBe('1 hour')
    await i18n.changeLanguage('pl')
    expect(occasionDuration(i18n.t, 5, 0)).toBe('5 dni')
    expect(occasionDuration(i18n.t, 1, 2)).toBe('2 godziny')
    await i18n.changeLanguage('es')
    expect(occasionDuration(i18n.t, 1, 0)).toBe('1 día')
  })
})
