import { beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import MultiClassifierForm from './MultiClassifierForm'
import { api } from '../api'

vi.mock('../api', () => ({
  api: {
    mcLabels: vi.fn(async () => ({
      date_min: '2023-02-01', date_max: '2023-05-31', n_photos: 1000, families: ['Cervidae'], remote_paths: 0,
      labels: [
        { classifier: 'DF', label: 'Cervus elaphus', photos: 500 },
        { classifier: 'DF', label: 'Dama dama', photos: 100 },
        { classifier: 'ZOO', label: 'Cervidae', photos: 50 },
        { classifier: 'DF', label: 'Sus scrofa', photos: 80 },
      ],
    })),
    mcGroups: vi.fn(async (species: string[]) => ({
      groups: Object.fromEntries(
        species.includes('Dama dama') && species.includes('Cervus elaphus')
          ? [['Cervidae', ['Cervus elaphus', 'Dama dama']], ...species.filter(s => !s.includes(' ') || s === 'Sus scrofa').map(s => [s, [s]])]
          : species.map(s => [s, [s]]),
      ),
    })),
    mcRun: vi.fn(async () => ({ job_id: 'j1', out_dir: '/tmp/x' })),
    mcJob: vi.fn(async () => ({ status: 'running', log: [], out_dir: '/tmp/x' })),
  },
}))

beforeAll(async () => {
  await i18n.changeLanguage('en')
})

describe('MultiClassifierForm', () => {
  it('sends the chosen species grouped as proposed', async () => {
    const user = userEvent.setup()
    render(<MultiClassifierForm onConverted={vi.fn()} />)

    const names = screen.getAllByLabelText('Name')
    const folders = screen.getAllByLabelText('CamtrapDP folder')
    await user.type(names[0], 'DF'); await user.type(folders[0], '/data/df')
    await user.type(names[1], 'ZOO'); await user.type(folders[1], '/data/zoo')
    await user.selectOptions(screen.getAllByLabelText('Role')[1], 'reference')

    expect(screen.getByRole('button', { name: 'Combine' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Read labels' }))
    await screen.findByLabelText('Cervus elaphus')

    await user.click(screen.getByLabelText('Cervus elaphus'))
    await user.click(screen.getByLabelText('Dama dama'))
    await user.click(screen.getByLabelText('Sus scrofa'))
    await waitFor(() => expect(screen.getByText(/Cervidae \(Cervus elaphus, Dama dama\)/)).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Combine' }))
    await waitFor(() => expect(api.mcRun).toHaveBeenCalled())
    const body = vi.mocked(api.mcRun).mock.calls[0][0]
    expect(body.groups).toEqual({ Cervidae: ['Cervus elaphus', 'Dama dama'], 'Sus scrofa': ['Sus scrofa'] })
    expect(body.date_start).toBe('2023-02-01')
    expect(body.filter_mode).toBe('cells')
    expect(body.classifiers.map(c => c.role)).toEqual(['ai', 'reference'])
  })
})
