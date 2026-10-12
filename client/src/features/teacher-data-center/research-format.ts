export function formatResearchTime(value: string, timeOnly = false): string {
  if (!value) return '未记录'
  const compact = value.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(\.\d+)?$/)
  if (compact) {
    const time = `${compact[4]}:${compact[5]}:${compact[6]}${compact[7] || ''}`
    return timeOnly ? time : `${compact[1]}-${compact[2]}-${compact[3]} ${time}`
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    ...(timeOnly ? {} : { year: 'numeric', month: '2-digit', day: '2-digit' } as const),
    hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3, hour12: false,
  }).format(date).replaceAll('/', '-')
}
