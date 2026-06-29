import { useEffect, useMemo, useState } from 'react'
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

type Mode = 'site' | 'machine' | 'ask'
type ProtoSectionDetail = Awaited<ReturnType<typeof getProtoSection>>

export function ProtoPage() {
  const [overview, setOverview] = useState<CustomerOverview | null>(null)
  const [mode, setMode] = useState<Mode>('site')
  const [selected, setSelected] = useState<string | null>(null) // machine slug
  const [query, setQuery] = useState('')
  const [askedQuery, setAskedQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [chatSessions, setChatSessions] = useState<ProtoChatSession[]>([])
  const [activeChat, setActiveChat] = useState<ProtoChatSession | null>(null)
  const [chatMessages, setChatMessages] = useState<ProtoChatMessage[]>([])
  const [selectedAssistantId, setSelectedAssistantId] = useState<string | null>(null)
  const [deep, setDeep] = useState(false)
  const [lightbox, setLightbox] = useState<string | null>(null)
  const [sectionDetail, setSectionDetail] = useState<ProtoSectionDetail | null>(null)
  const [activeCite, setActiveCite] = useState<number | null>(null)
  const [kunde, setKunde] = useState<string>('Alle')
  const [hersteller, setHersteller] = useState<string>('Alle')
  const [sonstigesOpen, setSonstigesOpen] = useState(false)

  useEffect(() => {
    getCustomerOverview().then(setOverview).catch(console.error)
  }, [])

  const selMachine = overview?.machines.find((m) => m.slug === selected) || null

  const { primary, sonstiges, herstellerOptions, kundenOptions } = useMemo(() => {
    if (!overview) {
      return { primary: [], sonstiges: [], herstellerOptions: [], kundenOptions: [] }
    }
    const kundenOptions = [
      'Alle',
      ...Array.from(new Set(overview.machines.map((m) => m.customer).filter(Boolean) as string[]))
        .sort((a, b) => a.localeCompare(b, 'de-CH')),
    ]
    const byCustomer = kunde === 'Alle'
      ? overview.machines
      : overview.machines.filter((m) => m.customer === kunde)
    const herstellerOptions = ['Alle', ...new Set(byCustomer.map((m) => m.hersteller))]
    const filtered = hersteller === 'Alle'
      ? byCustomer
      : byCustomer.filter((m) => m.hersteller === hersteller)
    const primary = filtered.filter(
      (m) => (m.docs ?? 0) > 0 || (m.imgs ?? 0) > 0 || (m.txts ?? 0) > 0,
    )
    const sonstiges = filtered.filter(
      (m) => (m.docs ?? 0) === 0 && (m.imgs ?? 0) === 0 && (m.txts ?? 0) === 0,
    )
    primary.sort((a, b) => (b.sections ?? 0) - (a.sections ?? 0))
    return { primary, sonstiges, herstellerOptions, kundenOptions }
  }, [overview, kunde, hersteller])

  useEffect(() => {
    if (hersteller === 'Alle' || herstellerOptions.includes(hersteller)) return
    setHersteller('Alle')
  }, [hersteller, herstellerOptions])

  function pickMachine(slug: string) {
    setSelected(slug)
    setMode('machine')
    setActiveChat(null)
    setChatMessages([])
    setSelectedAssistantId(null)
    setSectionDetail(null)
    listProtoChats({ machine_slug: slug })
      .then((sessions) => {
        setChatSessions(sessions)
      })
      .catch(console.error)
  }

  function backToSite() {
    setSelected(null)
    setMode('site')
    setActiveChat(null)
    setChatMessages([])
    setChatSessions([])
    setSelectedAssistantId(null)
    setSectionDetail(null)
  }

  async function runQuery(q?: string) {
    const text = (q ?? query).trim()
    if (!text) return
    const startingMode = mode
    setQuery('')
    setAskedQuery(text)
    setLoading(true)
    setSectionDetail(null)
    setActiveCite(null)
    setMode('ask')
    try {
      const customer = selected
        ? selMachine?.customer ?? null
        : kunde === 'Alle' ? null : kunde
      let session = activeChat
      let createdNewSession = false
      const forceNewSession = startingMode !== 'ask'
      if (
        forceNewSession ||
        !session ||
        (session.machine_slug ?? null) !== selected ||
        (session.customer ?? null) !== customer
      ) {
        session = await createProtoChat({
          machine_slug: selected,
          customer,
          title: text,
        })
        createdNewSession = true
        setActiveChat(session)
        setChatMessages([])
        setChatSessions((prev) => [session!, ...prev.filter((s) => s.id !== session!.id)])
      }
      const r = await sendProtoChatMessage(session.id, {
        text,
        deep,
      })
      setActiveChat(r.session)
      setChatMessages((prev) => [
        ...(createdNewSession ? [] : prev),
        r.user_message,
        r.assistant_message,
      ])
      setSelectedAssistantId(r.assistant_message.id)
      setChatSessions((prev) => [r.session, ...prev.filter((s) => s.id !== r.session.id)])
      setQuery('')
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  async function openChat(session: ProtoChatSession) {
    setActiveChat(session)
    setMode('ask')
    setLoading(true)
    setSectionDetail(null)
    setActiveCite(null)
    try {
      const detail = await getProtoChat(session.id)
      setActiveChat(detail.session)
      setChatMessages(detail.messages)
      const latestAssistant = [...detail.messages]
        .reverse()
        .find((m) => m.role === 'assistant')
      setSelectedAssistantId(latestAssistant?.id ?? null)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  async function showSection(sectionId: string | undefined, idx: number) {
    if (!sectionId) return
    setActiveCite(idx)
    try {
      const s = await getProtoSection(sectionId)
      setSectionDetail(s)
    } catch (e) {
      console.error(e)
    }
  }

  if (!overview) return <div className="proto2-loading">Lade Wissensdatenbank …</div>

  return (
    <div className="proto2">
      <div className="proto2-breadcrumb">
        <a onClick={backToSite}>Kunden</a>
        <span className="sep">›</span>
        <a onClick={backToSite}>{overview.customer.name}</a>
        {selMachine && (
          <>
            <span className="sep">›</span>
            <a onClick={() => setMode('machine')}>{selMachine.folder}</a>
          </>
        )}
        {mode === 'ask' && (
          <>
            <span className="sep">›</span>
            <span>Frage</span>
          </>
        )}
      </div>

      {mode === 'site' && (
        <SiteOverview
          overview={overview}
          primary={primary}
          sonstiges={sonstiges}
          hersteller={hersteller}
          kunde={kunde}
          herstellerOptions={herstellerOptions}
          kundenOptions={kundenOptions}
          sonstigesOpen={sonstigesOpen}
          onPickMachine={pickMachine}
          onChangeKunde={setKunde}
          onChangeHersteller={setHersteller}
          onToggleSonstiges={() => setSonstigesOpen(!sonstigesOpen)}
          query={query}
          setQuery={setQuery}
          deep={deep}
          setDeep={setDeep}
          loading={loading}
          onAsk={() => runQuery()}
        />
      )}

      {mode === 'machine' && selMachine && (
        <MachineLanding
          machine={selMachine}
          query={query}
          setQuery={setQuery}
          onAsk={() => runQuery()}
          onSuggest={runQuery}
          deep={deep}
          setDeep={setDeep}
          loading={loading}
          sessions={chatSessions}
          onOpenChat={openChat}
          onBack={backToSite}
        />
      )}

      {mode === 'ask' && (
        <AskView
          query={query}
          setQuery={setQuery}
          askedQuery={askedQuery}
          onAsk={() => runQuery()}
          loading={loading}
          sessions={chatSessions}
          activeChat={activeChat}
          messages={chatMessages}
          selectedAssistantId={selectedAssistantId}
          setSelectedAssistantId={setSelectedAssistantId}
          onOpenChat={openChat}
          deep={deep}
          setDeep={setDeep}
          activeCite={activeCite}
          setActiveCite={setActiveCite}
          showSection={showSection}
          sectionDetail={sectionDetail}
          setSectionDetail={setSectionDetail}
          setLightbox={setLightbox}
          scope={selMachine ? selMachine.folder : kunde === 'Alle' ? 'Alle Maschinen' : kunde}
          onBack={selMachine ? () => setMode('machine') : backToSite}
        />
      )}

      {lightbox && (
        <div className="proto-lightbox" onClick={() => setLightbox(null)}>
          <img src={lightbox} alt="" />
        </div>
      )}
    </div>
  )
}

// ── Site overview ─────────────────────────────────────────────

function SiteOverview({
  overview,
  primary,
  sonstiges,
  kunde,
  hersteller,
  kundenOptions,
  herstellerOptions,
  sonstigesOpen,
  onPickMachine,
  onChangeKunde,
  onChangeHersteller,
  onToggleSonstiges,
  query,
  setQuery,
  deep,
  setDeep,
  loading,
  onAsk,
}: {
  overview: CustomerOverview
  primary: CustomerOverview['machines']
  sonstiges: CustomerOverview['machines']
  kunde: string
  hersteller: string
  kundenOptions: string[]
  herstellerOptions: string[]
  sonstigesOpen: boolean
  onPickMachine: (slug: string) => void
  onChangeKunde: (k: string) => void
  onChangeHersteller: (h: string) => void
  onToggleSonstiges: () => void
  query: string
  setQuery: (s: string) => void
  deep: boolean
  setDeep: (b: boolean) => void
  loading: boolean
  onAsk: () => void
}) {
  const s = overview.stats
  return (
    <>
      <section className="proto2-hero">
        <h1>{overview.customer.name}</h1>
        <div className="meta">
          {overview.customer.machine_count} Maschinen · Wissensdatenbank aus
          Hersteller- und Service-Dokumentation
        </div>
        <div className="stats">
          <Stat v={s.machines} l="Maschinen" />
          <Stat v={s.documents} l="Indexierte Dokumente" />
          <Stat v={s.pages} l="Seiten analysiert" />
          <Stat v={s.images} l="Bilder & Schemata" />
          <Stat v={s.configs} l="Konfigurationen" />
        </div>
      </section>

      <div className="proto2-ask-bar">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !loading && onAsk()}
          placeholder='Frage zur gesamten Anlage — z. B. „Welche Maschinen verwenden Avery-Druckköpfe?"'
        />
        <label className="deep">
          <input
            type="checkbox"
            checked={deep}
            onChange={(e) => setDeep(e.target.checked)}
          />
          Deep mode
        </label>
        <button onClick={onAsk} disabled={loading || !query.trim()}>
          {loading ? <span>Denke nach<ThinkingDots /></span> : 'Fragen'}
        </button>
      </div>
      <div className="proto2-ask-meta">
        Standard-Bereich:{' '}
        <strong>{kunde === 'Alle' ? `alle ${s.machines} Maschinen` : kunde}</strong> ·
        nach Klick auf eine Maschine weiter eingrenzbar
      </div>

      <div className="proto2-section-row">
        <div className="proto2-section-title">Maschinen</div>
        <div className="proto2-filters">
          <div className="proto2-filter-row">
            <span className="label">Kunde:</span>
            <CustomerPicker value={kunde} options={kundenOptions} onChange={onChangeKunde} />
          </div>
          <div className="proto2-filter-row">
            <span className="label">Hersteller:</span>
            {herstellerOptions.map((h) => (
              <span
                key={h}
                className={`chip ${hersteller === h ? 'active' : ''}`}
                onClick={() => onChangeHersteller(h)}
              >
                {h}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="proto2-machine-grid">
        {primary.map((m) => (
          <MachineCard key={m.slug} machine={m} onClick={() => onPickMachine(m.slug)} />
        ))}
      </div>

      {sonstiges.length > 0 && (
        <div className={`proto2-sonstiges ${sonstigesOpen ? 'open' : ''}`}>
          <div className="row" onClick={onToggleSonstiges}>
            <span>
              {sonstigesOpen ? '▾' : '▸'} Ohne indexierbare Dokumentation (
              {sonstiges.length} Maschinen — nur externe Verknüpfungen)
            </span>
            <span className="toggle">{sonstigesOpen ? 'einklappen' : 'aufklappen'}</span>
          </div>
          {sonstigesOpen && (
            <div className="items">
              {sonstiges.map((m) => (
                <div
                  key={m.slug}
                  className="sonst-mini"
                  onClick={() => onPickMachine(m.slug)}
                >
                  <span className="lbl">{m.hersteller}</span>
                  {m.folder}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  )
}

function Stat({ v, l }: { v: number | string; l: string }) {
  const formatted =
    typeof v === 'number' && v >= 1000
      ? v.toLocaleString('de-CH').replace(/,/g, "'")
      : v
  return (
    <div className="proto2-stat">
      <span className="v">{formatted}</span>
      <span className="l">{l}</span>
    </div>
  )
}

function CustomerPicker({
  value,
  options,
  onChange,
}: {
  value: string
  options: string[]
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const visibleOptions = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return options
      .filter((option) => option === 'Alle' || !needle || option.toLowerCase().includes(needle))
      .slice(0, 50)
  }, [options, search])
  const label = value === 'Alle' ? 'Alle Kunden' : value

  function pick(option: string) {
    onChange(option)
    setSearch('')
    setOpen(false)
  }

  return (
    <div className="proto2-customer-picker">
      <input
        aria-label="Kunde auswählen"
        value={open ? search : label}
        onFocus={() => {
          setOpen(true)
          setSearch('')
        }}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onChange={(e) => {
          setSearch(e.target.value)
          setOpen(true)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setOpen(false)
          }
          if (e.key === 'Enter' && open && visibleOptions.length > 0) {
            pick(visibleOptions[0])
          }
        }}
        placeholder="Kunde suchen"
      />
      {open && (
        <div className="proto2-customer-menu" onMouseDown={(e) => e.preventDefault()}>
          {visibleOptions.map((option) => (
            <button
              key={option}
              type="button"
              className={option === value ? 'active' : ''}
              onClick={() => pick(option)}
            >
              {option === 'Alle' ? 'Alle Kunden' : option}
            </button>
          ))}
          {visibleOptions.length === 0 && (
            <div className="empty">Keine Kunden gefunden</div>
          )}
        </div>
      )}
    </div>
  )
}

function MachineCard({
  machine,
  onClick,
}: {
  machine: CustomerOverview['machines'][number]
  onClick: () => void
}) {
  const docs = machine.pdfs ?? 0
  const imgs = machine.imgs ?? 0
  const cfgs = machine.txts ?? 0
  const sections = machine.sections ?? 0

  let summary = ''
  if (docs > 0 && sections > 0) {
    summary = `${sections} Seiten technische Dokumentation indexiert${cfgs > 0 ? `, ${cfgs} Kunden-Konfigurationen` : ''}${imgs > 0 ? `, ${imgs} Schemata/Bilder` : ''}.`
  } else if (imgs > 0) {
    summary = `${imgs} Bilder verfügbar — keine PDF-Dokumentation indexiert.`
  } else if (cfgs > 0) {
    summary = `${cfgs} Konfigurationsdateien.`
  } else {
    summary = 'Keine indexierbaren Inhalte.'
  }

  return (
    <div className="proto2-machine-card" onClick={onClick}>
      <div className="type">
        {machine.type || 'Maschine'} · {machine.hersteller}
      </div>
      <div className="name">{machine.model || machine.folder}</div>
      <div className="serial">{machine.serial || ''}</div>
      <div className="summary">{summary}</div>
      <div className="badges">
        {docs > 0 && (
          <span className="tag docs">
            📘 {docs} PDF{sections > 0 ? ` · ${sections} S.` : ''}
          </span>
        )}
        {cfgs > 0 && <span className="tag cfg">⚙ {cfgs} Cfg</span>}
        {imgs > 0 && <span className="tag img">🖼 {imgs}</span>}
      </div>
    </div>
  )
}

// ── Machine landing ───────────────────────────────────────────

function MachineLanding({
  machine,
  query,
  setQuery,
  onAsk,
  onSuggest,
  deep,
  setDeep,
  loading,
  sessions,
  onOpenChat,
  onBack,
}: {
  machine: CustomerOverview['machines'][number]
  query: string
  setQuery: (s: string) => void
  onAsk: () => void
  onSuggest: (q: string) => void
  deep: boolean
  setDeep: (b: boolean) => void
  loading: boolean
  sessions: ProtoChatSession[]
  onOpenChat: (session: ProtoChatSession) => void
  onBack: () => void
}) {
  const suggestions = buildSuggestions(machine)

  return (
    <>
      <a className="proto2-nav-back" onClick={onBack}>
        ← Zurück zur Übersicht
      </a>

      <div className="proto2-machine-hero">
        <div className="info">
          <div className="type">
            {machine.type} · {machine.hersteller}
          </div>
          <h1>{machine.model || machine.folder}</h1>
          <div className="serial">
            {machine.serial && <>Serien-Nr. {machine.serial}</>}
          </div>
          <div className="counts">
            <span>{machine.pdfs ?? 0} PDF</span>
            <span>{machine.sections ?? 0} indexierte Seiten</span>
            <span>{machine.imgs ?? 0} Bilder</span>
            <span>{machine.txts ?? 0} Konfigurationen</span>
          </div>
        </div>
        <div className="suggestions">
          <h3>Vorgeschlagene Fragen</h3>
          {suggestions.map((s) => (
            <div className="qa-item" key={s} onClick={() => onSuggest(s)}>
              {s}
            </div>
          ))}
        </div>
      </div>

      <section className="proto2-machine-chats">
        <div className="proto2-machine-chats-head">
          <div>
            <div className="label">Gespeicherte Chats</div>
            <h3>Vergangene Unterhaltungen</h3>
          </div>
          <span>{sessions.length}</span>
        </div>
        {sessions.length === 0 ? (
          <div className="empty">Noch keine Chats zu dieser Maschine.</div>
        ) : (
          <div className="items">
            {sessions.map((session) => (
              <button
                key={session.id}
                type="button"
                onClick={() => onOpenChat(session)}
              >
                <span className="title">{session.title || 'Neue Unterhaltung'}</span>
                <span className="meta">
                  {session.message_count ?? 0} Nachrichten · {formatDateShort(session.last_message_at || session.updated_at)}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      <div className="proto2-ask-bar">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !loading && onAsk()}
          placeholder="Frage zu dieser Maschine — Ersatzteil, Schema, Wartungsschritt…"
        />
        <label className="deep">
          <input
            type="checkbox"
            checked={deep}
            onChange={(e) => setDeep(e.target.checked)}
          />
          Deep mode
        </label>
        <button onClick={onAsk} disabled={loading || !query.trim()}>
          {loading ? <span>Denke nach<ThinkingDots /></span> : 'Fragen'}
        </button>
      </div>
    </>
  )
}

// ── Ask view ───────────────────────────────────────────────────

function AskView({
  query,
  setQuery,
  askedQuery,
  onAsk,
  loading,
  sessions,
  activeChat,
  messages,
  selectedAssistantId,
  setSelectedAssistantId,
  onOpenChat,
  deep,
  setDeep,
  activeCite,
  setActiveCite,
  showSection,
  sectionDetail,
  setSectionDetail,
  setLightbox,
  scope,
  onBack,
}: {
  query: string
  setQuery: (s: string) => void
  askedQuery: string
  onAsk: () => void
  loading: boolean
  sessions: ProtoChatSession[]
  activeChat: ProtoChatSession | null
  messages: ProtoChatMessage[]
  selectedAssistantId: string | null
  setSelectedAssistantId: (id: string | null) => void
  onOpenChat: (session: ProtoChatSession) => void
  deep: boolean
  setDeep: (b: boolean) => void
  activeCite: number | null
  setActiveCite: (n: number | null) => void
  showSection: (id: string | undefined, idx: number) => void
  sectionDetail: ProtoSectionDetail | null
  setSectionDetail: (s: ProtoSectionDetail | null) => void
  setLightbox: (s: string | null) => void
  scope: string
  onBack: () => void
}) {
  const selectedAssistant =
    messages.find((m) => m.id === selectedAssistantId && m.role === 'assistant') ||
    [...messages].reverse().find((m) => m.role === 'assistant') ||
    null
  const selectedAssistantIndex = selectedAssistant
    ? messages.findIndex((m) => m.id === selectedAssistant.id)
    : -1
  const selectedQuery =
    selectedAssistantIndex > 0
      ? [...messages.slice(0, selectedAssistantIndex)]
          .reverse()
          .find((m) => m.role === 'user')?.text || askedQuery
      : askedQuery

  return (
    <>
      <a className="proto2-nav-back" onClick={onBack}>
        ← Zurück
      </a>
      <div className="proto2-ask-meta">Bereich: <strong>{scope}</strong></div>

      <div className="proto-chat-shell">
        <aside className="proto-chat-history">
          <div className="proto-chat-history-title">Chats</div>
          {sessions.length === 0 && (
            <div className="proto-chat-empty">Noch keine gespeicherten Chats</div>
          )}
          {sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              className={activeChat?.id === session.id ? 'active' : ''}
              onClick={() => onOpenChat(session)}
            >
              <span className="title">{session.title || 'Neue Unterhaltung'}</span>
              <span className="meta">
                {session.message_count ?? 0} Nachrichten · {formatDateShort(session.last_message_at || session.updated_at)}
              </span>
            </button>
          ))}
        </aside>

        <div className="proto-chat-main">
          <div className="proto-chat-thread">
            {messages.length === 0 && !loading && (
              <div className="proto-chat-start">
                Starte eine Unterhaltung zu diesem Bereich. Folgefragen bleiben im Kontext und werden gespeichert.
              </div>
            )}
            {messages.map((msg) => (
              <div key={msg.id} className={`proto-chat-message ${msg.role}`}>
                <div className="proto-chat-message-meta">
                  <span>{msg.role === 'user' ? msg.username || 'User' : 'Assistant'}</span>
                  <span>{formatDateShort(msg.created_at)}</span>
                </div>
                {msg.role === 'assistant' ? (
                  <>
                    <div
                      className="proto-answer"
                      dangerouslySetInnerHTML={{ __html: formatAnswer(msg.text) }}
                    />
                    {msg.model && (
                      <div className="proto-model-label">model: {msg.model}</div>
                    )}
                    {(msg.citations?.length ?? 0) > 0 && (
                      <div className="proto-citations">
                        {msg.citations!.map((c) => (
                          <button
                            key={`${msg.id}-${c.idx}`}
                            className={`proto-cite ${selectedAssistant?.id === msg.id && activeCite === c.idx ? 'active' : ''}`}
                            onClick={() => {
                              setSelectedAssistantId(msg.id)
                              if (c.kind === 'page' && c.section_id) {
                                showSection(c.section_id, c.idx)
                              } else {
                                setActiveCite(c.idx)
                                setSectionDetail(null)
                              }
                            }}
                            title={`${c.machine} / ${c.doc}`}
                          >
                            [{c.idx}] {c.kind === 'page' ? `p.${c.page}` : c.name || c.doc}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="proto-chat-user-text">{msg.text}</div>
                )}
              </div>
            ))}
            {loading && (
              <div className="proto-chat-message assistant pending">
                <div className="proto-chat-message-meta">
                  <span>Assistant</span>
                  <span>arbeitet</span>
                </div>
                <div>
                  Verarbeite „{askedQuery}"<ThinkingDots />
                </div>
              </div>
            )}
          </div>

          <div className="proto-chat-composer">
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !loading && query.trim()) {
                  e.preventDefault()
                  onAsk()
                }
              }}
              placeholder="Nachricht schreiben…"
              rows={2}
            />
            <label className="deep">
              <input
                type="checkbox"
                checked={deep}
                onChange={(e) => setDeep(e.target.checked)}
              />
              Deep mode
            </label>
            <button onClick={onAsk} disabled={loading || !query.trim()}>
              {loading ? <span>Denke nach<ThinkingDots /></span> : 'Senden'}
            </button>
          </div>
        </div>
      </div>

      {selectedAssistant && (selectedAssistant.hits?.length ?? 0) > 0 && (
        <div className="proto-answer-block proto-chat-evidence">
          <div className="proto-chat-evidence-title">Quellen zur ausgewählten Antwort</div>
          <div className="proto-hits">
            {selectedAssistant.hits!.map((h, i) => (
              <HitCard
                key={`${selectedAssistant.id}-${h.label}-${h.id}`}
                hit={h}
                idx={i + 1}
                active={activeCite === i + 1}
                query={selectedQuery}
                onClick={() => {
                  setSelectedAssistantId(selectedAssistant.id)
                  if (h.label === 'ManualSection') {
                    showSection(h.id, i + 1)
                  } else {
                    setActiveCite(i + 1)
                    setSectionDetail(null)
                  }
                }}
              />
            ))}
          </div>
        </div>
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
            · p.{sectionDetail.page}
          </h3>
          <div className="proto-detail-big">
            <img
              src={`/api/proto/page-image/${sectionDetail.id}`}
              alt=""
              onClick={() =>
                setLightbox(`/api/proto/page-image/${sectionDetail.id}`)
              }
            />
          </div>
        </aside>
      )}
    </>
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
    const v = hit.vision_desc || ''
    const ps = v.match(/##\s*Page\s*summary\s*\n+([^\n]+)/i)
    headline = ps ? ps[1].trim() : firstNonEmpty(v) || firstNonEmpty(hit.text || '') || ''
    sub = highlightSnippet(hit.text || v || '', query, 120)
  } else if (label === 'ConfigFile') {
    const s = hit.summary || ''
    const cust = s.match(/CUSTOMER:\s*([^\n]+)/i)
    const purp = s.match(/PURPOSE:\s*([^\n]+)/i)
    const search = s.match(/SEARCHABLE:\s*([\s\S]+?)(?:\n\n|$)/i)
    headline = purp ? purp[1].trim() : firstNonEmpty(s)
    sub = (cust ? `👤 ${cust[1].trim()} — ` : '') +
      (search ? search[1].trim() : highlightSnippet(s, query, 160))
  } else if (isImage) {
    const c = hit.caption || ''
    const at = c.match(/##\s*Asset\s*type\s*\n+([^\n]+)/i)
    headline = at ? at[1].trim() : firstNonEmpty(c)
    sub = highlightSnippet(c, query, 140)
  }

  return (
    <div
      className={`proto-hit ${label.toLowerCase()} ${active ? 'active' : ''}`}
      onClick={onClick}
    >
      <div className="proto-hit-top">
        <SourceThumbnail src={thumbUrl} />
        <div className="proto-hit-meta">
          <div className="proto-hit-badge-row">
            <span className={`proto-hit-badge b-${label.toLowerCase()}`}>
              [{idx}] {label.replace('ManualSection', 'PAGE').replace('ConfigFile', 'CONFIG').replace('ImageAsset', 'IMAGE')}
            </span>
            <span className="proto-hit-score">{hit.score.toFixed(3)}</span>
          </div>
          <div className="proto-hit-title">{hit.machine_folder}</div>
          <a
            className="proto-hit-sub proto-hit-doc-link"
            href={`/api/proto/view/${hit.document_id}${hit.page ? `?page=${hit.page}` : ''}`}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            title="Original-Dokument im neuen Tab öffnen"
          >
            {hit.doc_name}
            {hit.page ? ` · p.${hit.page}` : ''} ↗
          </a>
        </div>
      </div>
      {headline && <div className="proto-hit-headline">{headline}</div>}
      {sub && (
        <div
          className="proto-hit-snippet"
          dangerouslySetInnerHTML={{ __html: sub }}
        />
      )}
    </div>
  )
}

function SourceThumbnail({ src }: { src: string | null }) {
  const [failed, setFailed] = useState(false)
  if (!src || failed) {
    return (
      <div className="proto-hit-thumb proto-hit-thumb-fallback" aria-hidden="true">
        <span>Kein Bild</span>
      </div>
    )
  }
  return (
    <img
      src={src}
      alt=""
      className="proto-hit-thumb"
      loading="lazy"
      onError={() => setFailed(true)}
    />
  )
}

function ThinkingDots() {
  return (
    <span className="proto-thinking-dots" aria-hidden="true">
      <span>.</span>
      <span>.</span>
      <span>.</span>
    </span>
  )
}

// Hand-tuned high-confidence queries for known machines (proven to return
// good answers in our benchmark)
const KNOWN_GOOD: Record<string, string[]> = {
  smb: [
    'Welche Bestellnummer hat das Rillenkugellager der Umlenkrolle?',
    'Welche Baugruppen gehören zur 2.4 Mio Wartungsstufe?',
    'Wo sitzen die Schaltnetzteile T14 und T15 — was ist die Klemmleiste darunter?',
    'Wie transportiere ich die SMB S03 — wo sind die Hebepunkte markiert?',
    'Aus welchen Teilen besteht die Spannrolle x17730_?',
  ],
  netjet1: [
    'Adressiersystem meldet "Out of Sequence" — welche Registry-Parameter prüfen?',
    'Welche GUI-Hardware-Konfiguration wird empfohlen?',
    'Wie wird der V4-Emulator eingerichtet?',
    'Welche Job-Konfigurationen sind für den Kunden ENIWA hinterlegt?',
    'Was zeigt die Bitmap ABB_600.bmp — für welche Sendungen ist sie?',
  ],
  netjet2: [
    'Welche Bitmap-Templates sind für NetJet 2 hinterlegt?',
    'Welche GUI-Konfigurationen unterscheidet sich von NetJet 1?',
    'Was zeigt die Versions-Information der NetJet 2 GUI?',
    'Wie ist das Kundennetzwerk eingerichtet?',
  ],
  cmc: [
    'Wo sitzen die Heizungen und welche Schemata gibt es dafür?',
    'Welche Seriennummern sind für die CMC 2800 dokumentiert?',
    'Welche Avery-Komponenten sind in der Dokumentation aufgeführt?',
  ],
  inkjet_bx: [
    'Welche Komponenten besteht der Encoder mit Steckerbox?',
    'Welche Ersatzteile sind für die 14-Stationen-Variante aufgeführt?',
  ],
}

function buildSuggestions(m: CustomerOverview['machines'][number]): string[] {
  const slug = (m.slug || '').toLowerCase()
  const folder = (m.folder || '').toLowerCase()
  const all = `${slug} ${folder}`

  // Match known-good buckets
  let knownKey: string | null = null
  if (all.includes('smb')) knownKey = 'smb'
  else if (all.includes('netjet 1') || all.includes('netjet-1')) knownKey = 'netjet1'
  else if (all.includes('netjet 2') || all.includes('netjet-2')) knownKey = 'netjet2'
  else if (all.includes('cmc') || all.includes('folieneinschlag')) knownKey = 'cmc'
  else if (all.includes('inkjet') && all.includes('bx')) knownKey = 'inkjet_bx'

  const known = knownKey ? KNOWN_GOOD[knownKey] || [] : []

  // Generic — derive from real doc names so it always works
  const docs = (m.sample_docs || []).filter(
    (d) => d.name && !/^[\d_-]+\.(pdf|PDF)$/.test(d.name),
  )
  const generic: string[] = []
  for (const d of docs.slice(0, 4)) {
    const cleanName = d.name
      .replace(/\.(pdf|PDF|txt|TXT|jpg|JPG|bmp|BMP)$/i, '')
      .replace(/_/g, ' ')
      .slice(0, 80)
    generic.push(`Was beschreibt „${cleanName}"?`)
  }

  // Combine: known-good first, then generic from real doc names, dedup, cap at 6
  const seen = new Set<string>()
  const merged: string[] = []
  for (const q of [...known, ...generic]) {
    const key = q.toLowerCase().slice(0, 50)
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(q)
    if (merged.length >= 6) break
  }
  if (merged.length === 0) {
    if ((m.pdfs ?? 0) > 0) merged.push('Welche Themen behandelt die Dokumentation?')
    if ((m.imgs ?? 0) > 0) merged.push('Was zeigen die Bilder?')
    if ((m.txts ?? 0) > 0) merged.push('Welche Konfigurationen sind hinterlegt?')
  }
  return merged
}

function formatDateShort(value: string | null | undefined): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('de-CH', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function firstNonEmpty(s: string): string {
  for (const line of s.split('\n')) {
    const t = line.replace(/^#+\s*/, '').replace(/\*\*/g, '').trim()
    if (t && t.length > 3 && !t.startsWith('##')) return t.slice(0, 100)
  }
  return ''
}

function escHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function highlightSnippet(text: string, query: string, len: number): string {
  if (!text) return ''
  const terms = query
    .split(/\s+/)
    .filter((t) => t.length >= 3 && !/^(co|the|and|gdzie|jak|pokazuje|jest|sa|dla)$/i.test(t))
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  let snippet = text.replace(/\n+/g, ' ').trim()
  if (terms.length) {
    const re = new RegExp(`(${terms.join('|')})`, 'i')
    const m = snippet.match(re)
    if (m && m.index !== undefined) {
      const start = Math.max(0, m.index - 40)
      snippet = (start > 0 ? '…' : '') + snippet.slice(start, start + len + 40)
    } else {
      snippet = snippet.slice(0, len)
    }
    const hlRe = new RegExp(`(${terms.join('|')})`, 'gi')
    return escHtml(snippet).replace(hlRe, '<mark>$1</mark>')
  }
  return escHtml(snippet.slice(0, len))
}

function formatAnswer(a: string): string {
  const esc = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

  const lines = a.split('\n')
  const out: string[] = []
  let inList: 'ul' | 'ol' | null = null
  const closeList = () => {
    if (inList) {
      out.push(`</${inList}>`)
      inList = null
    }
  }

  for (const raw of lines) {
    const line = raw.trimEnd()
    const ulMatch = line.match(/^\s*[-*]\s+(.+)$/)
    const olMatch = line.match(/^\s*(\d+)\.\s+(.+)$/)
    const hMatch = line.match(/^(#{1,6})\s+(.+)$/)

    if (ulMatch) {
      if (inList !== 'ul') {
        closeList()
        out.push('<ul>')
        inList = 'ul'
      }
      out.push(`<li>${renderInline(ulMatch[1], esc)}</li>`)
    } else if (olMatch) {
      if (inList !== 'ol') {
        closeList()
        out.push('<ol>')
        inList = 'ol'
      }
      out.push(`<li>${renderInline(olMatch[2], esc)}</li>`)
    } else if (hMatch) {
      closeList()
      const level = Math.min(hMatch[1].length + 2, 6)
      out.push(`<h${level}>${renderInline(hMatch[2], esc)}</h${level}>`)
    } else if (line.trim() === '') {
      closeList()
      out.push('')
    } else {
      closeList()
      out.push(`<p>${renderInline(line, esc)}</p>`)
    }
  }
  closeList()
  return out.join('\n')
}

function renderInline(s: string, esc: (x: string) => string): string {
  let t = esc(s)
  t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  t = t.replace(/(^|[\s(])\*([^*\n]+?)\*(?=[\s.,;:!?)]|$)/g, '$1<em>$2</em>')
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>')
  t = t.replace(/\[(\d+)\]/g, '<sup class="cite-ref">[$1]</sup>')
  return t
}
