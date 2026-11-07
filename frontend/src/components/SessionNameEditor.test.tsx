import { beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import SessionNameEditor from './SessionNameEditor'
import { api } from '../api'

vi.mock('../api', () => ({
  api: { renameSession: vi.fn(async (_dir: string, name: string) => ({ session_dir: '/s', session_name: name.trim() })) },
}))

beforeAll(async () => {
  await i18n.changeLanguage('en')
})

describe('SessionNameEditor', () => {
  it('renames a session with Enter and shows the new name', async () => {
    const user = userEvent.setup()
    const onRenamed = vi.fn()
    render(<SessionNameEditor sessionDir="/s" name="" fallback="no name" onRenamed={onRenamed} />)
    expect(screen.getByText('no name')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Rename the session' }))
    await user.type(screen.getByLabelText('Session name'), 'Doñana 2023 combined{Enter}')
    await waitFor(() => expect(onRenamed).toHaveBeenCalledWith('Doñana 2023 combined'))
    expect(vi.mocked(api.renameSession)).toHaveBeenCalledWith('/s', 'Doñana 2023 combined')
  })

  it('Escape cancels without saving', async () => {
    const user = userEvent.setup()
    vi.mocked(api.renameSession).mockClear()
    const onDone = vi.fn()
    render(<SessionNameEditor sessionDir="/s" name="old" fallback="" startEditing onDone={onDone} />)
    await user.type(screen.getByLabelText('Session name'), ' new{Escape}')
    expect(onDone).toHaveBeenCalled()
    expect(api.renameSession).not.toHaveBeenCalled()
  })
})
