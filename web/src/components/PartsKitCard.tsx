import { useState, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { marked } from 'marked'
import type { PartsKit, PartEntry } from '../api/mission'

function PartRow({ p }: { p: PartEntry }) {
  const { t } = useTranslation()
  return (
    <div className="parts-row">
      <Link to={`/part/${p.nummer}`} className="part-nummer part-nummer-link">{p.nummer}</Link>
      <span className="part-titel-full">{p.titel}</span>
      {p.frequency != null && (
        <span className="part-freq">{t('briefing.frequency', { count: p.frequency })}</span>
      )}
    </div>
  )
}

export function PartsKitCard({ kit }: { kit: PartsKit }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [showRaw, setShowRaw] = useState(false)
  const hasAny = kit.machine_parts.length || kit.type_parts.length || kit.co_occurrence_parts.length

  const summaryHtml = useMemo(() => {
    if (!kit.summary) return ''
    let html = marked.parse(kit.summary) as string
    // Replace <code>NNNNN</code> (from markdown backticks) with links
    html = html.replace(
      /<code>(\d{4,5})<\/code>/g,
      '<a href="/einsatzplaner/part/$1" class="part-ref part-ref-link" data-part="$1">$1</a>'
    )
    // Replace remaining bare part numbers
    html = html.replace(
      /(?<!data-part=")\b(\d{4,5})\b(?=[:\s,.])/g,
      '<a href="/einsatzplaner/part/$1" class="part-ref part-ref-link" data-part="$1">$1</a>'
    )
    return html
  }, [kit.summary])

  const handleSummaryClick = useCallback((e: React.MouseEvent) => {
    const target = e.target as HTMLElement
    const partNummer = target.closest<HTMLElement>('[data-part]')?.dataset.part
    if (partNummer) {
      e.preventDefault()
      navigate(`/part/${partNummer}`)
    }
  }, [navigate])

  if (!hasAny) return null

  return (
    <div className="card">
      <h3 className="card-header">
        <span className="card-icon">&#128295;</span>
        {t('briefing.partsKit')}
      </h3>

      {summaryHtml && (
        <div
          className="parts-kit-summary"
          dangerouslySetInnerHTML={{ __html: summaryHtml }}
          onClick={handleSummaryClick}
        />
      )}

      <button
        className="case-expand-btn"
        onClick={() => setShowRaw(!showRaw)}
      >
        {showRaw ? t('briefing.hidePartsList') : t('briefing.showPartsList')}
        <span className={`case-expand-chevron ${showRaw ? 'open' : ''}`}>&#9662;</span>
      </button>

      {showRaw && (
        <div className="parts-raw-list">
          {kit.machine_parts.length > 0 && (
            <div className="parts-section">
              <h4 className="parts-section-title">{t('briefing.machineParts')}</h4>
              <div className="parts-table">
                {kit.machine_parts.map((p, i) => (
                  <PartRow key={i} p={p} />
                ))}
              </div>
            </div>
          )}

          {kit.type_parts.length > 0 && (
            <div className="parts-section">
              <h4 className="parts-section-title">{t('briefing.typeParts')}</h4>
              <div className="parts-table">
                {kit.type_parts.map((p, i) => (
                  <PartRow key={i} p={p} />
                ))}
              </div>
            </div>
          )}

          {kit.co_occurrence_parts.length > 0 && (
            <div className="parts-section">
              <h4 className="parts-section-title">{t('briefing.coOccurrence')}</h4>
              <div className="parts-table">
                {kit.co_occurrence_parts.map((p, i) => (
                  <PartRow key={i} p={p} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
