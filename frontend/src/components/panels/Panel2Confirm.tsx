import { useEffect, useState } from 'react'

import type { Profile, RoleRecommendation, SiteOption } from '../../types'

interface Card {
  base: RoleRecommendation
  selected: boolean
  role: string
  keywords: string
}

interface Props {
  active: boolean
  profile: Profile | null
  cities: string[]
  sites: SiteOption[]
  onSubmit: (payload: Record<string, unknown>, sites: string[]) => Promise<void>
  onToast: (message: string) => void
}

export default function Panel2Confirm({
  active, profile, cities, sites, onSubmit, onToast,
}: Props) {
  const [cards, setCards] = useState<Card[]>([])
  const [selectedCities, setSelectedCities] = useState<string[]>([])
  const [selectedSites, setSelectedSites] = useState<string[]>([])
  const [salary, setSalary] = useState('')
  const [years, setYears] = useState('')
  const [graduationYear, setGraduationYear] = useState('')

  // Re-seed whenever a different profile arrives (a fresh upload).
  useEffect(() => {
    if (!profile) return
    const chosen = new Set((profile.selected_roles ?? []).map((item) => item.role))
    setCards(
      profile.recommendations.map((role, index) => ({
        base: role,
        selected: chosen.size ? chosen.has(role.role) : index < 2,
        role: role.role,
        keywords: role.keywords.join('，'),
      })),
    )
    setSelectedCities(profile.cities ?? [])
    setSalary(profile.salary_preference ?? '')
    setYears(profile.experience_years == null ? '' : String(profile.experience_years))
    setGraduationYear(profile.expected_graduation_year == null ? '' : String(profile.expected_graduation_year))
  }, [profile])

  // The product flow runs one board at a time. Selecting another board replaces
  // the previous choice, so the browser never starts at an unexpected site.
  useEffect(() => {
    const enabled = sites.filter((site) => site.enabled).map((site) => site.key)
    if (!enabled.length) return
    setSelectedSites((current) => {
      const kept = current.find((key) => enabled.includes(key))
      return [kept ?? enabled[0]]
    })
  }, [sites])

  const toggleSite = (key: string) => {
    setSelectedSites([key])
  }

  const strip = [
    ...(profile?.skills ?? []),
    profile?.education,
    profile?.experience_years != null ? `${profile.experience_years} 年经验` : null,
  ].filter(Boolean) as string[]

  const toggleCard = (index: number) => {
    setCards((current) => {
      const next = [...current]
      const target = next[index]
      // The old UI allowed at most two directions; keep that rule.
      if (!target.selected && next.filter((item) => item.selected).length >= 2) {
        onToast('最多选择两个岗位方向')
        return current
      }
      next[index] = { ...target, selected: !target.selected }
      return next
    })
  }

  const toggleCity = (city: string) => {
    setSelectedCities((current) => {
      if (current.includes(city)) return current.filter((item) => item !== city)
      if (current.length >= 2) {
        onToast('最多选择两个城市')
        return current
      }
      return [...current, city]
    })
  }

  const submit = () => {
    const picked = cards.filter((item) => item.selected)
    if (!picked.length || !selectedCities.length) {
      onToast('请至少选择一个岗位方向和一个城市')
      return
    }
    const selected_roles = picked.map((card) => ({
      ...card.base,
      role: card.role.trim(),
      keywords: card.keywords
        .split(/[，,]/)
        .map((value) => value.trim())
        .filter(Boolean)
        .slice(0, 4),
    }))
    void onSubmit({
      selected_roles,
      cities: selectedCities,
      salary_preference: salary || null,
      experience_years: years ? Number(years) : null,
      expected_graduation_year: graduationYear ? Number(graduationYear) : null,
    }, selectedSites)
  }

  return (
    <section
      className={active ? 'panel active' : 'panel'}
      id="panel-2"
      aria-labelledby="confirm-title"
    >
      <div className="panel-heading">
        <span>02</span>
        <div>
          <h2 id="confirm-title">确认岗位方向</h2>
          <p>最多选择 2 个方向和 2 个城市，岗位名和关键词都能修改。</p>
        </div>
      </div>
      <div className="profile-strip" id="profile-strip">
        {strip.map((value) => <span key={value}>{value}</span>)}
      </div>
      <form
        id="confirm-form"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <fieldset className="site-picker">
          <legend>抓取平台 <small>单选</small></legend>
          <div className="chips" id="site-options">
            {sites.filter((site) => site.enabled).map((site) => (
              <label key={site.key}>
                <input
                  type="radio"
                  name="sites"
                  value={site.key}
                  checked={selectedSites.includes(site.key)}
                  onChange={() => toggleSite(site.key)}
                />
                <span>{site.label}</span>
              </label>
            ))}
          </div>
          <p className="site-note">每次任务只采集一个平台；切换平台会替换当前选择。</p>
        </fieldset>
        <div id="role-cards" className="role-cards">
          {cards.map((card, index) => (
            <article
              key={`${card.base.role}-${index}`}
              className={card.selected ? 'role-card selected' : 'role-card'}
              data-index={index}
            >
              <input
                className="choose"
                type="checkbox"
                checked={card.selected}
                aria-label="选择岗位"
                onChange={() => toggleCard(index)}
              />
              <span className="confidence">{card.base.confidence}置信度</span>
              <input
                className="role-name"
                type="text"
                value={card.role}
                aria-label="岗位名称"
                onChange={(event) =>
                  setCards((current) =>
                    current.map((item, i) => (i === index ? { ...item, role: event.target.value } : item)),
                  )
                }
              />
              <input
                className="keywords"
                type="text"
                value={card.keywords}
                aria-label="搜索关键词"
                onChange={(event) =>
                  setCards((current) =>
                    current.map((item, i) =>
                      i === index ? { ...item, keywords: event.target.value } : item,
                    ),
                  )
                }
              />
              <textarea className="rationale" readOnly value={card.base.rationale} />
              <button
                type="button"
                className="evidence-link"
                onClick={() =>
                  onToast(card.base.citations.map((item) => `“${item.quote}”`).join(' · '))
                }
              >
                查看 {card.base.citations.length} 条简历证据
              </button>
            </article>
          ))}
        </div>
        <div className="preference-grid">
          <fieldset>
            <legend>目标城市 <small>最多 2 个</small></legend>
            <div className="chips" id="city-options">
              {cities.map((city) => (
                <label key={city}>
                  <input
                    type="checkbox"
                    name="cities"
                    value={city}
                    checked={selectedCities.includes(city)}
                    onChange={() => toggleCity(city)}
                  />
                  <span>{city}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <label>
            <span>期望薪资</span>
            <input
              id="salary"
              placeholder="例如 20–30K，可留空"
              value={salary}
              onChange={(event) => setSalary(event.target.value)}
            />
          </label>
          <label>
            <span>工作年限</span>
            <input
              id="years"
              type="number"
              min={0}
              max={50}
              step={0.5}
              placeholder="无法识别时可补充"
              value={years}
              onChange={(event) => setYears(event.target.value)}
            />
          </label>
          <label>
            <span>毕业年份</span>
            <input
              id="graduation-year"
              type="number"
              min={2000}
              max={2100}
              placeholder="例如 2027，可留空"
              value={graduationYear}
              onChange={(event) => setGraduationYear(event.target.value)}
            />
          </label>
        </div>
        <button className="primary" type="submit">
          确认并搜索岗位 <span>→</span>
        </button>
      </form>
    </section>
  )
}
