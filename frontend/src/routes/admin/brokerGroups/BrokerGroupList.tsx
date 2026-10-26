import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import axios from 'axios'
import { ArchiveRestore, ArrowLeft, ChevronRight, Globe, Plus, Check, AlertTriangle } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { MultiSelectField, TextField } from '@/routes/leads/fields'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  getBrokerGroups,
  getCommunities,
  getCountries,
  createBrokerGroup,
  createCommunity,
  renameCommunity,
  deleteCommunity,
  validateLeadForm,
  setCommunityCountries,
  type BrokerGroup,
  type Community,
  type Country,
  type MetaFormRecord,
  firstValidationMessage,
} from '@/data/api'

// The admin management home. Real backend data — GET /broker-groups, which
// already returns broker_count and has_form_mapped per group in one call.
//
// Broker Groups are the day-to-day work level, so this screen is anchored on
// them: communities appear as section headers grouping their groups, read-only
// there. Everything that CHANGES a community — create, rename, countries,
// delete — lives behind the Communities button, so one dialog owns it rather
// than the controls being spread across section headers and other screens.
//
// Each group drills into its own detail screen (brokers + lead-form mapping).
// A group only exists once a real Meta-confirmed lead form is mapped to it
// (no group without a form — see
// services/broker_group.py::create_broker_group); there's no "empty group,
// map a form later" path anymore.

const MAX_BROKERS_PER_GROUP = 10

interface CommunitySection {
  community: Community
  groups: BrokerGroup[]
}

// Build one section per COMMUNITY (not per group) so every community shows —
// including ones with no groups yet — and each section carries the
// community's id + countries for the edit affordance. The Recycle Bin is a
// single global holding area, not a real working group, so it's excluded
// here and shown as its own pinned card instead.
function buildSections(communities: Community[], groups: BrokerGroup[]): CommunitySection[] {
  const realGroups = groups.filter((g) => !g.is_recycle_bin)
  return communities.map((community) => ({
    community,
    groups: realGroups.filter((g) => g.community_id === community.id),
  }))
}

function countryNames(c: Community): string[] {
  return (c.community_country ?? []).map((cc) => cc.country.name)
}

