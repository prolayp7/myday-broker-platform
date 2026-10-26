// My Day dashboard data — ported from 02-my-day.html.
// (Pipeline counts here mirror the wireframe's static values; the live
//  My Leads screen derives its rail counts from the actual leads array.)

export interface MyDayData {
  broker: { name: string; available: boolean }
  closed: {
    auto: number
    home: number
    travel: number
    total: number
    policies: number
    vsLastMonth: number
  }
  stages: { key: string; label: string; count: number; state: string }[]
  attention: {
    overdue: number
    dueToday: number
    stale: number
    tickets: number
  }
}

export interface Broker {
  name: string
  initials: string
  role: string
  avatarUrl: string
  available: boolean
}

export const MOCK_BROKER: Broker = {
  name: 'Sarah Mitchell',
  initials: 'SM',
  role: 'Broker',
  avatarUrl: 'https://i.pravatar.cc/96?img=47',
  available: true,
}

export const MOCK_MY_DAY: MyDayData = {
  broker: { name: 'Sarah Mitchell', available: true },
  closed: {
    auto: 9920,
    home: 1400,
    travel: 320,
    total: 11640,
    policies: 7,
    vsLastMonth: 18,
  },
  stages: [
    { key: 'new', label: 'New', count: 4, state: 'neutral' },
    { key: 'called', label: 'Called', count: 6, state: 'neutral' },
    { key: 'contacted', label: 'Contacted', count: 3, state: 'neutral' },
    { key: 'followup', label: 'Follow Up', count: 3, state: 'warning' },
    { key: 'quoted', label: 'Quoted', count: 2, state: 'neutral' },
    { key: 'awaiting', label: 'Awaiting', count: 1, state: 'warning' },
    { key: 'sold', label: 'Sold', count: 5, state: 'success' },
    { key: 'submitted', label: 'Submitted', count: 2, state: 'success' },
    { key: 'issued', label: 'Issued', count: 1, state: 'success' },
    { key: 'notint', label: 'Not Interested', count: 7, state: 'error' },
    { key: 'invalid', label: 'Invalid', count: 2, state: 'error' },
  ],
  attention: { overdue: 2, dueToday: 3, stale: 4, tickets: 1 },
}
