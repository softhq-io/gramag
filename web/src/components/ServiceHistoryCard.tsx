import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ServiceJob, Comment as CommentType } from '../api/mission'

function highlightNames(text: string): string {
  // Fix missing spaces from HTML-stripped text (camelCase artifacts in German)
  const spaced = text.replace(/([a-zäöüß])([A-ZÄÖÜ])/g, '$1 $2')
  // Highlight names only after greeting/sign-off words
  return spaced.replace(
    /\b(Hallo|Hoi|Morge|Gruss|Gruess|Grüss|Ciao|Danke|Hi|Liebe[rn]?|Von|von)\s+([A-ZÄÖÜ][a-zäöüß]+(?:\s[A-ZÄÖÜ][a-zäöüß]+)?)/g,
    '$1 <span class="comment-name">$2</span>'
  )
}

function CommentBubble({ comment }: { comment: CommentType }) {
  const body = comment.text ? highlightNames(comment.text) : ''
  return (
    <div className="comment-bubble">
      {comment.author && <span className="comment-author">{comment.author}</span>}
      <span className="comment-body" dangerouslySetInnerHTML={{ __html: body }} />
    </div>
  )
}

export function ServiceHistoryCard({ history }: { history: ServiceJob[] }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState<string | null>(null)

  if (!history || !history.length) {
    return (
      <div className="card">
        <h3 className="card-header">
          <span className="card-icon">&#128197;</span>
          {t('briefing.history')}
        </h3>
        <div className="empty-state">
          <div className="empty-state-icon">&#128203;</div>
          <p>{t('briefing.historyEmpty')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="card">
      <h3 className="card-header">
        <span className="card-icon">&#128197;</span>
        {t('briefing.history')}
        <span className="card-count">{history.length}</span>
      </h3>
      <div className="timeline">
        {history.map(job => (
          <div
            key={job.erp_id}
            className={`timeline-item ${expanded === job.erp_id ? 'expanded' : ''}`}
            onClick={() => setExpanded(expanded === job.erp_id ? null : job.erp_id)}
          >
            <div className="timeline-dot" />
            <div className="timeline-content">
              <div className="timeline-header">
                <span className="timeline-date">{job.date}</span>
                <span className="timeline-title">{job.title}</span>
              </div>
              {expanded === job.erp_id && (
                <div className="timeline-detail">
                  {job.parts && job.parts.filter(p => p.nummer).length > 0 && (
                    <div className="detail-section">
                      <span className="detail-label">{t('briefing.parts')}:</span>
                      <div className="part-tags">
                        {job.parts.filter(p => p.nummer).map((p, i) => (
                          <span key={i} className="part-tag">{p.nummer}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {job.comments && job.comments.filter(c => c.text).length > 0 && (
                    <div className="detail-section">
                      <span className="detail-label">{t('briefing.comments')}:</span>
                      <div className="comments-list">
                        {job.comments.filter(c => c.text).map((c, i) => (
                          <CommentBubble key={i} comment={c} />
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
            <span className="timeline-chevron">{expanded === job.erp_id ? '\u25BE' : '\u25B8'}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
