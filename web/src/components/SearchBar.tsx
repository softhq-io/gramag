import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useDebounce } from '../hooks/useDebounce'
import { searchMachines, type MachineSearchResult } from '../api/mission'

interface Props {
  onSelect: (machine: MachineSearchResult) => void
  symptom: string
  onSymptomChange: (s: string) => void
  onAsk?: () => void
}

export function SearchBar({ onSelect, symptom, onSymptomChange, onAsk }: Props) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<MachineSearchResult[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const debouncedQuery = useDebounce(query, 300)
  const wrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (debouncedQuery.length < 2) {
      setResults([])
      return
    }
    setLoading(true)
    searchMachines(debouncedQuery)
      .then(r => {
        setResults(r)
        setOpen(true)
      })
      .catch(() => setResults([]))
      .finally(() => setLoading(false))
  }, [debouncedQuery])

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  return (
    <div className="search-container" ref={wrapperRef}>
      <div className="search-inputs">
        <div className="search-field">
          <svg className="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            className="search-input"
            placeholder={t('search.placeholder')}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onFocus={() => results.length > 0 && setOpen(true)}
            autoFocus
          />
          {loading && <span className="search-spinner" />}
        </div>
        <input
          type="text"
          className="symptom-input"
          placeholder={t('search.symptomPlaceholder')}
          value={symptom}
          onChange={e => onSymptomChange(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && symptom.trim() && onAsk) {
              e.preventDefault()
              onAsk()
            }
          }}
        />
      </div>
      {open && results.length > 0 && (
        <ul className="search-dropdown">
          {results.map(m => (
            <li
              key={m.erp_id}
              className="search-result"
              onClick={() => {
                onSelect(m)
                setOpen(false)
                setQuery(m.title || '')
              }}
            >
              <div className="search-result-title">{m.title}</div>
              <div className="search-result-meta">
                {m.customer && <span>{m.customer}</span>}
                {m.machine_type && <span className="badge badge-type">{m.machine_type}</span>}
                {m.brand && <span className="badge badge-brand">{m.brand}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
      {open && debouncedQuery.length >= 2 && results.length === 0 && !loading && (
        <div className="search-dropdown search-no-results">{t('search.noResults')}</div>
      )}
    </div>
  )
}
