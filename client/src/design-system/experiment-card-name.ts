export function experimentCardName(name: string) {
  const suffix = name.match(/^(.*\S)\s*（([^（）]+)）$/) || name.match(/^(.*\S)\s*\(([^()]+)\)$/)
  return suffix ? { title: suffix[1]!.trim(), subtitle: suffix[2]!.trim() } : { title: name, subtitle: '' }
}
