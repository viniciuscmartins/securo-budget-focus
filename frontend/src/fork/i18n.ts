// The fork's strings live in src/fork/locales/<lang>.json, so upstream's
// locale files stay identical to upstream's and merge cleanly. They are added
// on top of upstream's bundle for each language without overwriting a key.
import type { i18n as I18n, ResourceLanguage } from 'i18next'

const forkLocales = import.meta.glob<ResourceLanguage>('./locales/*.json', {
  eager: true,
  import: 'default',
})

export function forkStrings(language: string): ResourceLanguage | undefined {
  return forkLocales[`./locales/${language}.json`]
}

function add(i18n: I18n, language: string) {
  // Only on top of a bundle upstream really loaded: a language whose file
  // failed to load must stay absent so English is the fallback.
  if (!i18n.hasResourceBundle(language, 'translation')) return
  const strings = forkStrings(language)
  if (strings) i18n.addResourceBundle(language, 'translation', strings, true, false)
}

/** Add the fork's strings to the bundled languages now, and to every other
 * language as soon as upstream's loader brings it in (the `loaded` event fires
 * before the language switch completes, so the first render has them). */
export function addForkStrings(i18n: I18n) {
  for (const language of Object.keys(i18n.store.data)) add(i18n, language)
  i18n.on('loaded', (loaded) => {
    for (const language of Object.keys(loaded)) add(i18n, language)
  })
}
