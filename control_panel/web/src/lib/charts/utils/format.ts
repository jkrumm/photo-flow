// Vendored from argo/packages/charts — keep in sync manually.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const SHORT_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

/** Axis-friendly short date: DD.MM */
export function fmtAxisDate(value: unknown): string {
  if (value instanceof Date) {
    const dd = String(value.getDate()).padStart(2, '0')
    const mm = String(value.getMonth() + 1).padStart(2, '0')
    return `${dd}.${mm}`
  }
  const s = String(value ?? '')
  const match = s.match(/(\d{4})-(\d{2})-(\d{2})/)
  if (match) return `${match[3]}.${match[2]}`
  return s
}

/** Tooltip-friendly long date: "Mon Apr 21 2026" */
export function fmtTooltipDate(date: unknown): string {
  if (date instanceof Date) {
    return `${SHORT_DAYS[date.getDay()]} ${MONTHS[date.getMonth()]} ${date.getDate()} ${date.getFullYear()}`
  }
  const s = String(date ?? '')
  const match = s.match(/(\d{4})-(\d{2})-(\d{2})/)
  if (!match) return s
  const d = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return `${SHORT_DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()} ${d.getFullYear()}`
}
