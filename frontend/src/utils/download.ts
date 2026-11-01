/** Download a file served by the backend, keeping the server's file name. */
export async function downloadFile(url: string, fallbackName: string): Promise<void> {
  const res = await fetch(url)
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    let message = text
    try { message = JSON.parse(text).detail ?? text } catch { /* not JSON */ }
    throw new Error(message)
  }
  const blob = await res.blob()
  const cd = res.headers.get('content-disposition') ?? ''
  const match = cd.match(/filename="?([^"]+)"?/)
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = match ? match[1] : fallbackName
  a.click()
  URL.revokeObjectURL(objectUrl)
}
