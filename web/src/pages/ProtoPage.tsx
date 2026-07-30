import { useEffect, useMemo, useRef, useState } from 'react'
import {
  createProtoChat,
  getCustomerOverview,
  getProtoChat,
  getProtoSection,
  listProtoChats,
  sendProtoChatMessage,
} from '../api/proto'
import type {
  CustomerOverview,
  ProtoChatMessage,
  ProtoChatSession,
  ProtoHit,
} from '../api/proto'
import { useAuth } from '../hooks/useAuth'
import { useTranslation } from 'react-i18next'

type ProtoSectionDetail = Awaited<ReturnType<typeof getProtoSection>>
type Machine = CustomerOverview['machines'][number]
type SpeechRecognitionResultLike = {
  isFinal: boolean
  0: { transcript: string }
}
type SpeechRecognitionEventLike = {
  results: {
    length: number
    [index: number]: SpeechRecognitionResultLike
  }
}
type SpeechRecognitionErrorLike = { error: string }
type SpeechRecognitionLike = {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((event: SpeechRecognitionEventLike) => void) | null
  onerror: ((event: SpeechRecognitionErrorLike) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}
type SpeechRecognitionConstructor = new () => SpeechRecognitionLike

function getSpeechRecognitionConstructor(): SpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null
  const speechWindow = window as typeof window & {
    SpeechRecognition?: SpeechRecognitionConstructor
    webkitSpeechRecognition?: SpeechRecognitionConstructor
  }
  return speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition || null
}

