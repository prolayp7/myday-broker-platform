import { Link } from 'react-router-dom'
import { Route, ChevronRight, Users, Megaphone, Percent, Receipt, Globe, FileSearch } from 'lucide-react'
import { AppShell } from '@/components/AppShell'

// ─── WIREFRAME ──────────────────────────────────────────────────────────
// Admin landing hub. Its job is orientation + routing, not doing work — you
// pick a tile, then work inside that area's own screen.
//
// Communities and Brokers still exist as their own real screens/routes
// (reached via the Lead Management wizard, or directly by
// URL) but deliberately have no separate hub tile right now — revisit once
// they need to be surfaced as independent, frequently-used areas on their
// own, not just steps inside the routing wizard.
//
// Campaigns intentionally has no tile either — it's reporting/attribution
// only, with zero involvement in routing (confirmed: a lead_form mapping
// can be created and used with zero campaign rows existing at all).

interface Tile {
  title: string
  description: string
  to: string
  icon: typeof Route
  count?: string
}

const TILES: Tile[] = [
  {
    title: 'Lead Management',
    description: 'Manage communities, broker groups, and the lead forms routed to each. Onboard brokers from inside a group.',
    to: '/admin/broker-groups',
    icon: Route,
  },
  {
    title: 'Commission rates',
    description: 'The auto and property percentages every commission projection is calculated from.',
    to: '/admin/commission',
    icon: Percent,
  },
  {
    title: 'Lead cost',
    description:
      "What each broker owes for the leads the system gave them this month, less any that should not be charged. Wireframe — nothing is saved yet.",
    to: '/admin/lead-cost',
    icon: Receipt,
  },
  {
    title: 'MVR log',
    description:
      'Every MVR pulled by every broker, month by month, for the MTO register. Change the head-office password and copy its link.',
    to: '/mvr-log',
    icon: FileSearch,
  },
  {
    title: 'Leads',
    description: 'Every lead across all communities, groups and brokers. Spot leads sitting unworked in the pool, and reassign.',
    to: '/admin/leads',
    icon: Users,
  },
  {
    title: 'Meta Quality',
    description:
      'What Meta has been told about each Facebook lead, by community, group and broker — and whether Meta is getting enough Qualified leads a week to finish learning.',
    to: '/admin/capi',
    icon: Megaphone,
  },
  // Appended, not inserted: the tiles above keep the positions the admin
  // already knows.
  {
    title: 'Website leads',
    description:
      'Which brokers receive leads from the website quote form, and how many a day. Separate from Facebook routing and limits.',
    to: '/admin/website-leads',
    icon: Globe,
  },
]

export default function AdminHub() {
  return (
    <AppShell section="Admin">
      <div className="mx-auto max-w-5xl space-y-8 p-6">
        <div>
          <h1 className="text-2xl font-semibold">Admin</h1>
          <p className="text-muted-foreground mt-1 text-sm">Manage the day-to-day.</p>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {TILES.map((t) => (
            <TileCard key={t.title} tile={t} />
          ))}
        </div>
      </div>
    </AppShell>
  )
}

function TileCard({ tile }: { tile: Tile }) {
  const Icon = tile.icon
  return (
    <Link
      to={tile.to}
      className="group bg-card hover:border-primary/40 hover:shadow-sm relative flex flex-col gap-3 rounded-lg border p-5 transition"
    >
      <div className="flex items-center justify-between">
        <div className="bg-accent text-accent-foreground flex size-10 items-center justify-center rounded-lg">
          <Icon className="size-5" />
        </div>
        <ChevronRight className="text-muted-foreground group-hover:text-foreground size-4 transition" />
      </div>
      <div>
        <h3 className="font-semibold">{tile.title}</h3>
        <p className="text-muted-foreground mt-1 text-sm leading-snug">{tile.description}</p>
      </div>
      {tile.count && (
        <span className="text-muted-foreground mt-auto text-xs font-medium">{tile.count}</span>
      )}
    </Link>
  )
}
