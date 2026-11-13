export type QuestionImages = Record<string, string>

export function hasQuestionImage(text: string) {
  return /\{\{image:[a-f0-9]{64}\.(?:png|jpg|webp)\}\}/.test(text)
}

export function questionContentSummary(text: string) {
  return text.replace(/\{\{image:[a-f0-9]{64}\.(?:png|jpg|webp)\}\}/g, '[图片]').replace(/\{\{b[0-9]{2,}\}\}/g, '____')
}

export function questionContentParts(text: string, images?: QuestionImages) {
  const parts: Array<{ text: string; source?: string }> = []
  let cursor = 0
  for (const match of text.matchAll(/\{\{image:([a-f0-9]{64}\.(?:png|jpg|webp))\}\}/g)) {
    if (match.index > cursor) parts.push({ text: text.slice(cursor, match.index) })
    const source = images?.[match[1]!]
    parts.push({ text: match[0], ...(/^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/.test(source || '') ? { source } : {}) })
    cursor = match.index + match[0].length
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor) })
  return parts
}
