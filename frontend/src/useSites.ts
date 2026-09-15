import { useEffect, useState } from 'react'

import { api } from './api'
import type { SiteOption } from './types'

/**
 * Replaces the `cities` the Jinja template used to inject. The list now comes
 * from the backend so job sites can contribute their own city sets.
 */
export function useSites() {
  const [sites, setSites] = useState<SiteOption[]>([])

  useEffect(() => {
    let alive = true
    void api<SiteOption[]>('/api/sites')
      .then((value) => {
        if (alive) setSites(value)
      })
      .catch(() => {
        if (alive) setSites([])
      })
    return () => {
      alive = false
    }
  }, [])

  const cities: string[] = []
  for (const site of sites) {
    if (!site.enabled) continue
    for (const city of site.cities) if (!cities.includes(city)) cities.push(city)
  }
  return { sites, cities }
}