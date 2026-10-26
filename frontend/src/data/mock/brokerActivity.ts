// TEMPORARY scaffold for the Brokers / broker-activity screens. Built against
// this mock data first so the layout can be reviewed with a realistic dataset
// (the real DB only has 1 lead today) — swapped for the real endpoints
// (GET /broker-groups -> pills, GET /brokers/{id}/leads -> history) right
// after the layout is approved. Not wired into api.ts's USE_MOCK seam on
// purpose — these two screens import this file directly, so the swap is a
// clean, visible diff in the two screen files, not a hidden branch.

export interface MockGroup {
  id: string
  label: string // "Dragon · Auto Team 1"
}

export interface MockBrokerSummary {
  id: string
  group_id: string
  name: string
  status: 'active' | 'paused'
  leads_taken: number
}

export type MockStage =
  | 'New'
  | 'In Progress'
  | 'Quote Done'
  | 'Pending'
  | 'Sold'
  | 'Submitted'
  | 'Issued'
  | 'Lost'

export interface MockBrokerLead {
  id: string
  name: string
  phone: string
  source: 'meta' | 'website' | 'tiktok'
  assigned_by: 'system' | 'admin'
  reassigned_from: string | null // broker name, only when assigned_by === 'admin'
  stage: MockStage
  delivered_at: string // ISO
}

export const MOCK_GROUPS: MockGroup[] = [
  { id: 'g-dragon-1', label: 'Dragon · Auto Team 1' },
  { id: 'g-tiger-1', label: 'Tiger · Auto Team 1' },
  { id: 'g-nature-admin', label: 'Nature · Admin Team' },
  { id: 'g-gta', label: 'Gta Team' },
]

export const MOCK_BROKERS: MockBrokerSummary[] = [
  { id: 'b-sarah', group_id: 'g-tiger-1', name: 'Sarah Mitchell', status: 'active', leads_taken: 47 },
  { id: 'b-kwame', group_id: 'g-tiger-1', name: 'Kwame Boateng', status: 'active', leads_taken: 28 },
  { id: 'b-david', group_id: 'g-dragon-1', name: 'David Osei', status: 'active', leads_taken: 31 },
  { id: 'b-mike', group_id: 'g-dragon-1', name: 'Mike Torres', status: 'paused', leads_taken: 52 },
]

// Keyed by broker id. Each broker has a few leads across a couple of days —
// enough to show the day-grouping and the "+N more" collapse in the wireframe.
export const MOCK_BROKER_LEADS: Record<string, MockBrokerLead[]> = {
  'b-sarah': [
    {
      id: 'l1', name: 'Jordan Silva', phone: '(416) 555-0142', source: 'meta',
      assigned_by: 'system', reassigned_from: null, stage: 'New',
      delivered_at: new Date(new Date().setHours(9, 2)).toISOString(),
    },
    {
      id: 'l2', name: 'Tyler Nguyen', phone: '(437) 555-0116', source: 'meta',
      assigned_by: 'system', reassigned_from: null, stage: 'Quote Done',
      delivered_at: new Date(new Date().setHours(10, 41)).toISOString(),
    },
    {
      id: 'l3', name: 'Priya Anand', phone: '(905) 555-0166', source: 'website',
      assigned_by: 'admin', reassigned_from: 'David Osei', stage: 'New',
      delivered_at: new Date(new Date().setHours(11, 20)).toISOString(),
    },
    {
      id: 'l4', name: 'Amara Chen', phone: '(647) 555-0188', source: 'meta',
      assigned_by: 'system', reassigned_from: null, stage: 'Issued',
      delivered_at: new Date(Date.now() - 86400000).toISOString(),
    },
    {
      id: 'l5', name: 'Raj Patel', phone: '(905) 555-0173', source: 'meta',
      assigned_by: 'system', reassigned_from: null, stage: 'Lost',
      delivered_at: new Date(Date.now() - 86400000 - 3600000).toISOString(),
    },
  ],
  'b-kwame': [
    {
      id: 'l6', name: 'Lena Kowalski', phone: '(416) 555-0129', source: 'website',
      assigned_by: 'system', reassigned_from: null, stage: 'Submitted',
      delivered_at: new Date(Date.now() - 86400000).toISOString(),
    },
  ],
  'b-david': [
    {
      id: 'l7', name: 'Owen Baptiste', phone: '(647) 555-0141', source: 'meta',
      assigned_by: 'system', reassigned_from: null, stage: 'Quote Done',
      delivered_at: new Date(new Date().setHours(8, 30)).toISOString(),
    },
  ],
  'b-mike': [],
}
