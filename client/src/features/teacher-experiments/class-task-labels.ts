// Translate only the authoritative status returned by Django; never derive it from browser time.
const labels: Record<string, string> = {
  scheduled: '未开放', open: '开放中', closed: '已截止',
  manual_closed: '已终止', cancelled: '已取消', invalidated: '已作废',
}
export function classTaskStatusLabel(status: string) { return labels[status] || status }
