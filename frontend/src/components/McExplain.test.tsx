import { beforeAll, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { McHowItWorks, McResultSummary } from './McExplain'
import type { McResult } from '../types'

beforeAll(async () => {
  await i18n.changeLanguage('en')
})

const row = (strategy: string, rounds: number, extra: object = {}) => ({
  strategy, rounds, cell_retention: 0.992, eligible: true, diff_ci_low: 0, diff_ci_high: 0, tie: false,
  ambiguous_photos: 0, mean_second_species: 0.1, best: false, chosen: false, ...extra,
})

const result: McResult = {
  camtrap_dir: '/out', strategy: 'learned_context', has_reference: true, filter_mode: 'cells',
  events_before: 26131, events_after: 19377, photos_high_in_two_before: 18062, photos_high_in_two_after: 673,
  by_species: [
    { species: 'Equus caballus', events_before: 3182, events_after: 1153, removed: 2029, saving: 0.6376,
      cells_before: 563, cells_after: 563, reference_cells: 133, threshold: 1 },
    { species: 'Lepus granatensis', events_before: 885, events_after: 885, removed: 0, saving: 0,
      cells_before: 309, cells_after: 309, reference_cells: 56, threshold: 0 },
  ],
  strategies: [
    row('raw', 9832, { eligible: false }),
    row('learned', 9565, { tie: true }),
    row('learned_context', 9554, { tie: true, best: true, chosen: true, mean_second_species: 0.0598 }),
  ],
  study_start: '2023-02-01', study_end: '2023-05-31', occasion_days: 5, gap_seconds: 60, warnings: [],
}

describe('McResultSummary', () => {
  it('explains the choice, the events removed and each species', () => {
    render(<McResultSummary r={result} retention={99} tieBreak="ambiguous" />)
    expect(screen.getByText(/Method chosen: Learned, with neighbouring photos/)).toHaveTextContent(
      'keep a true event in 99 % of the cells with the species, it leaves the fewest events to look at (9,554)')
    expect(screen.getByText(/Learned from the reference left a number that is not clearly different/)).toHaveTextContent(
      'clearest species (mean probability of the second species: 0.060)')
    expect(screen.getByText(/Events to review: 26,131 → 19,377 \(26 % fewer\)/)).toHaveTextContent(
      'Every cell with the species keeps at least one event.')
    expect(screen.getByText(/threshold 1.000; 2,029 of 3,182 events removed \(64 %\)/)).toBeInTheDocument()
    expect(screen.getByText(/not filtered: only 56 cells confirmed by the reference, at least 100 are needed/)).toBeInTheDocument()
  })
})

describe('McHowItWorks', () => {
  it('opens the explanation of the calculation', async () => {
    const user = userEvent.setup()
    render(<McHowItWorks />)
    expect(screen.queryByText(/It is the product of two parts/)).toBeNull()
    await user.click(screen.getByRole('button', { name: /How the new probability is calculated/ }))
    expect(screen.getByText(/It is the product of two parts/)).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(5)
    expect(screen.getByRole('link')).toHaveAttribute('href', 'https://wildintelproject.github.io/wildintel-trapverify/multiclassifier/')
  })
})