export default function BrokerGroupList() {
  const [groups, setGroups] = useState<BrokerGroup[]>([])
  const [communities, setCommunities] = useState<Community[]>([])
  const [countries, setCountries] = useState<Country[]>([])
  const [loading, setLoading] = useState(true)
  const [newGroupOpen, setNewGroupOpen] = useState(false)
  const [communitiesOpen, setCommunitiesOpen] = useState(false)

  async function loadAll() {
    setLoading(true)
    try {
      const [groupData, communityData, countryData] = await Promise.all([
        getBrokerGroups(),
        getCommunities(),
        getCountries(),
      ])
      setGroups(groupData)
      setCommunities(communityData)
      setCountries(countryData)
    } catch {
      toast.error('Could not reach the backend — is it running?')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadAll()
  }, [])

  const sections = buildSections(communities, groups)
  const recycleBin = groups.find((g) => g.is_recycle_bin)

  return (
    <AppShell section="Admin · Lead Management">
      <div className="mx-auto max-w-4xl space-y-6 p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <Button variant="ghost" size="sm" asChild className="text-muted-foreground -ml-2">
              <Link to="/admin">
                <ArrowLeft /> Admin
              </Link>
            </Button>
            <h1 className="text-2xl font-semibold">Lead Management</h1>
            <p className="text-muted-foreground mt-1 text-sm">
              Manage broker groups, their brokers, and the lead form routed to each. Grouped by
              community.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {!loading && recycleBin && (
              <>
                <Button
                  variant="outline"
                  size="icon"
                  asChild
                  title={`Recycle Bin — ${recycleBin.broker_count} held`}
                  className="text-muted-foreground relative"
                >
                  <Link to={`/admin/broker-groups/${recycleBin.id}`}>
                    <ArchiveRestore />
                    {recycleBin.broker_count > 0 && (
                      <span className="bg-foreground text-background ring-background absolute -top-1.5 -right-1.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] leading-none font-semibold ring-2">
                        {recycleBin.broker_count}
                      </span>
                    )}
                    <span className="sr-only">Recycle Bin</span>
                  </Link>
                </Button>
                <div className="bg-border mx-1 h-6 w-px" aria-hidden />
              </>
            )}
            <Button variant="outline" onClick={() => setCommunitiesOpen(true)}>
              <Globe /> Communities
            </Button>
            <Button onClick={() => setNewGroupOpen(true)}>
              <Plus /> New Group
            </Button>
          </div>
        </div>

        {loading ? (
          <div className="rounded-lg border bg-card p-10 text-center">
            <p className="text-muted-foreground text-sm">Loading…</p>
          </div>
        ) : sections.length === 0 ? (
          <div className="rounded-lg border bg-card p-10 text-center">
            <p className="text-muted-foreground text-sm">
              No communities yet — create one from Communities, then add a group to it.
            </p>
          </div>
        ) : (
          <div className="space-y-6">
            {sections.map((section) => {
              const cnames = countryNames(section.community)
              return (
                <section key={section.community.id} className="space-y-2">
                  <div className="flex items-center gap-2">
                    <h2 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
                      {section.community.name}
                    </h2>
                    {/* Read-only here. Editing a community lives behind the
                        Communities button, so there is one place that owns it
                        rather than an Edit link per section header. */}
                    <span className="text-muted-foreground/70 flex items-center gap-1 text-xs">
                      <Globe className="size-3" />
                      {cnames.length > 0 ? cnames.join(', ') : 'No countries set'}
                    </span>
                  </div>
                  <div className="overflow-hidden rounded-lg border bg-card">
                    {section.groups.length === 0 ? (
                      <p className="text-muted-foreground px-4 py-3 text-sm">
                        No groups in this community yet.
                      </p>
                    ) : (
                      section.groups.map((g) => (
                        <Link
                          key={g.id}
                          to={`/admin/broker-groups/${g.id}`}
                          className="hover:bg-muted flex items-center justify-between border-b px-4 py-3 last:border-0"
                        >
                          <span className="font-medium">{g.name || `Group ${g.group_number}`}</span>
                          <div className="flex items-center gap-4">
                            <FormBadge
                              formName={g.form_name}
                              formId={g.form_platform_id}
                              formCount={g.form_count}
                            />
                            <BrokerCount count={g.broker_count} />
                            <ChevronRight className="text-muted-foreground size-4" />
                          </div>
                        </Link>
                      ))
                    )}
                  </div>
                </section>
              )
            })}
          </div>
        )}
      </div>

      <NewGroupDialog
        open={newGroupOpen}
        communities={communities}
        countries={countries}
        onOpenChange={setNewGroupOpen}
        onCreated={loadAll}
      />

      <CommunitiesDialog
        open={communitiesOpen}
        communities={communities}
        countries={countries}
        onOpenChange={setCommunitiesOpen}
        onChanged={loadAll}
      />
    </AppShell>
  )
}

