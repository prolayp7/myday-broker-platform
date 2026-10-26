import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { ArrowLeft, Plus, Trash2 } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

// ─── WIREFRAME ONLY ──────────────────────────────────────────────────────
// Static placeholder data, no backend calls. Nothing here persists.
//
// Campaign is reporting/attribution only — it does NOT route leads.
// Meta's real leadgen webhook never carries campaign_id directly (only
// leadgen_id, form_id, adgroup_id, ad_id); getting campaign context needs a
// separate, non-guaranteed follow-up API call. Routing must never block on
// that, so the routing decision lives on LeadForm (form_id -> broker group)
// instead — managed from a broker group's detail screen (BrokerGroupDetail.tsx).

const MOCK_PLATFORMS = ['meta', 'tiktok', 'website']
const MOCK_PRODUCTS = ['Auto', 'Travel', 'Business', 'Life']

interface MockCampaign {
  id: string
  name: string
  source: string
  product: string
  platformCampaignId: string
}

const INITIAL_CAMPAIGNS: MockCampaign[] = [
  { id: '1', name: 'Auto — Winter Promo', source: 'meta', product: 'Auto', platformCampaignId: '120210…884' },
  { id: '2', name: 'Travel — Super Visa Q1', source: 'tiktok', product: 'Travel', platformCampaignId: '723118…091' },
  { id: '3', name: 'Life — Referral Drive', source: 'website', product: 'Life', platformCampaignId: '' },
]

export default function CampaignList() {
  const [campaigns, setCampaigns] = useState<MockCampaign[]>(INITIAL_CAMPAIGNS)
  const [newOpen, setNewOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<MockCampaign | null>(null)

  return (
    <AppShell section="Admin · Campaigns">
      <div className="mx-auto max-w-5xl space-y-4 p-6">
        <div className="flex items-center justify-between">
          <div>
            <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
              <Link to="/admin">
                <ArrowLeft /> Admin
              </Link>
            </Button>
            <h1 className="text-2xl font-semibold">Campaigns</h1>
          </div>
          <Button onClick={() => setNewOpen(true)}>
            <Plus /> New Campaign
          </Button>
        </div>

        <p className="text-muted-foreground text-sm">
          A campaign mirrors a real Meta/TikTok campaign, for reporting and attribution.
          It does not decide where leads route — that's set up separately in{' '}
          <Link to="/admin/broker-groups" className="text-primary underline underline-offset-2">
            Lead Management
          </Link>
          , since Meta's webhook identifies a lead by its form, not its campaign.
        </p>

        <div className="overflow-hidden rounded-lg border bg-card">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted-foreground border-b text-left">
                <th className="px-4 py-2.5 font-medium">Campaign</th>
                <th className="px-4 py-2.5 font-medium">Source</th>
                <th className="px-4 py-2.5 font-medium">Product</th>
                <th className="px-4 py-2.5 font-medium">Platform Campaign ID</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {campaigns.length === 0 ? (
                <tr>
                  <td colSpan={5} className="text-muted-foreground p-8 text-center">
                    No campaigns yet — create one to track it.
                  </td>
                </tr>
              ) : (
                campaigns.map((c) => (
                  <tr key={c.id} className="hover:bg-muted border-b last:border-0">
                    <td className="px-4 py-2.5 font-medium">{c.name}</td>
                    <td className="text-muted-foreground px-4 py-2.5 capitalize">{c.source}</td>
                    <td className="text-muted-foreground px-4 py-2.5">{c.product}</td>
                    <td className="text-muted-foreground px-4 py-2.5">
                      {c.platformCampaignId || '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="text-muted-foreground hover:text-error"
                        onClick={() => setDeleteTarget(c)}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <NewCampaignDialog
        open={newOpen}
        onOpenChange={setNewOpen}
        onCreated={(c) => setCampaigns((cur) => [...cur, c])}
      />

      <DeleteCampaignDialog
        campaign={deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        onDeleted={(id) => setCampaigns((cur) => cur.filter((c) => c.id !== id))}
      />
    </AppShell>
  )
}

function NewCampaignDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  onCreated: (c: MockCampaign) => void
}) {
  const [name, setName] = useState('')
  const [source, setSource] = useState('')
  const [product, setProduct] = useState('')
  const [platformCampaignId, setPlatformCampaignId] = useState('')

  const canCreate = name.trim() && source && product

  function handleCreate() {
    onCreated({
      id: crypto.randomUUID(),
      name: name.trim(),
      source,
      product,
      platformCampaignId: platformCampaignId.trim(),
    })
    toast('Campaign created (wireframe — not saved)')
    setName('')
    setSource('')
    setProduct('')
    setPlatformCampaignId('')
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New campaign</DialogTitle>
          <DialogDescription>
            Mirror a real Meta/TikTok campaign for reporting. Routing is set up separately
            in Lead Forms.
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Name" required className="sm:col-span-2">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Auto — Winter Promo"
            />
          </Field>

          <Field label="Source" required>
            <SelectBox value={source} onChange={setSource} placeholder="Ad platform" items={MOCK_PLATFORMS} capitalize />
          </Field>

          <Field label="Product" required>
            <SelectBox value={product} onChange={setProduct} placeholder="Line of business" items={MOCK_PRODUCTS} />
          </Field>

          <Field label="Platform Campaign ID" className="sm:col-span-2">
            <Input
              value={platformCampaignId}
              onChange={(e) => setPlatformCampaignId(e.target.value)}
              placeholder="Paste from Meta/TikTok Ads Manager (optional)"
            />
          </Field>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canCreate} onClick={handleCreate}>
            Create campaign
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DeleteCampaignDialog({
  campaign,
  onOpenChange,
  onDeleted,
}: {
  campaign: MockCampaign | null
  onOpenChange: (o: boolean) => void
  onDeleted: (id: string) => void
}) {
  return (
    <Dialog open={!!campaign} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete campaign?</DialogTitle>
          <DialogDescription>
            {campaign && `${campaign.name} will be permanently deleted. This can't be undone.`}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              if (campaign) {
                onDeleted(campaign.id)
                toast('Campaign deleted (wireframe)')
              }
              onOpenChange(false)
            }}
          >
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// Small local helpers to keep the wireframe form tidy.
function Field({
  label,
  required,
  className,
  children,
}: {
  label: string
  required?: boolean
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={`space-y-1.5 ${className ?? ''}`}>
      <Label className="text-muted-foreground text-xs font-normal">
        {label} {required && <span className="text-error">*</span>}
      </Label>
      {children}
    </div>
  )
}

function SelectBox({
  value,
  onChange,
  placeholder,
  items,
  capitalize,
}: {
  value: string
  onChange: (v: string) => void
  placeholder: string
  items: string[]
  capitalize?: boolean
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-full">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {items.map((it) => (
          <SelectItem key={it} value={it} className={capitalize ? 'capitalize' : ''}>
            {it}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
