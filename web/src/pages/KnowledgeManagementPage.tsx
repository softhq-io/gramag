import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuth } from '../hooks/useAuth'
import {
  deleteManagedDocument,
  listKnowledgeClients,
  listKnowledgeMachines,
  listManagedDocuments,
  retryManagedDocument,
  setKnowledgeClientActive,
  setKnowledgeMachineSelected,
  uploadManagedDocument,
  type KnowledgeClient,
  type KnowledgeMachine,
  type ManagedDocument,
} from '../api/knowledge'

const MAX_FILES = 20
const MAX_BYTES = 250 * 1024 * 1024
const ACCEPT = '.pdf,.txt,.jpg,.jpeg,.png,.gif,.bmp,.tif,.tiff,.pcx'
const TERMINAL = new Set(['ready', 'failed'])

interface PendingUpload {
  id: string
  name: string
  size: number
  state: 'uploading' | 'failed'
  error?: string
}

export function KnowledgeManagementPage() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'superadmin'
  const [clients, setClients] = useState<KnowledgeClient[]>([])
  const [machines, setMachines] = useState<KnowledgeMachine[]>([])
  const [documents, setDocuments] = useState<ManagedDocument[]>([])
  const [clientId, setClientId] = useState('')
  const [machineId, setMachineId] = useState('')
  const [clientQuery, setClientQuery] = useState('')
  const [machineQuery, setMachineQuery] = useState('')
  const [category, setCategory] = useState('Documents')
  const [files, setFiles] = useState<File[]>([])
  const [pendingUploads, setPendingUploads] = useState<PendingUpload[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [uploadErrors, setUploadErrors] = useState<string[]>([])
  const fileInput = useRef<HTMLInputElement>(null)

  const selectedClient = clients.find(item => item.id === clientId) || null
  const selectedMachine = machines.find(item => item.erp_id === machineId) || null
  const orderedMachines = useMemo(
    () => [...machines].sort((left, right) => (
      Number(right.selected) - Number(left.selected)
      || (left.name || left.erp_id).localeCompare(right.name || right.erp_id)
    )),
    [machines],
  )
  const selectedMachineCount = machines.filter(machine => machine.selected).length
  const uploadButtonLabel = busy
    ? 'Uploading…'
    : files.length === 0
      ? 'Choose files to continue'
      : !category.trim()
        ? 'Add a category to continue'
        : `Upload and ingest ${files.length} file${files.length === 1 ? '' : 's'}`

  const loadClients = useCallback(async () => {
    const rows = await listKnowledgeClients(Boolean(isAdmin), clientQuery)
    setClients(rows)
    if (clientId && !rows.some(item => item.id === clientId && item.active)) {
      setClientId('')
      setMachineId('')
    }
  }, [clientId, clientQuery, isAdmin])

  const loadMachines = useCallback(async () => {
    if (!clientId) {
      setMachines([])
      return
    }
    setMachines(await listKnowledgeMachines(clientId, machineQuery))
  }, [clientId, machineQuery])

  const loadDocuments = useCallback(async () => {
    if (!machineId) {
      setDocuments([])
      return
    }
    setDocuments(await listManagedDocuments(machineId))
  }, [machineId])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      loadClients().catch(err => setError(errorMessage(err)))
    }, 200)
    return () => window.clearTimeout(timer)
  }, [loadClients])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      loadMachines().catch(err => setError(errorMessage(err)))
    }, 200)
    return () => window.clearTimeout(timer)
  }, [loadMachines])

  useEffect(() => {
    loadDocuments().catch(err => setError(errorMessage(err)))
  }, [loadDocuments])

  const hasActiveJobs = useMemo(
    () => documents.some(document => !TERMINAL.has(document.status)),
    [documents],
  )

  useEffect(() => {
    if (!machineId || !hasActiveJobs) return
    const timer = window.setInterval(() => {
      loadDocuments().catch(err => setError(errorMessage(err)))
    }, 2000)
    return () => window.clearInterval(timer)
  }, [hasActiveJobs, loadDocuments, machineId])

  async function toggleClient(client: KnowledgeClient) {
    setError('')
    try {
      await setKnowledgeClientActive(client.id, !client.active)
      if (client.active && client.id === clientId) {
        setClientId('')
        setMachineId('')
      }
      await loadClients()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function toggleMachine(machine: KnowledgeMachine) {
    if (!clientId) return
    setError('')
    try {
      await setKnowledgeMachineSelected(clientId, machine.erp_id, !machine.selected)
      if (machine.selected && machine.erp_id === machineId) setMachineId('')
      await loadMachines()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  function chooseFiles(incoming: FileList | null) {
    const incomingFiles = Array.from(incoming || [])
    const selected = incomingFiles.slice(0, MAX_FILES)
    const tooLarge = selected.filter(file => file.size > MAX_BYTES)
    setUploadErrors([
      ...(incomingFiles.length > MAX_FILES ? [`Only the first ${MAX_FILES} files were selected.`] : []),
      ...tooLarge.map(file => `${file.name} exceeds 250 MB.`),
    ])
    setFiles(selected.filter(file => file.size <= MAX_BYTES))
  }

  async function uploadBatch() {
    if (!machineId || !category.trim() || files.length === 0) return
    const batch = files.map(file => ({
      file,
      card: {
        id: crypto.randomUUID(),
        name: file.name,
        size: file.size,
        state: 'uploading' as const,
      },
    }))
    setPendingUploads(batch.map(item => item.card))
    setBusy(true)
    setError('')
    setUploadErrors([])
    let index = 0
    const worker = async () => {
      while (index < batch.length) {
        const item = batch[index++]
        try {
          const document = await uploadManagedDocument(machineId, category.trim(), item.file)
          setPendingUploads(current => current.filter(card => card.id !== item.card.id))
          setDocuments(current => [document, ...current.filter(row => row.id !== document.id)])
        } catch (err) {
          const message = errorMessage(err)
          setPendingUploads(current => current.map(card => (
            card.id === item.card.id ? { ...card, state: 'failed', error: message } : card
          )))
        }
      }
    }
    try {
      await Promise.all(Array.from({ length: Math.min(3, batch.length) }, worker))
      setFiles([])
      if (fileInput.current) fileInput.current.value = ''
      await loadDocuments()
      await loadMachines()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function retry(document: ManagedDocument) {
    try {
      await retryManagedDocument(document.id)
      await loadDocuments()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function remove(document: ManagedDocument) {
    if (!window.confirm(`Permanently delete “${document.name}” and its searchable content?`)) return
    try {
      await deleteManagedDocument(document.id)
      await loadDocuments()
      await loadMachines()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <div className="knowledge-page">
      <header className="knowledge-header">
        <div>
          <span className="knowledge-eyebrow">Knowledge administration</span>
          <h1>Clients, machines and documents</h1>
          <p>Activate CRM clients, choose their machines, and ingest individual files.</p>
        </div>
      </header>

      {error && <div className="knowledge-alert" role="alert">{error}</div>}

      <div className="knowledge-columns">
        <section className="knowledge-panel">
          <div className="knowledge-panel-heading">
            <div className="knowledge-panel-title">
              <div><span>1</span><strong>Clients</strong></div>
              <small>{clients.length} shown</small>
            </div>
            <input value={clientQuery} onChange={event => setClientQuery(event.target.value)} placeholder="Search CRM clients" />
          </div>
          <div className="knowledge-list">
            {clients.map(client => (
              <div className={`knowledge-row ${client.id === clientId ? 'selected' : ''} ${client.active ? '' : 'inactive'}`} key={client.id}>
                <button className="knowledge-row-main" disabled={busy || !client.active} onClick={() => {
                  setClientId(client.id)
                  setMachineId('')
                }}>
                  <strong title={client.name || client.id}>{client.name || client.id}</strong>
                  <small title={`CRM client ${client.id}`}>
                    CRM {client.id} · {client.machine_count} machines · {client.selected_machine_count} selected
                  </small>
                </button>
                {isAdmin && (
                  <button
                    aria-label={`${client.active ? 'Deactivate' : 'Activate'} ${client.name || client.id}`}
                    disabled={busy}
                    className={`knowledge-toggle ${client.active ? 'on' : ''}`}
                    onClick={() => toggleClient(client)}
                  >
                    {client.active ? 'Active' : 'Activate'}
                  </button>
                )}
              </div>
            ))}
            {clients.length === 0 && <div className="knowledge-empty">No clients found.</div>}
          </div>
        </section>

        <section className="knowledge-panel">
          <div className="knowledge-panel-heading">
            <div className="knowledge-panel-title">
              <div><span>2</span><strong>Machines</strong></div>
              <small>{selectedMachineCount} selected</small>
            </div>
            <input disabled={!clientId} value={machineQuery} onChange={event => setMachineQuery(event.target.value)} placeholder="Search machines" />
          </div>
          <div className="knowledge-list">
            {!selectedClient && <div className="knowledge-empty">Choose an active client.</div>}
            {orderedMachines.map(machine => (
              <div className={`knowledge-row ${machine.erp_id === machineId ? 'selected' : ''}`} key={machine.erp_id}>
                <button className="knowledge-row-main" disabled={busy || !machine.selected} onClick={() => setMachineId(machine.erp_id)}>
                  <strong className="knowledge-machine-name" title={machine.name || machine.erp_id}>{machine.name || machine.erp_id}</strong>
                  <small>{machine.serial || machine.number || (machine.legacy ? 'Legacy grouped machine' : `CRM ${machine.erp_id}`)} · {machine.ready_document_count} ready</small>
                </button>
                <button
                  aria-label={`${machine.selected ? 'Deselect' : 'Select'} ${machine.name || machine.erp_id}`}
                  disabled={busy}
                  className={`knowledge-toggle ${machine.selected ? 'on' : ''}`}
                  onClick={() => toggleMachine(machine)}
                >
                  {machine.selected ? 'Selected' : 'Select'}
                </button>
              </div>
            ))}
          </div>
        </section>

        <section className="knowledge-panel knowledge-documents-panel">
          <div className="knowledge-panel-heading">
            <div className="knowledge-panel-title">
              <div><span>3</span><strong>Documents</strong></div>
              <small>{selectedMachine ? `${documents.length} total` : ''}</small>
            </div>
            <small title={selectedMachine?.name || undefined}>{selectedMachine?.name || 'Choose a selected machine'}</small>
          </div>

          {selectedMachine && (
            <div className="knowledge-upload">
              <label>
                Category
                <input list="knowledge-categories" value={category} maxLength={80} onChange={event => setCategory(event.target.value)} />
              </label>
              <datalist id="knowledge-categories">
                {Array.from(new Set(documents.map(document => document.category))).map(value => <option value={value} key={value} />)}
              </datalist>
              <label
                className="knowledge-dropzone"
                onDragOver={event => event.preventDefault()}
                onDrop={event => {
                  event.preventDefault()
                  chooseFiles(event.dataTransfer.files)
                }}
              >
                <input ref={fileInput} type="file" multiple accept={ACCEPT} onChange={event => chooseFiles(event.target.files)} />
                <strong>{files.length ? `${files.length} file${files.length === 1 ? '' : 's'} selected` : 'Choose up to 20 files'}</strong>
                <span>PDF, images or TXT · maximum 250 MB each</span>
              </label>
              <button className="knowledge-upload-button" disabled={busy || !category.trim() || files.length === 0} onClick={uploadBatch}>
                {uploadButtonLabel}
              </button>
              {uploadErrors.map(message => <div className="knowledge-file-error" key={message}>{message}</div>)}
            </div>
          )}

          <div className="knowledge-document-list">
            {selectedMachine && documents.length === 0 && pendingUploads.length === 0 && <div className="knowledge-empty">No documents yet.</div>}
            {pendingUploads.map(upload => (
              <article className="knowledge-document" key={upload.id}>
                <div className="knowledge-document-title">
                  <div><strong>{upload.name}</strong><small>{category.trim()} · {formatBytes(upload.size)}</small></div>
                  <span className={`knowledge-status ${upload.state === 'failed' ? 'failed' : 'processing'}`}>
                    {upload.state}
                  </span>
                </div>
                {upload.state === 'uploading' && (
                  <div className="knowledge-progress">
                    <div><span style={{ width: '8%' }} /></div>
                    <small>Uploading original…</small>
                  </div>
                )}
                {upload.error && <div className="knowledge-file-error">{upload.error}</div>}
              </article>
            ))}
            {documents.map(document => (
              <article className={`knowledge-document ${document.status === 'ready' ? 'compact' : ''}`} key={document.id}>
                <div className="knowledge-document-title">
                  <div>
                    <strong title={document.name}>{document.name}</strong>
                    <small>{document.category} · {formatBytes(document.size)} · {document.source === 'upload' ? 'Uploaded' : 'Legacy SharePoint'}</small>
                  </div>
                  <span className={`knowledge-status ${document.status}`}>{document.status}</span>
                </div>
                {!TERMINAL.has(document.status) && document.status !== 'deleting' && (
                  <div className="knowledge-progress">
                    <div><span style={{ width: progressWidth(document) }} /></div>
                    <small>{phaseLabel(document)}{document.queue_position ? ` · queue position ${document.queue_position}` : ''}{document.progress_total ? ` · ${document.progress_current}/${document.progress_total}` : ''}</small>
                  </div>
                )}
                {document.error_message && <div className="knowledge-file-error">{document.error_message}</div>}
                <div className="knowledge-document-actions">
                  {document.status === 'ready' && <a aria-label={`Open ${document.name}`} href={`/api/proto/view/${encodeURIComponent(document.id)}`} target="_blank" rel="noreferrer">Open</a>}
                  {document.status === 'ready' && <a aria-label={`Download ${document.name}`} href={`/api/proto/document/${encodeURIComponent(document.id)}?download=true`}>Download</a>}
                  {document.status === 'failed' && document.phase !== 'delete_failed' && <button aria-label={`Retry ${document.name}`} onClick={() => retry(document)}>Retry</button>}
                  <button aria-label={`Permanently delete ${document.name}`} className="danger" disabled={document.status === 'deleting'} onClick={() => remove(document)}>Delete</button>
                </div>
              </article>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}

function formatBytes(value: number) {
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${value} B`
}

function progressWidth(document: ManagedDocument) {
  if (!document.progress_total) return document.status === 'processing' ? '18%' : '5%'
  return `${Math.max(3, Math.min(100, document.progress_current / document.progress_total * 100))}%`
}

function phaseLabel(document: ManagedDocument) {
  return document.phase.replaceAll('_', ' ').replace(/^./, value => value.toUpperCase())
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'The operation failed.'
}
