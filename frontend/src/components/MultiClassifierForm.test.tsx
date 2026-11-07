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
        { classifier: 'ZOO', label: 'deer', photos: 5 },
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
    mcDefaults: vi.fn(async () => ({ output_dir: '/home/me/Documents/camtrap_verify/conversions' })),
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
    expect(body.output_dir).toBeNull()
    expect(body.output_name).toBeNull()
  })

  it('asks for a different name when two classifiers share one', async () => {
    const user = userEvent.setup()
    render(<MultiClassifierForm onConverted={vi.fn()} />)
    const names = screen.getAllByLabelText('Name')
    const folders = screen.getAllByLabelText('CamtrapDP folder')
    await user.type(names[0], '2023'); await user.type(folders[0], '/data/DeepFaune1.4/2023')
    await user.type(names[1], '2023'); await user.type(folders[1], '/data/YOLODonana/2023')
    expect(screen.getByRole('alert')).toHaveTextContent('Two classifiers are called "2023"')
    expect(screen.getByRole('button', { name: 'Read labels' })).toBeDisabled()
    await user.clear(names[1]); await user.type(names[1], 'YOLO')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('button', { name: 'Read labels' })).toBeEnabled()
  })

  it('works with a single AI classifier and an empty reference row', async () => {
    const user = userEvent.setup()
    vi.mocked(api.mcLabels).mockClear()
    render(<MultiClassifierForm onConverted={vi.fn()} />)
    expect(screen.getByText('Fill in a name and a folder for each classifier.')).toBeInTheDocument()
    await user.type(screen.getAllByLabelText('Name')[0], 'DF')
    await user.type(screen.getAllByLabelText('CamtrapDP folder')[0], '/data/df')
    // the reference row is left empty: it is ignored, with a note that nothing would change
    expect(screen.getByText(/With one AI classifier and no reference nothing changes/)).toBeInTheDocument()
    expect(screen.getByText('Read the labels first.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Read labels' }))
    await screen.findByLabelText('Cervus elaphus')
    expect(vi.mocked(api.mcLabels).mock.calls[0][0].classifiers).toEqual([{ name: 'DF', path: '/data/df', role: 'ai' }])
    expect(screen.getByRole('button', { name: 'Read labels again' })).toBeEnabled()
    expect(screen.getByText('Tick at least one target species.')).toBeInTheDocument()
  })

  it('sends the chosen output folder and name', async () => {
    const user = userEvent.setup()
    vi.mocked(api.mcRun).mockClear()
    render(<MultiClassifierForm onConverted={vi.fn()} />)
    await user.type(screen.getAllByLabelText('Name')[0], 'DF')
    await user.type(screen.getAllByLabelText('CamtrapDP folder')[0], '/data/df')
    await user.click(screen.getByRole('button', { name: 'Read labels' }))
    await user.click(await screen.findByLabelText('Cervus elaphus'))
    expect(screen.getByLabelText('Folder')).toHaveAttribute('placeholder', '/home/me/Documents/camtrap_verify/conversions')
    await user.type(screen.getByLabelText('Folder'), 'D:/2023_optimized')
    await user.type(screen.getByLabelText('Name of the new folder'), 'donana_2023_combined')
    await user.click(screen.getByRole('button', { name: 'Combine' }))
    await waitFor(() => expect(api.mcRun).toHaveBeenCalled())
    const body = vi.mocked(api.mcRun).mock.calls[0][0]
    expect(body.output_dir).toBe('D:/2023_optimized')
    expect(body.output_name).toBe('donana_2023_combined')
  })

  async function readTwo(user: ReturnType<typeof userEvent.setup>) {
    const names = screen.getAllByLabelText('Name')
    const folders = screen.getAllByLabelText('CamtrapDP folder')
    await user.type(names[0], 'DF'); await user.type(folders[0], '/data/df')
    await user.type(names[1], 'ZOO'); await user.type(folders[1], '/data/zoo')
    await user.click(screen.getByRole('button', { name: 'Read labels' }))
    await screen.findByLabelText('Cervus elaphus')
  }

  it('lists the labels that are not a species and sends what they mean', async () => {
    const user = userEvent.setup()
    vi.mocked(api.mcRun).mockClear()
    render(<MultiClassifierForm onConverted={vi.fn()} />)
    await readTwo(user)
    for (const sp of ['Cervus elaphus', 'Dama dama', 'Sus scrofa']) await user.click(screen.getByLabelText(sp))
    // Cervidae is the group's own name: read as that group automatically
    await waitFor(() => expect(screen.getByText(/Group label: an animal of Cervus elaphus, Dama dama/)).toBeInTheDocument())
    expect(screen.queryByLabelText('What Cervidae means')).toBeNull()
    await user.selectOptions(screen.getByLabelText('What deer means'), 'Cervidae')
    await user.click(screen.getByRole('button', { name: 'Combine' }))
    await waitFor(() => expect(api.mcRun).toHaveBeenCalled())
    expect(vi.mocked(api.mcRun).mock.calls[0][0].aliases).toEqual({ deer: 'Cervidae' })
  })

  it('keeps a group typed by hand when more species are ticked', async () => {
    const user = userEvent.setup()
    vi.mocked(api.mcRun).mockClear()
    render(<MultiClassifierForm onConverted={vi.fn()} />)
    await readTwo(user)
    await user.click(screen.getByLabelText('Cervus elaphus'))
    await user.click(screen.getByLabelText('Sus scrofa'))
    await user.type(screen.getByLabelText('Group of Cervus elaphus'), 'ungulates')
    await user.type(screen.getByLabelText('Group of Sus scrofa'), 'ungulates')
    await user.click(screen.getByLabelText('Dama dama'))
    await user.click(screen.getByRole('button', { name: 'Combine' }))
    await waitFor(() => expect(api.mcRun).toHaveBeenCalled())
    expect(vi.mocked(api.mcRun).mock.calls[0][0].groups).toEqual({
      ungulates: ['Cervus elaphus', 'Sus scrofa'], 'Dama dama': ['Dama dama'] })
  })
})
