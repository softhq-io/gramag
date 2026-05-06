import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Markdown from 'react-markdown'
import { SearchBar } from '../components/SearchBar'
import { askFreeQuestion, type MachineSearchResult, type FreeAnswer } from '../api/mission'

interface GraphStats {
  nodes: number
  relationships: number
  customers?: number
  machines?: number
}

function formatNumber(n: number): string {
  return n.toLocaleString('de-CH')
}

export function SearchPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [symptom, setSymptom] = useState('')
  const [stats, setStats] = useState<GraphStats | null>(null)
  const [answer, setAnswer] = useState<FreeAnswer | null>(null)
  const [askLoading, setAskLoading] = useState(false)
  const [askError, setAskError] = useState('')

  useEffect(() => {
    fetch('/api/graph/stats')
      .then(r => r.json())
      .then(data => {
        const sumValues = (obj: Record<string, number> | number) =>
          typeof obj === 'number' ? obj : Object.values(obj).reduce((a, b) => a + b, 0)
        const nodeMap = typeof data.nodes === 'object' ? data.nodes : {}
        setStats({
          nodes: sumValues(data.nodes),
          relationships: sumValues(data.relationships),
          customers: nodeMap.Customer ?? 0,
          machines: nodeMap.Machine ?? 0,
        })
      })
      .catch(() => {})
  }, [])

  const handleSelect = (machine: MachineSearchResult) => {
    const params = symptom ? `?symptom=${encodeURIComponent(symptom)}` : ''
    navigate(`/mission/${machine.erp_id}${params}`)
  }

  const handleAsk = () => {
    if (!symptom.trim()) return
    setAskLoading(true)
    setAskError('')
    setAnswer(null)
    askFreeQuestion(symptom.trim())
      .then(setAnswer)
      .catch(e => setAskError(e.message || 'Fehler'))
      .finally(() => setAskLoading(false))
  }

  const exampleQueries = [
    'Fehler 5004 Etikettenmaterial gerissen',
    'Papierstau bei Falzmaschine',
    'Leimüberwachung ULT300 Störung',
    'Welche Teile braucht eine MBO T52?',
    'Kuvertiermaschine Stau im Zuführbereich',
    'Schneidemaschine Messer wechseln',
  ]

  const handleExampleClick = (q: string) => {
    setSymptom(q)
    setAnswer(null)
    setAskError('')
    setAskLoading(true)
    askFreeQuestion(q)
      .then(setAnswer)
      .catch(e => setAskError(e.message || 'Fehler'))
      .finally(() => setAskLoading(false))
  }

  return (
    <div className="search-page">
      <div className="search-hero">
        <h1 className="search-hero-title">{t('app.title')}</h1>
        <p className="search-hero-sub">{t('app.subtitle')}</p>
        <SearchBar onSelect={handleSelect} symptom={symptom} onSymptomChange={setSymptom} onAsk={handleAsk} />
        {!answer && !askLoading && !askError && (
          <div className="example-queries">
            <span className="example-queries-label">{t('search.exampleQueries')}</span>
            <div className="example-queries-chips">
              {exampleQueries.map(q => (
                <button key={q} className="example-chip" onClick={() => handleExampleClick(q)}>{q}</button>
              ))}
            </div>
          </div>
        )}
        {askLoading && (
          <div className="free-answer-card">
            <span className="search-spinner" /> {t('search.loading')}
          </div>
        )}
        {askError && (
          <div className="free-answer-card free-answer-error">{askError}</div>
        )}
        {answer && !askLoading && (
          <div className="free-answer-card">
            <div className="free-answer-text"><Markdown>{answer.answer}</Markdown></div>
            {answer.sources.length > 0 && (
              <details className="free-answer-sources">
                <summary>{t('search.sources')} ({answer.sources.length})</summary>
                <ul>
                  {answer.sources.map(s => (
                    <li key={s.rank}>
                      <div>
                        [{s.rank}]{' '}
                        {s.pdf_url
                          ? <a href={s.pdf_url} target="_blank" rel="noopener noreferrer" className="source-link">{s.source}</a>
                          : s.source
                        }
                        {' '}<span className="badge badge-type">{s.type}</span>
                      </div>
                      {s.text && <div className="free-answer-quote">{s.text}</div>}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
        {stats && (
          <div className="hero-stats">
            <div className="hero-stat">
              <div className="hero-stat-value">{formatNumber(stats.nodes)}</div>
              <div className="hero-stat-label">{t('search.nodes')}</div>
            </div>
            <span className="hero-stats-divider">&middot;</span>
            <div className="hero-stat">
              <div className="hero-stat-value">{formatNumber(stats.relationships)}</div>
              <div className="hero-stat-label">{t('search.relationships')}</div>
            </div>
            {stats.customers ? (
              <>
                <span className="hero-stats-divider">&middot;</span>
                <div className="hero-stat">
                  <div className="hero-stat-value">{formatNumber(stats.customers)}</div>
                  <div className="hero-stat-label">{t('search.customers')}</div>
                </div>
              </>
            ) : null}
            {stats.machines ? (
              <>
                <span className="hero-stats-divider">&middot;</span>
                <div className="hero-stat">
                  <div className="hero-stat-value">{formatNumber(stats.machines)}</div>
                  <div className="hero-stat-label">{t('search.machines')}</div>
                </div>
              </>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}