function NewGroupDialog({
  open,
  communities,
  countries,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  communities: Community[]
  countries: Country[]
  onOpenChange: (o: boolean) => void
  onCreated: () => void
}) {
  const [communityId, setCommunityId] = useState('')
  const [name, setName] = useState('')
  const [formPlatformId, setFormPlatformId] = useState('')
  // The two steps are gated on this: null until the form has been mapped
  // (validated against Meta). Once set, it holds Meta's own record so the real
  // form name can be shown read-only, and Step 2 (Create group) unlocks.
  const [mappedForm, setMappedForm] = useState<MetaFormRecord | null>(null)
  const [mapping, setMapping] = useState(false)
  const [creating, setCreating] = useState(false)
  // Swaps the community picker for the create-a-community form.
  const [creatingCommunity, setCreatingCommunity] = useState(false)

  const { control, handleSubmit, reset: resetCommunityForm } = useForm<{
    name: string
    countryIds: string[]
  }>({ defaultValues: { name: '', countryIds: [] } })

  /** Lifted from the Onboard Broker wizard unchanged, including the 409/400
   *  split: 409 means the name is taken, while 400 means the community WAS
   *  created but a country failed to link — a partial success the admin needs
   *  told about differently. */
  async function onCreateCommunity(values: { name: string; countryIds: string[] }) {
    const communityName = values.name.trim()
    if (!communityName) {
      toast.error('Enter a community name')
      return
    }
    try {
      const created = await createCommunity(communityName, values.countryIds)
      toast(`${communityName} community created`)
      resetCommunityForm()
      setCreatingCommunity(false)
      setCommunityId(created[0].id)
      onCreated()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      if (status === 409) {
        toast.error('That community name already exists')
      } else if (status === 400) {
        toast.error(
          firstValidationMessage(err) ??
            'Community created, but one or more countries were invalid — set them from the community page',
        )
        setCreatingCommunity(false)
        onCreated()
      } else {
        toast.error('Could not reach the backend')
      }
    }
  }

  function reset() {
    setCommunityId('')
    setName('')
    setFormPlatformId('')
    setMappedForm(null)
    setCreatingCommunity(false)
    resetCommunityForm()
  }

  function close(o: boolean) {
    if (!o) reset()
    onOpenChange(o)
  }

  // Step 1 — Map the form: validate the id against Meta (no write), then show
  // its real name read-only. Nothing is created yet.
  async function handleMapForm() {
    setMapping(true)
    try {
      const form = await validateLeadForm(formPlatformId.trim())
      setMappedForm(form)
      toast('Form confirmed with Meta')
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      const message = firstValidationMessage(err)
      if (status === 422) toast.error(message ?? 'Meta rejected this form — check the Form ID')
      else if (status === 502) toast.error('Could not reach Meta to validate this form — try again')
      else toast.error('Could not validate the form')
    } finally {
      setMapping(false)
    }
  }

  // Step 2 — Create the group: one atomic backend call that re-validates the
  // form and creates the group + mapping together (or neither). Only reachable
  // once Step 1 confirmed the form.
  async function handleCreate() {
    setCreating(true)
    try {
      await createBrokerGroup(communityId, name.trim(), formPlatformId.trim())
      toast('Group created and form mapped')
      reset()
      onOpenChange(false)
      onCreated()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      // firstValidationMessage safely handles BOTH shapes of `detail`: a plain
      // string (our HTTPExceptions) and pydantic's array of {msg, loc, ...}
      // (422). Passing the raw array to toast.error would make React try to
      // render an object and blank the page.
      const message = firstValidationMessage(err)
      if (status === 422) toast.error(message ?? 'Meta rejected this form — check the Form ID')
      else if (status === 409 && message?.includes('name')) {
        toast.error('A group with that name already exists in this community')
      } else if (status === 409) toast.error('That form is already mapped to a group')
      else if (status === 400) toast.error(message ?? "That community doesn't exist")
      else if (status === 502) toast.error('Could not reach Meta to validate this form — try again')
      else toast.error('Could not create the group')
    } finally {
      setCreating(false)
    }
  }

  const canMap = !!communityId && !!name.trim() && !!formPlatformId.trim()

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New broker group</DialogTitle>
          <DialogDescription>
            First map the real Meta/TikTok form that should route here — Meta confirms it and
            captures the form name. Then create the group. No form, no group.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label className="text-muted-foreground text-xs font-normal">
              Community <span className="text-error">*</span>
            </Label>
            {creatingCommunity ? (
              /* Same fields, same call and same error handling the Onboard
                 Broker wizard used — moved here because a group cannot be
                 created without a community, so this is where the need for
                 one actually arises. */
              <form onSubmit={handleSubmit(onCreateCommunity)} className="space-y-3">
                <TextField
                  control={control}
                  name="name"
                  label="Community name"
                  placeholder="e.g. Phoenix"
                  required
                />
                <MultiSelectField
                  control={control}
                  name="countryIds"
                  label="Countries this community represents"
                  items={countries.map((c) => ({ k: c.id, t: c.name }))}
                />
                <div className="flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setCreatingCommunity(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" size="sm">
                    Create community
                  </Button>
                </div>
              </form>
            ) : (
              <>
                {communities.length === 0 ? (
                  <p className="text-muted-foreground text-sm">
                    No communities yet — create one below.
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {communities.map((c) => (
                      <button
                        key={c.id}
                        type="button"
                        onClick={() => setCommunityId(c.id)}
                        className={
                          'rounded-lg border px-3 py-1.5 text-sm font-medium transition ' +
                          (communityId === c.id
                            ? 'border-primary bg-primary text-primary-foreground'
                            : 'hover:bg-muted')
                        }
                      >
                        {c.name}
                      </button>
                    ))}
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => setCreatingCommunity(true)}
                  className="text-muted-foreground hover:text-foreground flex items-center gap-1 pt-1 text-xs transition-colors"
                >
                  <Plus className="size-3" /> New community
                </button>
              </>
            )}
          </div>
          <div className="space-y-1.5">
            <Label className="text-muted-foreground text-xs font-normal">
              Group name <span className="text-error">*</span>
            </Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. GTA Auto Team"
            />
          </div>

          {/* Step 1 — Map the form */}
          <div className="space-y-2 border-t pt-3">
            <Label className="text-muted-foreground text-xs font-normal">
              Form ID <span className="text-error">*</span>
            </Label>
            <div className="flex gap-2">
              <Input
                value={formPlatformId}
                onChange={(e) => {
                  setFormPlatformId(e.target.value)
                  // Changing the id invalidates any prior confirmation — the
                  // admin must re-map so a stale name can't be created.
                  if (mappedForm) setMappedForm(null)
                }}
                placeholder="e.g. 811203456789012"
                disabled={!!mappedForm}
              />
              {!mappedForm && (
                <Button variant="outline" disabled={!canMap || mapping} onClick={handleMapForm}>
                  {mapping ? 'Mapping…' : 'Map form'}
                </Button>
              )}
            </div>

            {mappedForm && (
              <div className="border-success/40 bg-success/10 flex items-start gap-2 rounded-lg border p-3 text-sm">
                <Check className="text-success mt-0.5 size-4 shrink-0" />
                <div>
                  <div className="font-medium">{mappedForm.name}</div>
                  <div className="text-muted-foreground text-xs">
                    Confirmed with Meta ({mappedForm.status}) · this name is captured from Meta
                  </div>
                </div>
                <Button
                  variant="link"
                  size="sm"
                  className="text-muted-foreground ml-auto h-auto p-0"
                  onClick={() => setMappedForm(null)}
                >
                  Change
                </Button>
              </div>
            )}
          </div>
        </div>

        {/* Step 2 — Create group (only after the form is mapped) */}
        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)}>
            Cancel
          </Button>
          <Button disabled={!mappedForm || creating} onClick={handleCreate}>
            {creating ? 'Creating…' : 'Create group'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * Everything about communities in one place: see them all, create, rename,
 * set countries, delete.
 *
 * A community is only ever a container for broker groups, so it has no screen
 * of its own — this dialog sits beside New Group on the screen where its
 * groups are already listed.
 */
function CommunitiesDialog({
  open,
  communities,
  countries,
  onOpenChange,
  onChanged,
}: {
  open: boolean
  communities: Community[]
  countries: Country[]
  onOpenChange: (o: boolean) => void
  onChanged: () => void
}) {
  // Which community's row is expanded for editing, or 'new' while creating.
  const [editing, setEditing] = useState<string | null>(null)
  const [draftName, setDraftName] = useState('')
  const [draftCountries, setDraftCountries] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<Community | null>(null)

  function startEdit(c: Community) {
    setEditing(c.id)
    setDraftName(c.name)
    setDraftCountries(new Set((c.community_country ?? []).map((cc) => cc.country.id)))
  }

  function startCreate() {
    setEditing('new')
    setDraftName('')
    setDraftCountries(new Set())
  }

  function cancel() {
    setEditing(null)
    setDraftName('')
    setDraftCountries(new Set())
  }

  function toggleCountry(id: string) {
    setDraftCountries((cur) => {
      const next = new Set(cur)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function handleSave() {
    const name = draftName.trim()
    if (!name) {
      toast.error('Enter a community name')
      return
    }
    setBusy(true)
    try {
      if (editing === 'new') {
        await createCommunity(name, Array.from(draftCountries))
        toast(`${name} created`)
      } else {
        const current = communities.find((c) => c.id === editing)
        // Only send what actually changed — a rename and a country change are
        // separate endpoints, and skipping the untouched one keeps a no-op
        // edit from looking like two writes.
        if (current && current.name !== name) {
          await renameCommunity(editing!, name)
        }
        await setCommunityCountries(editing!, Array.from(draftCountries))
        toast('Community updated')
      }
      cancel()
      onChanged()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      if (status === 409) toast.error('That community name already exists')
      else if (status === 400) {
        toast.error(
          firstValidationMessage(err) ?? 'Saved, but one or more countries were invalid',
        )
        cancel()
        onChanged()
      } else toast.error('Could not save the community')
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete(c: Community) {
    setBusy(true)
    try {
      await deleteCommunity(c.id)
      toast(`${c.name} deleted`)
      setConfirmDelete(null)
      onChanged()
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : null
      // The backend refuses while any broker group still belongs to it —
      // deleting would orphan those groups.
      if (status === 409) {
        toast.error("Can't delete — this community still has groups")
      } else toast.error('Could not delete the community')
      setConfirmDelete(null)
    } finally {
      setBusy(false)
    }
  }

  const countryChips = (
    <div className="flex flex-wrap gap-1.5">
      {countries.map((c) => {
        const on = draftCountries.has(c.id)
        return (
          <button
            key={c.id}
            type="button"
            onClick={() => toggleCountry(c.id)}
            className={
              'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium transition ' +
              (on ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-muted')
            }
          >
            {on && <Check className="size-3" />}
            {c.name}
          </button>
        )
      })}
    </div>
  )

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          cancel()
          setConfirmDelete(null)
        }
        onOpenChange(o)
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Communities</DialogTitle>
          <DialogDescription>
            A community groups the broker groups that serve a region. A community can only be
            deleted once it has no groups left.
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-96 space-y-2 overflow-y-auto">
          {communities.length === 0 && editing !== 'new' && (
            <p className="text-muted-foreground py-4 text-center text-sm">
              No communities yet.
            </p>
          )}

          {communities.map((c) =>
            editing === c.id ? (
              <div key={c.id} className="space-y-3 rounded-lg border p-3">
                <Input
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  placeholder="Community name"
                />
                {countryChips}
                <div className="flex justify-end gap-2">
                  <Button variant="outline" size="sm" onClick={cancel} disabled={busy}>
                    Cancel
                  </Button>
                  <Button size="sm" onClick={handleSave} disabled={busy}>
                    {busy ? 'Saving…' : 'Save'}
                  </Button>
                </div>
              </div>
            ) : (
              <div
                key={c.id}
                className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{c.name}</div>
                  <div className="text-muted-foreground truncate text-xs">
                    {(c.community_country ?? []).length > 0
                      ? (c.community_country ?? []).map((cc) => cc.country.name).join(', ')
                      : 'No countries set'}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button variant="ghost" size="sm" onClick={() => startEdit(c)} disabled={busy}>
                    Edit
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground hover:text-error"
                    onClick={() => setConfirmDelete(c)}
                    disabled={busy}
                  >
                    Delete
                  </Button>
                </div>
              </div>
            ),
          )}

          {editing === 'new' && (
            <div className="space-y-3 rounded-lg border p-3">
              <Input
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                placeholder="Community name"
                autoFocus
              />
              {countryChips}
              <div className="flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={cancel} disabled={busy}>
                  Cancel
                </Button>
                <Button size="sm" onClick={handleSave} disabled={busy}>
                  {busy ? 'Creating…' : 'Create'}
                </Button>
              </div>
            </div>
          )}
        </div>

        {editing === null && (
          <Button variant="outline" onClick={startCreate}>
            <Plus /> New community
          </Button>
        )}

        {/* Confirmed because a community is infrastructure — groups, and every
            lead routed through them, hang off it. */}
        <Dialog open={!!confirmDelete} onOpenChange={(o) => !o && setConfirmDelete(null)}>
          <DialogContent className="sm:max-w-sm">
            <DialogHeader>
              <DialogTitle>Delete {confirmDelete?.name}?</DialogTitle>
              <DialogDescription>
                This cannot be undone. A community with broker groups still in it cannot be
                deleted.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirmDelete(null)} disabled={busy}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={() => confirmDelete && handleDelete(confirmDelete)}
                disabled={busy}
              >
                {busy ? 'Deleting…' : 'Delete'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </DialogContent>
    </Dialog>
  )
}

function BrokerCount({ count }: { count: number }) {
  const full = count >= MAX_BROKERS_PER_GROUP
  return (
    <span className={'text-xs font-medium ' + (full ? 'text-warning' : 'text-muted-foreground')}>
      {full ? 'Full · ' : ''}
      {count} / {MAX_BROKERS_PER_GROUP} brokers
    </span>
  )
}

function FormBadge({
  formName,
  formId,
  formCount,
}: {
  formName: string | null
  formId: string | null
  formCount: number
}) {
  // Show the Meta-confirmed form name (the real thing routing leads here), with
  // the form id on hover for support/debugging. Falls back to a warning only for
  // legacy groups created before the "no form, no group" rule.
  //
  // A group can have several forms mapped to it, so anything beyond the first
  // is counted rather than dropped — "+2" is the difference between a list
  // that is incomplete and one that says so.
  const extra = formCount - 1
  return formName ? (
    <span
      className="text-success inline-flex max-w-[14rem] items-center gap-1 text-xs font-medium"
      title={formId ? `Form ID: ${formId}` : undefined}
    >
      <Check className="size-3.5 shrink-0" />
      <span className="truncate">{formName}</span>
      {extra > 0 && <span className="text-muted-foreground shrink-0">+{extra}</span>}
    </span>
  ) : (
    <span className="text-warning inline-flex items-center gap-1 text-xs font-medium">
      <AlertTriangle className="size-3.5" /> No form
    </span>
  )
}
