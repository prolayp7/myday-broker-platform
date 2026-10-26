import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import axios from 'axios'
import { ArrowLeft, AlertTriangle, Check, Plus } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { BrokerDocumentsSection } from '@/components/BrokerDocumentsSection'
import { GeneratePasswordPanel } from '@/components/GeneratePasswordPanel'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { EoTermSummary, MultiSelectField, SelectField, TextField } from '@/routes/leads/fields'
import {
  CANADA_PROVINCE_ITEMS,
  ROLE_ITEMS,
  LINE_OF_AUTHORITY_ITEMS,
  type LineOfAuthority,
} from '@/domain/broker'
import {
  getCommunities,
  getBrokerGroups,
  createBrokerGroup,
  getLeadForms,
  createLeadForm,
  createBroker,
  firstValidationMessage,
  type Community,
  type BrokerGroup,
  type LeadForm,
} from '@/data/api'

// Broker onboarding always follows the real dependency chain: a broker
// belongs to exactly one broker group, and a broker group belongs to
// exactly one community. So this flow starts by resolving the community
// (pick an existing one, or create a new one), then a broker group within
// that community (each group caps at MAX_BROKERS_PER_GROUP, purely an
// org/headcount split — it has no bearing on lead routing), and finally the
// broker form itself (Step 3). This wizard is the one place brokers are
// CREATED; viewing/editing an existing broker lives on the broker detail
// screen instead.
//
// Lead-form mapping (which Meta/TikTok form routes to this group) is shown
// and managed right here as part of group setup, instead of a separate
// Lead Forms page — a group's routing is just an attribute of the group.
// This mirrors the same group-creation shape as the standalone "New Group"
// dialog on the Lead Management home screen — both create a group, both
// offer to map a form, so an admin gets the same result either way.
//
// Fully wired to the real backend — every create here persists.

const MAX_BROKERS_PER_GROUP = 10

const STEP_LABELS = ['Community', 'Broker Group', 'Broker'] as const

