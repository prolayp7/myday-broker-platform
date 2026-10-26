import axios from 'axios'
import {
  firstValidationMessage,
  MVR_BUSINESS_REASONS,
  MVR_INFO_RETRIEVED,
  type MvrPull,
} from '@/data/api'

// Shared by the MVR Log (brokers and admins) and the head-office report, so
// both print the register the same way.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "2026-10-08" -> "8 Oct 2026". Split by hand, NOT `new Date(iso)`: a bare
 *  date parses as UTC midnight, which in Ontario is the evening before — every
 *  date on the register would print a day early. */
export function longDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return `${d} ${MONTHS[m - 1]} ${y}`
}

/** The last thirteen months, newest first. Thirteen, not twelve: a line is
 *  kept for a year from its request date, so last October's lines are still
 *  live in this October. */
export function registerMonths(): { value: string; label: string }[] {
  const now = new Date()
  const out: { value: string; label: string }[] = []
  for (let i = 0; i < 13; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    out.push({
      value: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`,
      label: d.toLocaleString('en-CA', { month: 'long', year: 'numeric' }),
    })
  }
  return out
}

export function reasonLabel(value: MvrPull['business_reason']): string {
  return MVR_BUSINESS_REASONS.find((r) => r.value === value)?.label ?? value
}

export function retrievedLabel(value: MvrPull['info_retrieved']): string {
  return MVR_INFO_RETRIEVED.find((r) => r.value === value)?.label ?? value
}

/** The backend's own words when it sent some, otherwise `fallback`. */
export function apiDetail(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err) && typeof err.response?.data?.detail === 'string') {
    return err.response.data.detail
  }
  return firstValidationMessage(err) ?? fallback
}

/** The month as a CSV in the Excel template's own column order, so it can be
 *  pasted into, or handed over in place of, the sheet brokers kept by hand. */
export function downloadRegisterCsv(pulls: MvrPull[], fileName: string) {
  const header = [
    'No.',
    'Date of Request',
    'Support Document and Reference Number',
    'Business Reason',
    'Information Used to Conduct the Search',
    'Information Retrieved',
    'ARIS Order Number',
    'Completed By',
    'Date Authorized Use Completed',
    'Destruction Date',
    'Driver',
  ]
  const lines = pulls.map((p, i) => [
    String(i + 1),
    p.requested_on,
    p.licence_number,
    reasonLabel(p.business_reason),
    p.licence_masked,
    retrievedLabel(p.info_retrieved),
    p.aris_order_number,
    p.completed_by,
    p.use_completed_on,
    p.destruction_on,
    p.driver_name ?? '',
  ])
  const csv = [header, ...lines]
    .map((row) => row.map((cell) => `"${cell.replace(/"/g, '""')}"`).join(','))
    .join('\r\n')
  // The BOM makes Excel read it as UTF-8, so an accented name survives.
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  a.click()
  URL.revokeObjectURL(url)
}
