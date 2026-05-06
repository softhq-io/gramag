import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { SimilarCase } from '../api/mission'

function CaseItem({ c }: { c: SimilarCase }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  const hasDetails =
    (c.comments && c.comments.some(cm => cm.text)) ||
    (c.parts_used && c.parts_used.filter(p => p.nummer).length > 0)

  return (
    <div className="case-item">
      <div className="case-header">
        <span className="case-date">{c.job_date}</span>
        <Link
          to={`/mission/${encodeURIComponent(c.machine_erp_id)}`}
          className="case-title case-link"
          title={t('briefing.openMachineBriefing')}
        >
          {c.job_title}
          <span className="case-link-icon">&#8599;</span>
        </Link>
        {c.symptom_match && (
          <span className="badge badge-match">{t('briefing.symptomMatch')}</span>
        )}
      </div>

      <div className="case-meta">
        <span>{c.machine_title}</span>
        <span className="case-customer">@ {c.customer}</span>
      </div>

      {c.llm_summary && (
        <div className="case-summary">{c.llm_summary}</div>
      )}

      {hasDetails && (
        <>
          <button
            className="case-expand-btn"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? t('briefing.lessDetails') : t('briefing.moreDetails')}
            <span className={`case-expand-chevron ${expanded ? 'open' : ''}`}>&#9662;</span>
          </button>

          {expanded && (
            <div className="case-details">
              {c.comments && c.comments.filter(cm => cm.text).length > 0 && (
                <div className="case-comments">
                  <div className="case-detail-label">{t('briefing.comments')}</div>
                  {c.comments.filter(cm => cm.text).map((cm, j) => (
                    <div key={j} className="case-comment">
                      <div className="case-comment-header">
                        {cm.author && <span className="case-comment-author">{cm.author}</span>}
                        {cm.date && <span className="case-comment-date">{cm.date}</span>}
                      </div>
                      <span className="case-comment-text">{cm.text}</span>
                    </div>
                  ))}
                </div>
              )}

              {c.parts_used && c.parts_used.filter(p => p.nummer).length > 0 && (
                <div className="case-parts">
                  <div className="case-detail-label">{t('briefing.partsUsed')}</div>
                  <div className="part-tags">
                    {c.parts_used.filter(p => p.nummer).map((p, j) => (
                      <span key={j} className="part-tag" title={p.titel || p.nummer}>
                        {p.nummer}
                        {p.titel && <span className="part-tag-name"> {p.titel}</span>}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

export function SimilarCasesCard({ cases }: { cases: SimilarCase[] }) {
  const { t } = useTranslation()

  if (!cases.length) {
    return (
      <div className="card">
        <h3 className="card-header">
          <span className="card-icon">&#128269;</span>
          {t('briefing.similarCases')}
        </h3>
        <div className="empty-state">
          <div className="empty-state-icon">&#128270;</div>
          <p>{t('briefing.similarEmpty')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="card">
      <h3 className="card-header">
        <span className="card-icon">&#128269;</span>
        {t('briefing.similarCases')}
        <span className="card-count">{cases.length}</span>
      </h3>
      <div className="cases-list">
        {cases.map((c, i) => (
          <CaseItem key={i} c={c} />
        ))}
      </div>
    </div>
  )
}