function StepIndicator({ current }: { current: number }) {
  return (
    <div className="flex items-center">
      {STEP_LABELS.map((label, i) => {
        const n = i + 1
        const status = n < current ? 'done' : n === current ? 'current' : 'upcoming'
        return (
          <div key={label} className="flex flex-1 items-center last:flex-none">
            <div className="flex flex-col items-center gap-1.5">
              <div
                className={
                  'flex size-8 items-center justify-center rounded-full text-xs font-semibold ' +
                  (status === 'done'
                    ? 'bg-success text-success-foreground'
                    : status === 'current'
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-accent text-muted-foreground')
                }
              >
                {status === 'done' ? <Check className="size-4" /> : n}
              </div>
              <span
                className={
                  'text-xs font-medium ' +
                  (status === 'upcoming' ? 'text-muted-foreground' : 'text-foreground')
                }
              >
                {label}
              </span>
            </div>
            {i < STEP_LABELS.length - 1 && (
              <div className={'mx-2 h-px flex-1 ' + (status === 'done' ? 'bg-success' : 'bg-border')} />
            )}
          </div>
        )
      })}
    </div>
  )
}

function groupLabel(g: BrokerGroup): string {
  return g.name || `Group ${g.group_number}`
}

interface BrokerForm {
  name: string
  email: string
  phone: string
  role: string
  dateOfBirth: string
  address: string
  riboLicenseNumber: string
  linesOfAuthority: LineOfAuthority[]
  residentProvince: string
  licenseExpiryDate: string
  eoStartDate: string
  eoExpiryDate: string
  dailyCap: string
  maxCap: string
  // Commission split. Pre-filled with the house split and editable — a broker
  // on different terms is set here at onboarding rather than as a later edit.
}

function emptyBrokerForm(): BrokerForm {
  return {
    name: '', email: '', phone: '', role: 'broker',
    dateOfBirth: '', address: '', riboLicenseNumber: '', linesOfAuthority: [],
    residentProvince: 'Ontario', licenseExpiryDate: '', eoStartDate: '',
    eoExpiryDate: '', dailyCap: '10', maxCap: '10',
    // Placeholders only — loadAll() replaces these with the real house split
    // from GET /brokerage-settings the moment it lands. They exist so the
    // fields are never briefly empty on first paint.
  }
}

export default function OnboardBroker() {
  const navigate = useNavigate()

  // ?group=<id> — set when this was opened from INSIDE a group, which already
  // answers both of the first two steps. Asking an admin to pick the community
  // and group they are demonstrably already standing in is a wizard walking
  // them back through a decision they have made.
  const [searchParams] = useSearchParams()
  const presetGroupId = searchParams.get('group') ?? ''

  const [communities, setCommunities] = useState<Community[]>([])
  const [groups, setGroups] = useState<BrokerGroup[]>([])
  const [leadForms, setLeadForms] = useState<LeadForm[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedCommunityId, setSelectedCommunityId] = useState('')

  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [selectedGroupId, setSelectedGroupId] = useState('')
  const [creatingGroup, setCreatingGroup] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [formPlatformId, setFormPlatformId] = useState('')
  const [creatingGroupBusy, setCreatingGroupBusy] = useState(false)
  const [mappingFormBusy, setMappingFormBusy] = useState(false)
  const [creatingBrokerBusy, setCreatingBrokerBusy] = useState(false)
  // Set once a broker is created — swaps the form for a one-time reveal of
  // the generated temp password (shown here and nowhere else again).
  const [onboarded, setOnboarded] = useState<{ id: string; name: string } | null>(null)

  async function loadAll() {
    setLoading(true)
    try {
      const [communityData, groupData, leadFormData] = await Promise.all([
        getCommunities(),
        getBrokerGroups(),
        getLeadForms(),
      ])
      setCommunities(communityData)
      setGroups(groupData)
      setLeadForms(leadFormData)
      brokerFormCtl.reset(emptyBrokerForm())
    } catch {
      toast.error('Could not reach the backend — is it running?')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadAll()
  }, [])

  // Jump straight to the broker form once the groups have loaded and one was
  // named in the URL. Waits for the data rather than setting it immediately:
  // step 3 reads selectedGroup out of `groups`, and rendering before that
  // arrives would show a form with no group behind it.
  useEffect(() => {
    if (!presetGroupId || groups.length === 0) return
    const preset = groups.find((g) => g.id === presetGroupId)
    // Ignored if the id names a group that no longer exists, or the Recycle
    // Bin — which the backend refuses to onboard into anyway. Falling back to
    // the full wizard is better than a form that cannot submit.
    if (!preset || preset.is_recycle_bin) return
    setSelectedCommunityId(preset.community_id)
    setSelectedGroupId(preset.id)
    setStep(3)
  }, [presetGroupId, groups])

  const selectedCommunity = communities.find((c) => c.id === selectedCommunityId)

  // Never offer the Recycle Bin as an onboarding target — new hires start in
  // a real working group (backend rejects it too).
  const groupsInCommunity = groups.filter(
    (g) => g.community_id === selectedCommunityId && !g.is_recycle_bin,
  )
  const selectedGroup = groups.find((g) => g.id === selectedGroupId)
  // True once a URL-named group has actually resolved to a real, usable one —
  // not merely because ?group= was present. A stale or Recycle-Bin id falls
  // back to the full wizard rather than a form with nothing behind it.
  const cameFromGroup = Boolean(presetGroupId && selectedGroup?.id === presetGroupId)
  const selectedGroupForm = leadForms.find((f) => f.group_id === selectedGroupId)

  async function handleCreateGroup() {
    const name = newGroupName.trim()
    const platformId = formPlatformId.trim()
    if (!name || !platformId || !selectedCommunityId) return
    setCreatingGroupBusy(true)
    try {
      const created = await createBrokerGroup(selectedCommunityId, name, platformId)
      toast('Broker group created')
      setSelectedGroupId(created[0].id)
      setNewGroupName('')
      setFormPlatformId('')
      setCreatingGroup(false)
      loadAll()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      const message = firstValidationMessage(err)
      // Two distinct 409s share a status: duplicate group name vs form already
      // mapped — told apart by the backend's message. Same handling as
      // BrokerGroupList's NewGroupDialog.
      if (status === 422) toast.error(message ?? 'Meta rejected this form — check the form ID')
      else if (status === 409 && message?.includes('name'))
        toast.error('A group with that name already exists in this community')
      else if (status === 409) toast.error('That form is already mapped to a group')
      else if (status === 400) toast.error("That community doesn't exist")
      else if (status === 502) toast.error('Could not reach Meta to validate this form — try again')
      else toast.error('Could not create the group')
    } finally {
      setCreatingGroupBusy(false)
    }
  }

  async function handleMapForm() {
    if (!selectedGroupId || !formPlatformId.trim()) return
    setMappingFormBusy(true)
    try {
      await createLeadForm(formPlatformId.trim(), selectedGroupId)
      toast('Form mapped to this group')
      setFormPlatformId('')
      loadAll()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      if (status === 422) toast.error(firstValidationMessage(err) ?? 'Meta rejected this form — check the form ID')
      else if (status === 409) toast.error('That form is already mapped to a group')
      else toast.error('Could not map the form')
    } finally {
      setMappingFormBusy(false)
    }
  }

  function handleGoToBrokerCreation() {
    if (!selectedGroup) return
    setStep(3)
  }

  const brokerFormCtl = useForm<BrokerForm>({ defaultValues: emptyBrokerForm() })

  async function onCreateBroker(values: BrokerForm) {
    if (!selectedGroup) return
    setCreatingBrokerBusy(true)
    try {
      const result = await createBroker(selectedGroup.id, {
        name: values.name,
        email: values.email,
        phone: values.phone,
        role: values.role,
        date_of_birth: values.dateOfBirth || null,
        address: values.address || null,
        ribo_license_number: values.riboLicenseNumber || null,
        lines_of_authority: values.linesOfAuthority,
        resident_province: values.residentProvince,
        license_expiry_date: values.licenseExpiryDate || null,
        eo_start_date: values.eoStartDate || null,
        eo_expiry_date: values.eoExpiryDate || null,
        daily_cap: Number(values.dailyCap),
        max_cap: Number(values.maxCap),
      })
      brokerFormCtl.reset(emptyBrokerForm())
      // Don't navigate away yet — show the temp password once so the admin
      // can relay it. They dismiss the reveal to leave.
      setOnboarded({ id: result.broker.id, name: values.name })
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      if (status === 409) toast.error('A broker with that email already exists')
      else if (status === 400) toast.error('That group is full or invalid')
      else if (status === 422) toast.error(firstValidationMessage(err) ?? 'Some fields are invalid')
      else toast.error('Could not onboard the broker')
    } finally {
      setCreatingBrokerBusy(false)
    }
  }

  if (onboarded && selectedGroup) {
    return (
      <AppShell section="Admin · Onboard Broker">
        {/* max-w-2xl, matching the form this replaces — the document rows
            carry a filename, a size, a date and three buttons, which max-w-md
            wrapped onto two lines. */}
        <div className="mx-auto max-w-2xl space-y-6 p-6">
          <Card>
            <CardContent className="space-y-4 pt-6">
              <div className="bg-success-muted text-success flex size-10 items-center justify-center rounded-full">
                <Check className="size-5" />
              </div>
              <div>
                <h2 className="text-lg font-semibold">{onboarded.name} onboarded</h2>
                <p className="text-muted-foreground mt-1 text-sm">
                  Added to {groupLabel(selectedGroup)}.
                </p>
              </div>

              {/* A SECOND STEP, not part of creating the record. The same
                  panel appears on the broker's own screen, so recovering a
                  forgotten password later is this identical action rather
                  than a separate feature. */}
              <GeneratePasswordPanel
                brokerId={onboarded.id}
                brokerName={onboarded.name}
              />

              {/* HERE, not on the form. A document is attached TO a broker, so
                  the broker has to exist first — putting it on the form would
                  mean holding files in memory across a submit that might fail.
                  All three are optional, so an admin with nothing to hand
                  presses Done and uploads from the broker's screen later. */}
              <div className="border-t pt-4">
                <BrokerDocumentsSection brokerId={onboarded.id} />
              </div>

              <div className="flex justify-end gap-2 pt-1">
                <Button
                  variant="outline"
                  onClick={() => {
                    setOnboarded(null)
                    setStep(3)
                  }}
                >
                  Onboard another
                </Button>
                <Button onClick={() => navigate(`/admin/broker-groups/${selectedGroup.id}`)}>
                  Done
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell section="Admin · Onboard Broker">
      <div className="mx-auto max-w-2xl space-y-6 p-6">
        <div>
          {/* Back to wherever this was opened from. Returning someone to the
              group list when they came from a group would lose their place. */}
          <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
            <Link to={cameFromGroup ? `/admin/broker-groups/${presetGroupId}` : '/admin/broker-groups'}>
              <ArrowLeft /> {cameFromGroup && selectedGroup ? groupLabel(selectedGroup) : 'Lead Management'}
            </Link>
          </Button>
          <h1 className="text-2xl font-semibold">Onboard Broker</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            {cameFromGroup && selectedGroup ? (
              <>
                Into <span className="text-foreground font-medium">{groupLabel(selectedGroup)}</span>
                {selectedCommunity ? ` · ${selectedCommunity.name}` : ''}.
              </>
            ) : (
              <>
                Every broker belongs to exactly one broker group, and every broker group belongs to
                exactly one community. Start by choosing the community.
              </>
            )}
          </p>
        </div>

        {/* The step indicator and the first two cards are the WIZARD — how an
            admin picks a community and a group. Arriving from inside a group
            has already answered both, so showing three steps with two of them
            pre-ticked describes a journey that did not happen. */}
        {!cameFromGroup && <StepIndicator current={step} />}

        <Card className={cameFromGroup ? 'hidden' : step === 1 ? '' : 'opacity-60'}>
          <CardHeader>
            <CardTitle>Step 1 — Community</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {step >= 2 && selectedCommunity ? (
              <div className="bg-accent flex items-center gap-2 rounded-lg p-3 text-sm">
                <Check className="text-success size-4" />
                <span className="font-medium">{selectedCommunity.name}</span> selected
                <Button variant="link" size="sm" className="ml-auto h-auto p-0" onClick={() => setStep(1)}>
                  Change
                </Button>
              </div>
            ) : loading ? (
              <p className="text-muted-foreground text-sm">Loading…</p>
            ) : (
              <>
                <div className="space-y-1.5">
                  <label className="text-muted-foreground text-xs font-normal">
                    Select an existing community
                  </label>
                  {communities.length === 0 ? (
                    <p className="text-muted-foreground text-sm">No communities yet.</p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {communities.map((c) => (
                        <button
                          key={c.id}
                          type="button"
                          onClick={() => setSelectedCommunityId(c.id)}
                          className={
                            'rounded-lg border px-3 py-1.5 text-sm font-medium transition ' +
                            (selectedCommunityId === c.id
                              ? 'border-primary bg-primary text-primary-foreground'
                              : 'hover:bg-muted')
                          }
                        >
                          {c.name}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {selectedCommunity && (
                  <div className="bg-accent flex items-center gap-2 rounded-lg p-3 text-sm">
                    <Check className="text-success size-4" />
                    <span className="font-medium">{selectedCommunity.name}</span> selected
                    {selectedCommunity.community_country &&
                      selectedCommunity.community_country.length > 0 && (
                        <span className="text-muted-foreground">
                          ({selectedCommunity.community_country.map((cc) => cc.country.name).join(', ')})
                        </span>
                      )}
                  </div>
                )}

                {/* Creating a community lives in the New Group dialog on Lead
                    Management now — a group cannot exist without one, so that
                    is where the need for a new community actually arises. */}
                <div className="flex justify-end pt-2">
                  <Button disabled={!selectedCommunityId} onClick={() => setStep(2)}>
                    Next: Broker Group
                  </Button>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {step >= 2 && !cameFromGroup && (
          <Card className={step === 2 ? '' : 'opacity-60'}>
            <CardHeader>
              <CardTitle>Step 2 — Broker Group</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {step === 3 && selectedGroup ? (
                <div className="bg-accent flex items-center gap-2 rounded-lg p-3 text-sm">
                  <Check className="text-success size-4" />
                  <span className="font-medium">{groupLabel(selectedGroup)}</span> selected
                  <Button variant="link" size="sm" className="ml-auto h-auto p-0" onClick={() => setStep(2)}>
                    Change
                  </Button>
                </div>
              ) : !selectedGroup ? (
                <>
                  <div className="space-y-1.5">
                    <label className="text-muted-foreground text-xs font-normal">
                      Select an existing broker group in {selectedCommunity?.name}
                    </label>
                    {groupsInCommunity.length === 0 ? (
                      <p className="text-muted-foreground text-sm">No broker groups yet in this community.</p>
                    ) : (
                      <div className="flex flex-col gap-2">
                        {groupsInCommunity.map((g) => {
                          const full = g.broker_count >= MAX_BROKERS_PER_GROUP
                          return (
                            <button
                              key={g.id}
                              type="button"
                              disabled={full}
                              onClick={() => setSelectedGroupId(g.id)}
                              className="hover:bg-muted flex items-center justify-between rounded-lg border px-3 py-2 text-left text-sm disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <span className="font-medium">{groupLabel(g)}</span>
                              <span className="text-muted-foreground text-xs">
                                {full ? 'Full' : `${g.broker_count} / ${MAX_BROKERS_PER_GROUP} brokers`}
                              </span>
                            </button>
                          )
                        })}
                      </div>
                    )}
                  </div>

                  {!creatingGroup ? (
                    <div className="border-t pt-4">
                      <Button variant="outline" onClick={() => setCreatingGroup(true)}>
                        <Plus /> Create a new broker group
                      </Button>
                    </div>
                  ) : (
                    <div className="space-y-3 border-t pt-4">
                      <p className="text-muted-foreground text-xs">
                        A group only exists once it can receive leads — paste the real Meta/TikTok
                        form ID that should route here now. Meta confirms it's real and pulls the
                        form's name before the group is created.
                      </p>
                      <div className="space-y-1.5">
                        <Label className="text-muted-foreground text-xs font-normal">
                          Group name <span className="text-error">*</span>
                        </Label>
                        <Input
                          value={newGroupName}
                          onChange={(e) => setNewGroupName(e.target.value)}
                          placeholder="e.g. GTA Auto Team"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-muted-foreground text-xs font-normal">
                          Form ID <span className="text-error">*</span>
                        </Label>
                        <Input
                          value={formPlatformId}
                          onChange={(e) => setFormPlatformId(e.target.value)}
                          placeholder="e.g. 811203456789012"
                        />
                      </div>
                      <p className="text-muted-foreground text-xs">
                        Capped at {MAX_BROKERS_PER_GROUP} brokers — create another group once this one fills up.
                      </p>
                      <div className="flex gap-2">
                        <Button
                          onClick={handleCreateGroup}
                          disabled={
                            creatingGroupBusy ||
                            !newGroupName.trim() ||
                            !formPlatformId.trim()
                          }
                        >
                          {creatingGroupBusy ? 'Validating & creating…' : 'Create group'}
                        </Button>
                        <Button variant="outline" onClick={() => setCreatingGroup(false)}>
                          Cancel
                        </Button>
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div className="bg-accent flex items-center gap-2 rounded-lg p-3 text-sm">
                    <Check className="text-success size-4" />
                    <span className="font-medium">{groupLabel(selectedGroup)}</span> selected
                    <Button
                      variant="link"
                      size="sm"
                      className="ml-auto h-auto p-0"
                      onClick={() => setSelectedGroupId('')}
                    >
                      Change
                    </Button>
                  </div>

                  <div className="space-y-2 border-t pt-4">
                    <Label className="text-muted-foreground text-xs font-normal">
                      Lead form routed to this group
                    </Label>
                    {selectedGroupForm ? (
                      <div className="bg-accent rounded-lg p-3 text-sm">
                        <span className="font-medium">{selectedGroupForm.name}</span>{' '}
                        <span className="text-muted-foreground">({selectedGroupForm.platform_form_id})</span>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        <div className="space-y-1">
                          <Label className="text-muted-foreground text-xs font-normal">
                            Form ID — copied exactly from Meta (Instant Forms Library)
                          </Label>
                          <Input
                            value={formPlatformId}
                            onChange={(e) => setFormPlatformId(e.target.value)}
                            placeholder="e.g. 811203456789012"
                          />
                        </div>
                        <p className="text-muted-foreground text-xs">
                          Meta confirms the form is real and pulls its name automatically.
                        </p>
                        <Button
                          variant="outline"
                          disabled={!formPlatformId.trim() || mappingFormBusy}
                          onClick={handleMapForm}
                        >
                          {mappingFormBusy ? 'Mapping…' : 'Map this form'}
                        </Button>
                      </div>
                    )}
                  </div>

                  {!selectedGroupForm && (
                    <div className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
                      <AlertTriangle className="text-warning mt-0.5 size-4 shrink-0" />
                      <span className="text-muted-foreground">
                        No lead form is mapped to this group yet — leads won't route here until you
                        map one. You can still add brokers now and map a form later.
                      </span>
                    </div>
                  )}

                  <div className="flex justify-end pt-2">
                    <Button onClick={handleGoToBrokerCreation}>Next: Add Broker</Button>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        )}

        {step === 3 && selectedGroup && (
          <Card>
            <CardHeader>
              <CardTitle>{cameFromGroup ? 'Broker' : 'Step 3 — Broker'}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-muted-foreground text-sm">
                Onboarding into <span className="text-foreground font-medium">{groupLabel(selectedGroup)}</span>{' '}
                ({selectedCommunity?.name}
                {selectedGroupForm ? ` · routes from ${selectedGroupForm.name}` : ''}).
              </p>

              <form onSubmit={brokerFormCtl.handleSubmit(onCreateBroker)} className="space-y-6">
                <section className="space-y-3">
                  <h3 className="text-sm font-semibold">General information</h3>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <TextField control={brokerFormCtl.control} name="name" label="Name" required />
                    <TextField control={brokerFormCtl.control} name="email" label="Email" type="email" required />
                    <TextField control={brokerFormCtl.control} name="phone" label="Phone" type="tel" required />
                    <SelectField control={brokerFormCtl.control} name="role" label="Role" items={ROLE_ITEMS} required />
                    <TextField control={brokerFormCtl.control} name="dateOfBirth" label="Date of birth" type="date" />
                    <TextField control={brokerFormCtl.control} name="address" label="Address" />
                  </div>
                </section>

                <section className="space-y-3 border-t pt-5">
                  <h3 className="text-sm font-semibold">Licensing & compliance</h3>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <TextField control={brokerFormCtl.control} name="riboLicenseNumber" label="RIBO license #" />
                    <MultiSelectField
                      control={brokerFormCtl.control}
                      name="linesOfAuthority"
                      label="Lines of authority"
                      items={LINE_OF_AUTHORITY_ITEMS}
                    />
                    <SelectField
                      control={brokerFormCtl.control}
                      name="residentProvince"
                      label="Resident province"
                      items={CANADA_PROVINCE_ITEMS}
                    />
                    <TextField control={brokerFormCtl.control} name="licenseExpiryDate" label="License expiry" type="date" />
                    <TextField control={brokerFormCtl.control} name="eoStartDate" label="E&O start" type="date" />
                    <TextField control={brokerFormCtl.control} name="eoExpiryDate" label="E&O expiry" type="date" />
                  </div>
                  <EoTermSummary
                    control={brokerFormCtl.control}
                    startName="eoStartDate"
                    endName="eoExpiryDate"
                  />
                </section>

                <section className="space-y-3 border-t pt-5">
                  <h3 className="text-sm font-semibold">Operational</h3>
                  <p className="text-muted-foreground text-xs">
                    Safe to set directly here — this broker won't start receiving leads until
                    tomorrow, so there's no mid-day cap change to protect against.
                  </p>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <TextField control={brokerFormCtl.control} name="dailyCap" label="Daily cap (max 25)" type="number" required min={0} max={25} />
                    <TextField control={brokerFormCtl.control} name="maxCap" label="Max cap — overtime (max 25)" type="number" required min={0} max={25} />
                  </div>
                </section>

                <div className="flex items-center justify-end gap-3 pt-2">
                  <Button type="submit" disabled={creatingBrokerBusy}>
                    {creatingBrokerBusy ? 'Onboarding…' : 'Onboard broker'}
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>
        )}
      </div>
    </AppShell>
  )
}