export function ProtoPage() {
  const { user } = useAuth()
  const [overview, setOverview] = useState<CustomerOverview | null>(null)
  const [customer, setCustomer] = useState('')
  const [machineSlug, setMachineSlug] = useState('')
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [askedQuery, setAskedQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [chatSessions, setChatSessions] = useState<ProtoChatSession[]>([])
  const [activeChat, setActiveChat] = useState<ProtoChatSession | null>(null)
  const [chatMessages, setChatMessages] = useState<ProtoChatMessage[]>([])
  const [selectedAssistantId, setSelectedAssistantId] = useState<string | null>(null)
  const [lightbox, setLightbox] = useState<string | null>(null)
  const [sectionDetail, setSectionDetail] = useState<ProtoSectionDetail | null>(null)
  const [activeCite, setActiveCite] = useState<number | null>(null)
  const [loadError, setLoadError] = useState(false)

  useEffect(() => {
    getCustomerOverview()
      .then(setOverview)
      .catch(() => setLoadError(true))
  }, [])

  const customerOptions = useMemo(() => {
    if (!overview) return []
    const machineCustomers = overview.machines
      .map((machine) => machine.customer)
      .filter((value): value is string => Boolean(value))
    const names = machineCustomers.length > 0
      ? machineCustomers
      : [overview.customer.name]
    return Array.from(new Set(names)).sort((a, b) => a.localeCompare(b, 'de-CH'))
  }, [overview])

  const canChooseClient = user?.role === 'superadmin' || user?.role === 'all_clients'

  useEffect(() => {
    if (!canChooseClient && customerOptions.length === 1 && customer !== customerOptions[0]) {
      setCustomer(customerOptions[0])
      setMachineSlug('')
    }
  }, [canChooseClient, customer, customerOptions])

  const availableMachines = useMemo(() => {
    if (!overview || !customer) return []
    const hasCustomerMetadata = overview.machines.some((machine) => Boolean(machine.customer))
    const result = hasCustomerMetadata
      ? overview.machines.filter((machine) => machine.customer === customer)
      : overview.machines
    return [...result].sort((a, b) =>
      (a.model || a.folder).localeCompare(b.model || b.folder, 'de-CH'),
    )
  }, [customer, overview])

  const selectedMachine = overview?.machines.find((machine) => machine.slug === machineSlug) || null

  function changeCustomer(value: string) {
    setCustomer(value)
    setMachineSlug('')
  }

  async function enterWorkspace() {
    if (!machineSlug) return
    setWorkspaceOpen(true)
    setActiveChat(null)
    setChatMessages([])
    setSelectedAssistantId(null)
    setSectionDetail(null)
    setActiveCite(null)
    try {
      setChatSessions(await listProtoChats({ machine_slug: machineSlug }))
    } catch {
      setChatSessions([])
    }
  }

  function leaveWorkspace() {
    setWorkspaceOpen(false)
    setActiveChat(null)
    setChatMessages([])
    setChatSessions([])
    setSelectedAssistantId(null)
    setSectionDetail(null)
    setActiveCite(null)
    setQuery('')
  }

  function newChat() {
    setActiveChat(null)
    setChatMessages([])
    setSelectedAssistantId(null)
    setSectionDetail(null)
    setActiveCite(null)
    setQuery('')
  }

  async function runQuery(suggestedQuery?: string) {
    const text = (suggestedQuery ?? query).trim()
    if (!text || !selectedMachine || loading) return
    setQuery('')
    setAskedQuery(text)
    setLoading(true)
    setSectionDetail(null)
    setActiveCite(null)

    try {
      let session = activeChat
      let createdNewSession = false
      if (!session || session.machine_slug !== selectedMachine.slug) {
        session = await createProtoChat({
          machine_slug: selectedMachine.slug,
          customer: selectedMachine.customer || customer,
          title: text,
        })
        createdNewSession = true
        setActiveChat(session)
        setChatMessages([])
        setChatSessions((previous) => [
          session!,
          ...previous.filter((item) => item.id !== session!.id),
        ])
      }

      const response = await sendProtoChatMessage(session.id, { text })
      setActiveChat(response.session)
      setChatMessages((previous) => [
        ...(createdNewSession ? [] : previous),
        response.user_message,
        response.assistant_message,
      ])
      setSelectedAssistantId(response.assistant_message.id)
      setChatSessions((previous) => [
        response.session,
        ...previous.filter((item) => item.id !== response.session.id),
      ])
    } catch (error) {
      console.error(error)
      setQuery(text)
    } finally {
      setLoading(false)
    }
  }

  async function openChat(session: ProtoChatSession) {
    setLoading(true)
    setSectionDetail(null)
    setActiveCite(null)
    try {
      const detail = await getProtoChat(session.id)
      setActiveChat(detail.session)
      setChatMessages(detail.messages)
      const latestAssistant = [...detail.messages].reverse().find((message) => message.role === 'assistant')
      setSelectedAssistantId(latestAssistant?.id ?? null)
    } catch (error) {
      console.error(error)
    } finally {
      setLoading(false)
    }
  }

  async function showSection(sectionId: string | undefined, index: number) {
    if (!sectionId) return
    setActiveCite(index)
    try {
      setSectionDetail(await getProtoSection(sectionId))
    } catch (error) {
      console.error(error)
    }
  }

  if (loadError) {
    return (
      <div className="proto-state-page">
        <div className="proto-state-icon">!</div>
        <h1>Wissensdatenbank nicht erreichbar</h1>
        <p>Bitte laden Sie die Seite erneut oder versuchen Sie es später noch einmal.</p>
      </div>
    )
  }

  if (!overview) {
    return (
      <div className="proto-state-page">
        <div className="proto-loader" />
        <p>Wissensdatenbank wird vorbereitet …</p>
      </div>
    )
  }

  return (
    <div className="proto-app">
      {!workspaceOpen || !selectedMachine ? (
        <SelectionView
          customer={customer}
          customerOptions={customerOptions}
          machineSlug={machineSlug}
          machines={availableMachines}
          selectedMachine={selectedMachine}
          forceCustomerSelect={canChooseClient}
          onCustomerChange={changeCustomer}
          onMachineChange={setMachineSlug}
          onContinue={enterWorkspace}
        />
      ) : (
        <ChatWorkspace
          machine={selectedMachine}
          customer={customer}
          query={query}
          setQuery={setQuery}
          askedQuery={askedQuery}
          loading={loading}
          sessions={chatSessions}
          activeChat={activeChat}
          messages={chatMessages}
          selectedAssistantId={selectedAssistantId}
          setSelectedAssistantId={setSelectedAssistantId}
          onOpenChat={openChat}
          onNewChat={newChat}
          onChangeScope={leaveWorkspace}
          onAsk={runQuery}
          activeCite={activeCite}
          setActiveCite={setActiveCite}
          showSection={showSection}
          sectionDetail={sectionDetail}
          setSectionDetail={setSectionDetail}
          setLightbox={setLightbox}
        />
      )}

      {lightbox && (
        <div className="proto-lightbox" onClick={() => setLightbox(null)}>
          <img src={lightbox} alt="Dokumentvorschau" />
        </div>
      )}
    </div>
  )
}

function SelectionView({
  customer,
  customerOptions,
  machineSlug,
  machines,
  selectedMachine,
  forceCustomerSelect,
  onCustomerChange,
  onMachineChange,
  onContinue,
}: {
  customer: string
  customerOptions: string[]
  machineSlug: string
  machines: Machine[]
  selectedMachine: Machine | null
  forceCustomerSelect: boolean
  onCustomerChange: (value: string) => void
  onMachineChange: (value: string) => void
  onContinue: () => void
}) {
  const hasSingleCustomer = customerOptions.length === 1 && !forceCustomerSelect

  return (
    <main className="proto-select-page">
      <section className="proto-select-intro">
        <div className="proto-eyebrow">
          <span className="proto-status-dot" />
          Operative Wissensdatenbank
        </div>
        <h1>
          {hasSingleCustomer
            ? 'Welche Maschine betrifft Ihr Anliegen?'
            : 'Für welchen Einsatz benötigen Sie Hilfe?'}
        </h1>
        {!hasSingleCustomer && (
          <p>
            Wählen Sie Kunde und Maschine. So durchsuchen wir nur die Dokumentation, die zu Ihrem Einsatz gehört.
          </p>
        )}
      </section>

      <section className="proto-select-card" aria-label="Einsatz auswählen">
        <div className={`proto-select-step ${customer ? 'complete' : 'active'}`}>
          <div className="proto-step-number">{customer ? '✓' : '1'}</div>
          <div className="proto-step-content">
            <label htmlFor="proto-customer">Kunde</label>
            <span>
              {hasSingleCustomer
                ? 'Für Ihren Zugang vorausgewählt'
                : 'Für welchen Kunden sind Sie im Einsatz?'}
            </span>
            {hasSingleCustomer ? (
              <div className="proto-static-client" id="proto-customer">
                <ClientIcon />
                <span>
                  <strong>{customerOptions[0]}</strong>
                  <small>Ihr zugewiesener Kunde</small>
                </span>
                <span className="proto-static-check">✓</span>
              </div>
            ) : (
              <div className="proto-select-control">
                <ClientIcon />
                <select
                  id="proto-customer"
                  value={customer}
                  onChange={(event) => onCustomerChange(event.target.value)}
                >
                  <option value="">Kunden auswählen</option>
                  {customerOptions.map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
                <ChevronIcon />
              </div>
            )}
          </div>
        </div>

        <div className={`proto-select-connector ${customer ? 'ready' : ''}`} />

        <div className={`proto-select-step ${machineSlug ? 'complete' : customer ? 'active' : ''}`}>
          <div className="proto-step-number">{machineSlug ? '✓' : '2'}</div>
          <div className="proto-step-content">
            <label htmlFor="proto-machine">Maschine</label>
            <span>Zu welcher Maschine haben Sie eine Frage?</span>
            <SearchableMachineSelect
              id="proto-machine"
              value={machineSlug}
              machines={machines}
              disabled={!customer}
              onChange={onMachineChange}
            />
          </div>
        </div>

        {selectedMachine && (
          <div className="proto-machine-summary">
            <div className="proto-machine-summary-icon"><MachineIcon /></div>
            <div>
              <strong>{selectedMachine.model || selectedMachine.folder}</strong>
              <span>{selectedMachine.type || 'Maschine'} · {selectedMachine.hersteller}</span>
            </div>
            <div className="proto-machine-docs">
              {selectedMachine.pdfs ?? 0} PDF · {selectedMachine.sections ?? 0} Seiten
            </div>
          </div>
        )}

        <button
          type="button"
          className="proto-primary-action"
          disabled={!machineSlug}
          onClick={onContinue}
        >
          Unterhaltung starten
          <ArrowIcon />
        </button>
      </section>

      <p className="proto-select-footnote">
        <ShieldIcon /> Antworten basieren ausschließlich auf freigegebener Maschinen- und Service-Dokumentation.
      </p>
    </main>
  )
}

function SearchableMachineSelect({
  id,
  value,
  machines,
  disabled,
  onChange,
}: {
  id: string
  value: string
  machines: Machine[]
  disabled: boolean
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [menuPlacement, setMenuPlacement] = useState<'above' | 'below'>('below')
  const [menuMaxHeight, setMenuMaxHeight] = useState(300)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const selectedMachine = machines.find((machine) => machine.slug === value) || null
  const visibleMachines = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase('de-CH')
    if (!needle) return machines.slice(0, 100)
    return machines
      .filter((machine) => (
        [
          machine.model,
          machine.folder,
          machine.serial,
          machine.type,
          machine.hersteller,
        ]
          .filter(Boolean)
          .join(' ')
          .toLocaleLowerCase('de-CH')
          .includes(needle)
      ))
      .slice(0, 100)
  }, [machines, search])

  useEffect(() => {
    if (!open) return

    function updateMenuLayout() {
      const input = inputRef.current
      if (!input) return
      const rect = input.getBoundingClientRect()
      const viewportHeight = window.visualViewport?.height || window.innerHeight
      const spaceBelow = viewportHeight - rect.bottom - 14
      const spaceAbove = rect.top - 14
      const placement = spaceBelow < 220 && spaceAbove > spaceBelow ? 'above' : 'below'
      const availableSpace = placement === 'above' ? spaceAbove : spaceBelow
      setMenuPlacement(placement)
      setMenuMaxHeight(Math.max(150, Math.min(360, Math.floor(availableSpace))))
    }

    updateMenuLayout()
    window.addEventListener('resize', updateMenuLayout)
    window.visualViewport?.addEventListener('resize', updateMenuLayout)
    return () => {
      window.removeEventListener('resize', updateMenuLayout)
      window.visualViewport?.removeEventListener('resize', updateMenuLayout)
    }
  }, [open])

  function pick(machine: Machine) {
    onChange(machine.slug)
    setSearch('')
    setOpen(false)
  }

  return (
    <div className={`proto-machine-picker ${open ? 'open' : ''} ${disabled ? 'disabled' : ''}`}>
      <MachineIcon />
      <input
        ref={inputRef}
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={`${id}-options`}
        aria-autocomplete="list"
        autoComplete="off"
        disabled={disabled}
        value={open ? search : selectedMachine ? machineLabel(selectedMachine) : ''}
        placeholder={disabled ? 'Zuerst Kunden auswählen' : 'Maschine suchen …'}
        onFocus={() => {
          setSearch('')
          setOpen(true)
        }}
        onChange={(event) => {
          setSearch(event.target.value)
          setOpen(true)
        }}
        onBlur={() => window.setTimeout(() => setOpen(false), 140)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            setOpen(false)
            setSearch('')
          }
          if (event.key === 'Enter' && open && visibleMachines.length > 0) {
            event.preventDefault()
            pick(visibleMachines[0])
          }
        }}
      />
      <SearchIcon />
      {open && (
        <div
          className={`proto-machine-menu ${menuPlacement}`}
          id={`${id}-options`}
          role="listbox"
          style={{ maxHeight: `${menuMaxHeight}px` }}
          onMouseDown={(event) => event.preventDefault()}
        >
          <div className="proto-machine-menu-count">
            {visibleMachines.length === machines.length
              ? `${machines.length} Maschinen`
              : `${visibleMachines.length} von ${machines.length} Maschinen`}
          </div>
          {visibleMachines.length > 0 ? visibleMachines.map((machine) => (
            <button
              key={machine.slug}
              type="button"
              role="option"
              aria-selected={machine.slug === value}
              className={machine.slug === value ? 'selected' : ''}
              onClick={() => pick(machine)}
            >
              <span className="proto-machine-option-icon"><MachineIcon /></span>
              <span>
                <strong>{machine.model || machine.folder}</strong>
                <small>
                  {[machine.type, machine.hersteller, machine.serial].filter(Boolean).join(' · ')}
                </small>
              </span>
              {machine.slug === value && <span className="proto-machine-option-check">✓</span>}
            </button>
          )) : (
            <div className="proto-machine-menu-empty">
              Keine passende Maschine gefunden
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function machineLabel(machine: Machine): string {
  return `${machine.model || machine.folder}${machine.serial ? ` · ${machine.serial}` : ''}`
}

function ChatWorkspace({
  machine,
  customer,
  query,
  setQuery,
  askedQuery,
  loading,
  sessions,
  activeChat,
  messages,
  selectedAssistantId,
  setSelectedAssistantId,
  onOpenChat,
  onNewChat,
  onChangeScope,
  onAsk,
  activeCite,
  setActiveCite,
  showSection,
  sectionDetail,
  setSectionDetail,
  setLightbox,
}: {
  machine: Machine
  customer: string
  query: string
  setQuery: (value: string) => void
  askedQuery: string
  loading: boolean
  sessions: ProtoChatSession[]
  activeChat: ProtoChatSession | null
  messages: ProtoChatMessage[]
  selectedAssistantId: string | null
  setSelectedAssistantId: (id: string | null) => void
  onOpenChat: (session: ProtoChatSession) => void
  onNewChat: () => void
  onChangeScope: () => void
  onAsk: (query?: string) => void
  activeCite: number | null
  setActiveCite: (value: number | null) => void
  showSection: (id: string | undefined, index: number) => void
  sectionDetail: ProtoSectionDetail | null
  setSectionDetail: (section: ProtoSectionDetail | null) => void
  setLightbox: (url: string | null) => void
}) {
  const { i18n } = useTranslation()
  const [historyOpen, setHistoryOpen] = useState(false)
  const [speechSupported, setSpeechSupported] = useState(false)
  const [dictating, setDictating] = useState(false)
  const [dictationStatus, setDictationStatus] = useState('')
  const threadEndRef = useRef<HTMLDivElement | null>(null)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const dictationBaseRef = useRef('')
  const recognitionErrorRef = useRef(false)
  const suggestions = buildSuggestions(machine).slice(0, 4)

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [loading, messages])

  useEffect(() => {
    setSpeechSupported(Boolean(getSpeechRecognitionConstructor()))
    return () => {
      recognitionRef.current?.abort()
      recognitionRef.current = null
    }
  }, [])

  const selectedAssistant =
    messages.find((message) => message.id === selectedAssistantId && message.role === 'assistant') ||
    [...messages].reverse().find((message) => message.role === 'assistant') ||
    null
  const selectedAssistantIndex = selectedAssistant
    ? messages.findIndex((message) => message.id === selectedAssistant.id)
    : -1
  const selectedQuery = selectedAssistantIndex > 0
    ? [...messages.slice(0, selectedAssistantIndex)].reverse().find((message) => message.role === 'user')?.text || askedQuery
    : askedQuery

  function chooseChat(session: ProtoChatSession) {
    void onOpenChat(session)
    setHistoryOpen(false)
  }

  function beginNewChat() {
    stopDictation()
    onNewChat()
    setHistoryOpen(false)
  }

  function stopDictation() {
    recognitionRef.current?.stop()
    recognitionRef.current = null
    setDictating(false)
  }

  function toggleDictation() {
    if (dictating) {
      stopDictation()
      setDictationStatus('')
      return
    }

    const SpeechRecognition = getSpeechRecognitionConstructor()
    if (!SpeechRecognition) return

    const recognition = new SpeechRecognition()
    recognitionRef.current = recognition
    recognitionErrorRef.current = false
    dictationBaseRef.current = query.trimEnd()
    recognition.lang = i18n.language.toLowerCase().startsWith('en') ? 'en-US' : 'de-DE'
    recognition.continuous = false
    recognition.interimResults = true
    recognition.onresult = (event) => {
      let finalTranscript = ''
      let interimTranscript = ''
      for (let index = 0; index < event.results.length; index += 1) {
        const result = event.results[index]
        const transcript = result[0]?.transcript || ''
        if (result.isFinal) finalTranscript += transcript
        else interimTranscript += transcript
      }
      const spokenText = `${finalTranscript} ${interimTranscript}`.trim()
      const baseText = dictationBaseRef.current
      setQuery(baseText && spokenText ? `${baseText} ${spokenText}` : baseText || spokenText)
    }
    recognition.onerror = (event) => {
      recognitionErrorRef.current = true
      const message = event.error === 'not-allowed' || event.error === 'service-not-allowed'
        ? 'Mikrofonzugriff wurde nicht erlaubt.'
        : event.error === 'audio-capture'
          ? 'Kein Mikrofon verfügbar.'
          : event.error === 'no-speech'
            ? 'Keine Sprache erkannt. Bitte erneut versuchen.'
            : 'Spracherkennung ist gerade nicht verfügbar.'
      setDictationStatus(message)
      setDictating(false)
      recognitionRef.current = null
    }
    recognition.onend = () => {
      setDictating(false)
      recognitionRef.current = null
      if (!recognitionErrorRef.current) setDictationStatus('')
    }

    try {
      recognition.start()
      setDictating(true)
      setDictationStatus('Hört zu …')
    } catch {
      recognitionRef.current = null
      setDictating(false)
      setDictationStatus('Spracherkennung konnte nicht gestartet werden.')
    }
  }

  function submitMessage() {
    stopDictation()
    onAsk()
  }

  return (
    <div className="proto-workspace">
      {historyOpen && (
        <button
          className="proto-sidebar-backdrop"
          aria-label="Verlauf schließen"
          onClick={() => setHistoryOpen(false)}
        />
      )}

      <aside className={`proto-chat-sidebar ${historyOpen ? 'open' : ''}`}>
        <div className="proto-sidebar-mobile-head">
          <strong>Unterhaltungen</strong>
          <button type="button" onClick={() => setHistoryOpen(false)} aria-label="Verlauf schließen">×</button>
        </div>
        <button type="button" className="proto-new-chat" onClick={beginNewChat}>
          <PlusIcon /> Neuer Chat
        </button>
        <div className="proto-history-label">Verlauf</div>
        <div className="proto-history-list">
          {sessions.length === 0 ? (
            <div className="proto-history-empty">
              Ihre Unterhaltungen zu dieser Maschine erscheinen hier.
            </div>
          ) : sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              className={activeChat?.id === session.id ? 'active' : ''}
              onClick={() => chooseChat(session)}
            >
              <ChatIcon />
              <span>
                <strong>{session.title || 'Neue Unterhaltung'}</strong>
                <small>{formatDateShort(session.last_message_at || session.updated_at)}</small>
              </span>
            </button>
          ))}
        </div>
        <button type="button" className="proto-scope-card" onClick={onChangeScope}>
          <span className="proto-scope-icon"><MachineIcon /></span>
          <span>
            <small>Aktive Maschine</small>
            <strong>{machine.model || machine.folder}</strong>
            <em>{customer}</em>
          </span>
          <ChevronIcon direction="right" />
        </button>
      </aside>

      <section className="proto-conversation">
        <header className="proto-conversation-header">
          <button
            type="button"
            className="proto-mobile-menu"
            onClick={() => setHistoryOpen(true)}
            aria-label="Verlauf öffnen"
          >
            <MenuIcon />
          </button>
          <div className="proto-conversation-scope">
            <strong>{machine.model || machine.folder}</strong>
            <span>{customer} · {machine.hersteller}</span>
          </div>
          <button type="button" className="proto-change-scope" onClick={onChangeScope}>
            Maschine wechseln
          </button>
        </header>

        <div className="proto-thread-scroll">
          {messages.length === 0 && !loading ? (
            <div className="proto-chat-welcome">
              <div className="proto-welcome-mark"><MachineIcon /></div>
              <h1>Wie kann ich bei dieser Maschine helfen?</h1>
              <p>
                Ich durchsuche Handbücher, Service-Dokumentation, Konfigurationen und Schemata für <strong>{machine.model || machine.folder}</strong>.
              </p>
              <div className="proto-suggestions">
                {suggestions.map((suggestion) => (
                  <button key={suggestion} type="button" onClick={() => onAsk(suggestion)}>
                    <span>{suggestion}</span>
                    <ArrowIcon />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="proto-message-list">
              {messages.map((message) => (
                <div key={message.id} className={`proto-chat-message ${message.role}`}>
                  <div className="proto-avatar" aria-hidden="true">
                    {message.role === 'assistant' ? 'M' : (message.username || 'S').slice(0, 1).toUpperCase()}
                  </div>
                  <div className="proto-message-content">
                    <div className="proto-chat-message-meta">
                      <strong>{message.role === 'assistant' ? 'MachineGKI' : message.username || 'Sie'}</strong>
                      <span>{formatDateShort(message.created_at)}</span>
                    </div>
                    {message.role === 'assistant' ? (
                      <>
                        <div
                          className="proto-answer"
                          dangerouslySetInnerHTML={{ __html: formatAnswer(message.text) }}
                        />
                        {(message.citations?.length ?? 0) > 0 && (
                          <div className="proto-citations">
                            {message.citations!.map((citation) => (
                              <button
                                key={`${message.id}-${citation.idx}`}
                                className={`proto-cite ${selectedAssistant?.id === message.id && activeCite === citation.idx ? 'active' : ''}`}
                                onClick={() => {
                                  setSelectedAssistantId(message.id)
                                  if (citation.kind === 'page' && citation.section_id) {
                                    void showSection(citation.section_id, citation.idx)
                                  } else {
                                    setActiveCite(citation.idx)
                                    setSectionDetail(null)
                                  }
                                }}
                              >
                                [{citation.idx}] {citation.kind === 'page' ? `Seite ${citation.page}` : citation.name || citation.doc}
                              </button>
                            ))}
                          </div>
                        )}
                      </>
                    ) : (
                      <div className="proto-chat-user-text">{message.text}</div>
                    )}
                  </div>
                </div>
              ))}

              {loading && (
                <div className="proto-chat-message assistant pending">
                  <div className="proto-avatar">M</div>
                  <div className="proto-message-content">
                    <div className="proto-chat-message-meta">
                      <strong>MachineGKI</strong>
                      <span>arbeitet</span>
                    </div>
                    <div>Durchsuche die Maschinendokumentation<ThinkingDots /></div>
                  </div>
                </div>
              )}

              {selectedAssistant && (selectedAssistant.hits?.length ?? 0) > 0 && (
                <section className="proto-chat-evidence">
                  <div className="proto-chat-evidence-title">Quellen zur ausgewählten Antwort</div>
                  <div className="proto-hits">
                    {selectedAssistant.hits!.map((hit, index) => (
                      <HitCard
                        key={`${selectedAssistant.id}-${hit.label}-${hit.id}`}
                        hit={hit}
                        idx={index + 1}
                        active={activeCite === index + 1}
                        query={selectedQuery}
                        onClick={() => {
                          setSelectedAssistantId(selectedAssistant.id)
                          if (hit.label === 'ManualSection') {
                            void showSection(hit.id, index + 1)
                          } else {
                            setActiveCite(index + 1)
                            setSectionDetail(null)
                          }
                        }}
                      />
                    ))}
                  </div>
                </section>
              )}

              {sectionDetail && (
                <aside className="proto-detail">
                  <h3>
                    {sectionDetail.machine} ·{' '}
                    <a
                      href={`/api/proto/view/${sectionDetail.doc_id}?page=${sectionDetail.page}`}
                      target="_blank"
                      rel="noreferrer"
                      className="proto-hit-doc-link"
                    >
                      {sectionDetail.doc_name} ↗
                    </a>{' '}
                    · Seite {sectionDetail.page}
                  </h3>
                  <div className="proto-detail-big">
                    <img
                      src={`/api/proto/page-image/${sectionDetail.id}`}
                      alt="Dokumentseite"
                      onClick={() => setLightbox(`/api/proto/page-image/${sectionDetail.id}`)}
                    />
                  </div>
                </aside>
              )}
              <div ref={threadEndRef} />
            </div>
          )}
        </div>

        <footer className="proto-composer-wrap">
          <div className="proto-chat-composer">
            <textarea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !loading && query.trim()) {
                  event.preventDefault()
                  submitMessage()
                }
              }}
              placeholder="Fragen Sie MachineGKI …"
              rows={1}
            />
            {speechSupported && (
              <button
                type="button"
                className={`proto-dictate ${dictating ? 'active' : ''}`}
                onClick={toggleDictation}
                aria-label={dictating ? 'Diktat beenden' : 'Nachricht diktieren'}
                aria-pressed={dictating}
                title={dictating ? 'Diktat beenden' : 'Nachricht diktieren'}
              >
                {dictating ? <StopIcon /> : <MicrophoneIcon />}
              </button>
            )}
            <button
              type="button"
              className="proto-send"
              onClick={submitMessage}
              disabled={loading || !query.trim()}
              aria-label="Nachricht senden"
            >
              <SendIcon />
            </button>
          </div>
          {dictationStatus && (
            <div className={`proto-dictation-status ${dictating ? 'active' : 'error'}`} aria-live="polite">
              {dictating && <span />}
              {dictationStatus}
            </div>
          )}
          <p>
            Antworten können Ungenauigkeiten enthalten. Erkenntnisse werden automatisch in Ihrer Wissensbasis gespeichert.
          </p>
        </footer>
      </section>
    </div>
  )
}

function HitCard({
  hit,
  idx,
  active,
  query,
  onClick,
}: {
  hit: ProtoHit
  idx: number
  active: boolean
  query: string
  onClick: () => void
}) {
  const label = hit.label
  const isPage = label === 'ManualSection'
  const isImage = label === 'ImageAsset'
  const thumbUrl = isPage
    ? `/api/proto/page-image/${hit.id}`
    : isImage
      ? `/api/proto/asset-image/${hit.id}`
      : null
  let headline = ''
  let sub = ''

  if (isPage) {
    const description = hit.vision_desc || ''
    const pageSummary = description.match(/##\s*Page\s*summary\s*\n+([^\n]+)/i)
    headline = pageSummary ? pageSummary[1].trim() : firstNonEmpty(description) || firstNonEmpty(hit.text || '')
    sub = highlightSnippet(hit.text || description, query, 120)
  } else if (label === 'ConfigFile') {
    const summary = hit.summary || ''
    const purpose = summary.match(/PURPOSE:\s*([^\n]+)/i)
    headline = purpose ? purpose[1].trim() : firstNonEmpty(summary)
    sub = highlightSnippet(summary, query, 150)
  } else if (isImage) {
    headline = firstNonEmpty(hit.caption || '')
    sub = highlightSnippet(hit.caption || '', query, 140)
  }

  return (
    <article className={`proto-hit ${label.toLowerCase()} ${active ? 'active' : ''}`} onClick={onClick}>
      <div className="proto-hit-top">
        <SourceThumbnail src={thumbUrl} />
        <div className="proto-hit-meta">
          <div className="proto-hit-badge-row">
            <span className={`proto-hit-badge b-${label.toLowerCase()}`}>
              [{idx}] {label.replace('ManualSection', 'SEITE').replace('ConfigFile', 'CONFIG').replace('ImageAsset', 'BILD')}
            </span>
            <span className="proto-hit-score">{hit.score.toFixed(3)}</span>
          </div>
          <div className="proto-hit-title">{hit.machine_folder}</div>
          <a
            className="proto-hit-sub proto-hit-doc-link"
            href={`/api/proto/view/${hit.document_id}${hit.page ? `?page=${hit.page}` : ''}`}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
          >
            {hit.doc_name}{hit.page ? ` · S. ${hit.page}` : ''} ↗
          </a>
        </div>
      </div>
      {headline && <div className="proto-hit-headline">{headline}</div>}
      {sub && <div className="proto-hit-snippet" dangerouslySetInnerHTML={{ __html: sub }} />}
    </article>
  )
}

function SourceThumbnail({ src }: { src: string | null }) {
  const [failed, setFailed] = useState(false)
  if (!src || failed) {
    return <div className="proto-hit-thumb proto-hit-thumb-fallback">Quelle</div>
  }
  return <img src={src} alt="" className="proto-hit-thumb" loading="lazy" onError={() => setFailed(true)} />
}

function ClientIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></svg>
}

function MachineIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16v13H4zM8 3v4M16 3v4M8 12h.01M12 12h.01M16 12h.01M8 16h8" /></svg>
}

function ShieldIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10ZM9 12l2 2 4-4" /></svg>
}

function ChatIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" /></svg>
}

function PlusIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
}

function MenuIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
}

function SendIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m22 2-7 20-4-9-9-4zM22 2 11 13" /></svg>
}

function MicrophoneIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" /></svg>
}

function StopIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1" /></svg>
}

function SearchIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></svg>
}

function ArrowIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
}

function ChevronIcon({ direction = 'down' }: { direction?: 'down' | 'right' }) {
  return (
    <svg className={direction === 'right' ? 'right' : ''} viewBox="0 0 24 24" aria-hidden="true">
      <path d="m6 9 6 6 6-6" />
    </svg>
  )
}

function ThinkingDots() {
  return <span className="proto-thinking-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>
}

const KNOWN_GOOD: Record<string, string[]> = {
  smb: [
    'Welche Bestellnummer hat das Rillenkugellager der Umlenkrolle?',
    'Welche Baugruppen gehören zur 2.4 Mio Wartungsstufe?',
    'Wo sitzen die Schaltnetzteile T14 und T15?',
    'Wo sind die Hebepunkte für den Transport markiert?',
  ],
  netjet1: [
    'Was ist bei der Meldung „Out of Sequence“ zu prüfen?',
    'Welche GUI-Hardware-Konfiguration wird empfohlen?',
    'Wie wird der V4-Emulator eingerichtet?',
  ],
  netjet2: [
    'Welche Bitmap-Templates sind hinterlegt?',
    'Wie unterscheidet sich die Konfiguration von NetJet 1?',
    'Wie ist das Kundennetzwerk eingerichtet?',
  ],
  cmc: [
    'Wo sitzen die Heizungen und welche Schemata gibt es?',
    'Welche Seriennummern sind dokumentiert?',
    'Welche Avery-Komponenten sind aufgeführt?',
  ],
}

