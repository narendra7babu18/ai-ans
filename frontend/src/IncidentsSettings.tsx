import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import { Database, FileText, LoaderCircle, Trash2, Upload, X } from 'lucide-react'

export type IncidentFile = {
  id: string
  filename: string
  uploaded_at: string
  chunks: number
}

type Props = {
  open: boolean
  accessToken: string
  onClose: () => void
  onFilesChanged: (files: IncidentFile[]) => void
}

type ErrorResponse = { detail?: string }

async function responseError(response: Response) {
  try {
    const body = (await response.json()) as ErrorResponse
    return body.detail ?? `Request failed (HTTP ${response.status}).`
  } catch {
    return `Request failed (HTTP ${response.status}).`
  }
}

export default function IncidentsSettings({
  open,
  accessToken,
  onClose,
  onFilesChanged,
}: Props) {
  const [files, setFiles] = useState<IncidentFile[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const headers = useCallback(() => ({
    Authorization: `Bearer ${accessToken}`,
  }), [accessToken])

  const loadFiles = useCallback(async () => {
    const response = await fetch('/api/v1/incidents', { headers: headers() })
    if (!response.ok) throw new Error(await responseError(response))
    const payload = (await response.json()) as { files: IncidentFile[] }
    setFiles(payload.files)
    setError('')
    onFilesChanged(payload.files)
  }, [headers, onFilesChanged])

  useEffect(() => {
    if (!open) return
    let active = true
    const load = async () => {
      try {
        await loadFiles()
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : 'Could not load incident files.')
      }
    }
    void load()
    return () => { active = false }
  }, [open, loadFiles])

  const uploadFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return
    setError('')
    setNotice('')
    setBusy('upload')
    try {
      const body = new FormData()
      body.append('file', file)
      const response = await fetch('/api/v1/incidents', {
        method: 'POST',
        headers: headers(),
        body,
      })
      if (!response.ok) throw new Error(await responseError(response))
      const indexed = (await response.json()) as { filename: string; chunks: number }
      setNotice(`Indexed ${indexed.filename} into ${indexed.chunks} searchable chunks.`)
      await loadFiles()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not index this incident file.')
    } finally {
      setBusy('')
    }
  }

  const deleteFile = async (file: IncidentFile) => {
    if (!window.confirm(`Remove "${file.filename}" from incident search?`)) return
    setError('')
    setNotice('')
    setBusy(file.id)
    try {
      const response = await fetch(`/api/v1/incidents/${encodeURIComponent(file.id)}`, {
        method: 'DELETE',
        headers: headers(),
      })
      if (!response.ok) throw new Error(await responseError(response))
      setNotice(`Removed ${file.filename} from incident search.`)
      await loadFiles()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not remove this incident file.')
    } finally {
      setBusy('')
    }
  }

  if (!open) return null

  return (
    <div className="utility-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section className="utility-dialog incident-dialog" role="dialog" aria-modal="true" aria-labelledby="incidents-title">
        <button className="icon-button utility-close" aria-label="Close incident settings" onClick={onClose}><X size={17} /></button>
        <span className="settings-eyebrow"><Database size={13} /> PRIVATE INCIDENT LIBRARY</span>
        <h2 id="incidents-title">IT Incident RAG</h2>
        <p>Upload CSV, JSON, PDF, or UTF-8 text incident records. AI ANS indexes them with Hugging Face embeddings and searches your account’s ChromaDB collection for relevant context.</p>
        <p className="incident-privacy-note">Files persist unencrypted on the backend data directory and are isolated to your account. Relevant excerpts are sent to your selected AI provider only when Incident RAG is enabled. Upload only data approved for both uses.</p>

        <input
          ref={fileInputRef}
          className="incident-file-input"
          type="file"
          accept=".csv,.json,.pdf,.txt,text/csv,application/json,application/pdf,text/plain"
          onChange={(event) => void uploadFile(event)}
        />
        <button
          className="settings-primary-button incident-upload"
          disabled={Boolean(busy)}
          onClick={() => fileInputRef.current?.click()}
        >
          {busy === 'upload' ? <LoaderCircle size={15} className="spin" /> : <Upload size={15} />}
          Upload incident file
        </button>

        <div className="incident-file-list" aria-live="polite">
          {files.length === 0 ? (
            <p className="settings-empty">No incident files indexed yet. Files must be smaller than 5 MB.</p>
          ) : files.map((file) => (
            <article className="incident-file-card" key={file.id}>
              <FileText size={16} />
              <span><strong>{file.filename}</strong><small>{file.chunks} searchable chunks</small></span>
              <button
                className="settings-icon-button danger"
                aria-label={`Remove ${file.filename}`}
                title="Remove from incident search"
                disabled={Boolean(busy)}
                onClick={() => void deleteFile(file)}
              >
                {busy === file.id ? <LoaderCircle size={14} className="spin" /> : <Trash2 size={14} />}
              </button>
            </article>
          ))}
        </div>
        {error && <div className="settings-message settings-error" role="alert">{error}</div>}
        {notice && <div className="settings-message" role="status">{notice}</div>}
        <footer className="providers-footer">
          <span><Database size={13} /> Enable “Incident RAG” in chat to search these files.</span>
          <button className="settings-small-button" onClick={onClose}>Done</button>
        </footer>
      </section>
    </div>
  )
}
