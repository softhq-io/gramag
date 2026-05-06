import { useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { marked } from 'marked'

const SECTION_ICONS: Record<string, string> = {
  'Maschinenübersicht': '&#9881;',    // gear
  'Machine Overview': '&#9881;',
  'Symptom-Analyse': '&#128269;',      // magnifying glass
  'Symptom Analysis': '&#128269;',
  'Empfohlene Teile': '&#128295;',     // wrench
  'Recommended Parts': '&#128295;',
  'Bekannte Lösungen': '&#128161;',    // lightbulb
  'Known Solutions': '&#128161;',
  'Hinweise': '&#9888;',              // warning
  'Notes': '&#9888;',
}

function getIcon(title: string): string {
  for (const [key, icon] of Object.entries(SECTION_ICONS)) {
    if (title.includes(key)) return icon
  }
  return '&#8226;'
}

export function BriefingSummary({ summary }: { summary: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const handleClick = useCallback((e: React.MouseEvent) => {
    const target = e.target as HTMLElement
    const partNummer = target.closest<HTMLElement>('[data-part]')?.dataset.part
    if (partNummer) {
      e.preventDefault()
      navigate(`/part/${partNummer}`)
    }
  }, [navigate])

  const sections = useMemo(() => {
    marked.setOptions({ breaks: true, gfm: true })

    let rendered = marked.parse(summary) as string

    // Replace <code>NNNNN</code> (from markdown backticks) with clickable links
    rendered = rendered.replace(
      /<code>(\d{4,5})<\/code>/g,
      '<a href="/einsatzplaner/part/$1" class="part-ref part-ref-link" data-part="$1">$1</a>'
    )
    // Replace remaining bare part numbers (exclude years 1900-2099)
    rendered = rendered.replace(
      /(?<!data-part=")\b((?!19\d\d|20\d\d)\d{4,5})\b(?=[:\s,.])/g,
      '<a href="/einsatzplaner/part/$1" class="part-ref part-ref-link" data-part="$1">$1</a>'
    )

    // Split into sections on <p><strong>N. Title:</strong></p> pattern
    const sectionRegex = /<p><strong>(\d+)\.\s*(.+?):?<\/strong><\/p>/g
    const parts: { num: string; title: string; content: string }[] = []
    let header = ''
    let match

    // Collect all section boundaries
    const boundaries: { index: number; end: number; num: string; title: string }[] = []
    while ((match = sectionRegex.exec(rendered)) !== null) {
      boundaries.push({
        index: match.index,
        end: match.index + match[0].length,
        num: match[1],
        title: match[2].replace(/:$/, ''),
      })
    }

    if (boundaries.length === 0) {
      // No sections found — return as single block
      return [{ num: '', title: '', content: rendered, header: '' }]
    }

    // Everything before first section is the header (## title)
    header = rendered.slice(0, boundaries[0].index)

    for (let i = 0; i < boundaries.length; i++) {
      const start = boundaries[i].end
      const end = i + 1 < boundaries.length ? boundaries[i + 1].index : rendered.length
      parts.push({
        num: boundaries[i].num,
        title: boundaries[i].title,
        content: rendered.slice(start, end).trim(),
      })
    }

    return [
      ...(header.trim() ? [{ num: '', title: '', content: header, header: '' }] : []),
      ...parts.map(p => ({ ...p, header: '' })),
    ]
  }, [summary])

  return (
    <div className="card summary-card">
      <h3 className="card-header">
        <span className="summary-icon">&#9733;</span>
        {t('briefing.summary')}
      </h3>
      <div className="summary-sections" onClick={handleClick}>
        {sections.map((sec, i) =>
          sec.num ? (
            <div key={i} className="summary-section">
              <div className="summary-section-header">
                <span
                  className="summary-section-icon"
                  dangerouslySetInnerHTML={{ __html: getIcon(sec.title) }}
                />
                <span className="summary-section-num">{sec.num}</span>
                <span className="summary-section-title">{sec.title}</span>
              </div>
              <div
                className="summary-section-body"
                dangerouslySetInnerHTML={{ __html: sec.content }}
              />
            </div>
          ) : (
            <div
              key={i}
              className="summary-header-block"
              dangerouslySetInnerHTML={{ __html: sec.content }}
            />
          )
        )}
      </div>
    </div>
  )
}
