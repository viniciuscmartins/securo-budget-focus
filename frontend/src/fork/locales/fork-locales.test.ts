// The checks upstream's src/locales/i18n.test.ts runs on its locale files,
// for the fork's own strings in src/fork/locales, plus the merge with upstream.
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import i18n from '@/lib/i18n'

const FORK_DIR = path.dirname(fileURLToPath(import.meta.url))
const UPSTREAM_DIR = path.join(FORK_DIR, '..', '..', 'locales')
const LOCALES = readdirSync(FORK_DIR).filter((f) => f.endsWith('.json')).map((f) => f.replace(/\.json$/, ''))
const PLURAL_SUFFIXES = ['_zero', '_one', '_two', '_few', '_many', '_other']

type Tree = { [key: string]: string | Tree }
const read = (dir: string, locale: string): Tree => JSON.parse(readFileSync(path.join(dir, `${locale}.json`), 'utf-8'))

function flatten(tree: Tree, prefix = ''): Map<string, string> {
  const out = new Map<string, string>()
  for (const [key, value] of Object.entries(tree)) {
    const full = prefix ? `${prefix}.${key}` : key
    if (typeof value === 'string') out.set(full, value)
    else for (const [k, v] of flatten(value, full)) out.set(k, v)
  }
  return out
}

const pluralBase = (key: string) => {
  const suffix = PLURAL_SUFFIXES.find((s) => key.endsWith(s))
  return suffix ? key.slice(0, -suffix.length) : key
}
const bases = (keys: Iterable<string>) => new Set([...keys].map(pluralBase))
const placeholders = (s: string) => new Set([...s.matchAll(/{{\s*(\w+)\s*}}/g)].map((m) => m[1]))

describe('fork locale files', () => {
  const en = flatten(read(FORK_DIR, 'en'))

  it('cover the same locales as upstream', () => {
    const upstream = readdirSync(UPSTREAM_DIR).filter((f) => f.endsWith('.json')).map((f) => f.replace(/\.json$/, ''))
    expect(LOCALES.sort()).toEqual(upstream.sort())
  })

  for (const locale of LOCALES) {
    const strings = flatten(read(FORK_DIR, locale))

    it(`${locale}: same keys as en (plural forms aside)`, () => {
      expect([...bases(strings.keys())].sort()).toEqual([...bases(en.keys())].sort())
    })

    it(`${locale}: placeholders match en`, () => {
      const mismatches: string[] = []
      for (const [key, value] of strings) {
        const enValue = en.get(key) ?? en.get(`${pluralBase(key)}_other`)
        if (enValue === undefined) continue
        for (const p of placeholders(enValue)) if (!placeholders(value).has(p)) mismatches.push(`${key}: {{${p}}}`)
      }
      expect(mismatches).toEqual([])
    })

    it(`${locale}: no key that upstream also defines`, () => {
      const upstream = flatten(read(UPSTREAM_DIR, locale))
      expect([...strings.keys()].filter((k) => upstream.has(k))).toEqual([])
    })
  }
})

describe('fork strings at runtime', () => {
  it('are in the bundled languages', () => {
    expect(i18n.getResource('en', 'translation', 'budgetPlan.title')).toBe(flatten(read(FORK_DIR, 'en')).get('budgetPlan.title'))
    expect(i18n.getResource('pt-BR', 'translation', 'budgetPlan.title')).toBe(flatten(read(FORK_DIR, 'pt-BR')).get('budgetPlan.title'))
  })

  it('come with a language loaded later, next to upstream strings', async () => {
    const before = i18n.language
    await i18n.changeLanguage('de')
    try {
      expect(i18n.t('budgetPlan.title')).toBe(flatten(read(FORK_DIR, 'de')).get('budgetPlan.title'))
      expect(i18n.t('nav.budgets')).toBe(flatten(read(UPSTREAM_DIR, 'de')).get('nav.budgets'))
    } finally {
      await i18n.changeLanguage(before)
    }
  })
})
