import { useTranslation } from 'react-i18next'

export function LanguageToggle() {
  const { i18n } = useTranslation()
  const toggle = () => {
    const next = i18n.language === 'de' ? 'en' : 'de'
    i18n.changeLanguage(next)
    localStorage.setItem('lang', next)
  }
  return (
    <button className="lang-toggle" onClick={toggle}>
      {i18n.language === 'de' ? 'EN' : 'DE'}
    </button>
  )
}