function buildSuggestions(machine: Machine): string[] {
  const identity = `${machine.slug} ${machine.folder}`.toLowerCase()
  let knownKey: string | null = null
  if (identity.includes('smb')) knownKey = 'smb'
  else if (identity.includes('netjet 1') || identity.includes('netjet-1')) knownKey = 'netjet1'
  else if (identity.includes('netjet 2') || identity.includes('netjet-2')) knownKey = 'netjet2'
  else if (identity.includes('cmc') || identity.includes('folieneinschlag')) knownKey = 'cmc'

  const known = knownKey ? KNOWN_GOOD[knownKey] || [] : []
  const generic = (machine.sample_docs || [])
    .filter((doc) => doc.name)
    .slice(0, 4)
    .map((doc) => `Was beschreibt „${doc.name.replace(/\.(pdf|txt|jpg|bmp)$/i, '').replace(/_/g, ' ').slice(0, 70)}“?`)
  const merged = Array.from(new Set([...known, ...generic]))
  if (merged.length === 0) {
    merged.push(
      'Welche Themen behandelt die Dokumentation?',
      'Welche Wartungsschritte sind beschrieben?',
      'Welche Ersatzteile sind dokumentiert?',
    )
  }
  return merged.slice(0, 4)
}

function formatDateShort(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('de-CH', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function firstNonEmpty(value: string): string {
  for (const line of value.split('\n')) {
    const text = line.replace(/^#+\s*/, '').replace(/\*\*/g, '').trim()
    if (text.length > 3 && !text.startsWith('##')) return text.slice(0, 100)
  }
  return ''
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function highlightSnippet(text: string, query: string, length: number): string {
  if (!text) return ''
  const terms = query
    .split(/\s+/)
    .filter((term) => term.length >= 3)
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  let snippet = text.replace(/\n+/g, ' ').trim()
  if (terms.length > 0) {
    const expression = new RegExp(`(${terms.join('|')})`, 'i')
    const match = snippet.match(expression)
    if (match?.index !== undefined) {
      const start = Math.max(0, match.index - 40)
      snippet = (start > 0 ? '…' : '') + snippet.slice(start, start + length + 40)
    } else {
      snippet = snippet.slice(0, length)
    }
    return escapeHtml(snippet).replace(new RegExp(`(${terms.join('|')})`, 'gi'), '<mark>$1</mark>')
  }
  return escapeHtml(snippet.slice(0, length))
}

function formatAnswer(answer: string): string {
  const lines = answer.split('\n')
  const output: string[] = []
  let inList: 'ul' | 'ol' | null = null
  const closeList = () => {
    if (inList) output.push(`</${inList}>`)
    inList = null
  }

  for (const raw of lines) {
    const line = raw.trimEnd()
    const unordered = line.match(/^\s*[-*]\s+(.+)$/)
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/)
    const heading = line.match(/^(#{1,6})\s+(.+)$/)
    if (unordered) {
      if (inList !== 'ul') {
        closeList()
        output.push('<ul>')
        inList = 'ul'
      }
      output.push(`<li>${renderInline(unordered[1])}</li>`)
    } else if (ordered) {
      if (inList !== 'ol') {
        closeList()
        output.push('<ol>')
        inList = 'ol'
      }
      output.push(`<li>${renderInline(ordered[1])}</li>`)
    } else if (heading) {
      closeList()
      const level = Math.min(heading[1].length + 2, 6)
      output.push(`<h${level}>${renderInline(heading[2])}</h${level}>`)
    } else if (!line.trim()) {
      closeList()
    } else {
      closeList()
      output.push(`<p>${renderInline(line)}</p>`)
    }
  }
  closeList()
  return output.join('\n')
}

function renderInline(value: string): string {
  return escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+?)\*(?=[\s.,;:!?)]|$)/g, '$1<em>$2</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[(\d+)\]/g, '<sup class="cite-ref">[$1]</sup>')
}
